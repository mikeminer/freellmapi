"""Execution completion is established by this turn's native journal, never prose."""
import math,re,time
from pathlib import Path

EXECUTION_VERB=r'\b(?:esegui(?:lo|la|li|le)?|invia(?:lo|la|li|le)?|inoltra(?:lo|la|li|le)?|piazza(?:lo|la|li|le)?|manda(?:lo|la|li|le)?|submit|execute|place|compra(?:lo|la|li|le)?|vendi(?:lo|la|li|le)?|trada\w*|apri(?:lo|la|li|le)?|open)\b'

def execution_verb(text):
    if re.search(EXECUTION_VERB,text,re.I):return True
    # "a trade", "formulami un trade", "buy or sell?" describe a proposal.
    # English imperative trade/buy/sell still authorizes an explicit request.
    verb=r'(?:trade|buy|sell)\b'
    imperative=r'(?:^\s*(?:hyperliquid\s*:\s*)?(?:please\s+)?|\b(?:and|then|e|poi)\s+|\b(?:can you|could you|i want you to|i need you to)\s+)'+verb
    if re.search(imperative,text,re.I):return True
    return bool(re.search(r'\b(?:fai|effettua)\s+(?:un|il|questo)\s+trade\b',text,re.I))

def delegated_signal(prompt):
    signal=bool(re.search(r'\b(?:signal\w*|segnal\w*|strategi\w*)\b',prompt,re.I))
    selection=bool(re.search(r'\b(?:trova\w*|cerca\w*|scegli\w*|seleziona\w*|miglior\w*|best|find)\b',prompt,re.I)) and bool(re.search(r'\b(?:trade|trading|opportunit\w*)\b',prompt,re.I))
    return (signal or selection) and not bool(re.search(r'\b(?:compra|vendi|buy|sell)\b',prompt,re.I))

def requirement(prompt,request):
    if not request or request.get('venue')=='binance':return None
    text=prompt.casefold()
    if re.search(r'\b(?:solo analisi|sola analisi|analysis only|read.only|non inviare ordini|senza inviare ordini|do not (?:send|place|execute) orders)\b',text):return None
    conditional=bool(re.search(r'\b(?:h24|24/7|breakout|pullback|retest|monitor|condizionat\w*|attendi|aspetta)\b|\bquando\b.*\b(?:prezzo|price|livell\w*)\b',text))
    coding=bool(re.search(r'\b(?:implementa|integra|correggi|debug)\b',text))
    if conditional and not coding and re.search(r'\b(?:trada\w*|trad\w*|entry|ingress\w*|attiva|arma|monitora|watch|apri|compra|vendi|buy|sell)\b',text):return 'watch'
    if execution_verb(text):
        return 'leverage' if re.search(r'\b(?:leva|leverage)\b',text) and not re.search(r'\b(?:ordine|ordini|order|buy|sell|compra|vendi|posizione|position|entry|ingress\w*|long|short|trada\w*|trade|segnal\w*|signal\w*|strategi\w*|nozionale|notional|size|qty|quantit[aà])\b',text) else 'order'
    if re.search(r'\b(?:annulla|cancella|cancel)\b',text):return 'cancel'
    if re.search(r'\b(?:imposta|porta|cambia|riduci|set|change|reduce)\b',text) and re.search(r'\b(?:leva|leverage)\b',text):return 'leverage'
    if re.search(r'\b(?:prepara|anteprima|preview|prepare)\b',text) and re.search(r'\b(?:ordine|ordini|order)\b',text):return 'preview'
    return None

def assess(agent,request,evidence,home):
    required=request.get('execution_action') if request else None
    if not required:return None
    if required=='watch':return assess_watch(agent,request,evidence,home)
    if required=='order':
        held=assess_hold(agent,evidence,home)
        if held:return held
    import hyperliquid_tools as h
    candidates=[];observations=[];unprotected=[]
    from hyperliquid_protection import new_risk,completion
    expected_tool='hyperliquid_preview' if required=='preview' else 'hyperliquid_submit'
    path=Path(home)/'hyperliquid/operations.db';start=getattr(agent,'financial_turn_started_at',None)
    if path.exists():
        with h.database(home) as db:
            for item in evidence:
                if item['tool'] not in ('hyperliquid_submit','hyperliquid_preview','hyperliquid_operations','hyperliquid_execute_plan'):continue
                value=item['result']
                if not isinstance(value,dict):continue
                embedded=value.get('receipt') or value.get('preview') or {}
                ident=embedded.get('id') if isinstance(embedded,dict) else None
                ident=ident or value.get('id') or item['arguments'].get('id')
                if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{24}',ident):continue
                row=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
                if not row:continue
                body=row['body'];observations.append({'id':ident,'state':row['state'],'coin':body.get('coin'),'profile':row['profile']})
                matching_tool=item['tool']==expected_tool or (required=='order' and item['tool']=='hyperliquid_execute_plan')
                if not matching_tool or value.get('reused') or value.get('ok') is False or value.get('denied') or value.get('blocked'):continue
                if required!='preview' and value.get('sent') is False:continue
                action='order' if required=='preview' else required
                if body.get('action')!=action or (request.get('coin') and body.get('coin')!=request['coin']):continue
                expected=getattr(agent,'current_account_state',None) or {}
                if expected.get('profile') and row['profile']!=expected['profile']:continue
                p=h.profile(home,row['profile'])
                if body.get('scope')!=h.fingerprint(p):continue
                stamp=row['created'] if required=='preview' else row['updated']
                if not isinstance(start,(int,float)) or not math.isfinite(start) or not start<=stamp<=time.time()+5:continue
                if required=='order' and new_risk(body) and row['state'] in ('accepted','filled','partial','uncertain','sending'):
                    if not completion(body,row.get('response')):
                        unprotected.append(row);continue
                valid=('prepared',) if required=='preview' else ('accepted','filled') if required=='order' else ('acknowledged',)
                if row['state'] not in valid:continue
                candidates.append(row)
    if unprotected:
        row=unprotected[-1]
        return {'verified':False,'halt':True,'action':required,'code':'protection_not_verified','id':row['id'],'receipts':observations[-5:],
                'protection':(row.get('response') or {}).get('protection'),
                'last_error':{'code':'protection_not_verified','message':'Entry inviata o esito incerto, ma TP/SL completi non verificati. Riconcilia gli ID esistenti in sola lettura; non inviare un nuovo ingresso.'}}
    if candidates:
        row=candidates[-1];b=row['body'];return {'verified':True,'action':required,'id':row['id'],'state':row['state'],'coin':b.get('coin'),'profile':row['profile'],
            'bracket':b.get('bracket') if new_risk(b) else None,'protection':(row.get('response') or {}).get('protection')}
    failures=[v['result'] for v in evidence if v['tool'] in ('hyperliquid_preview','hyperliquid_submit','hyperliquid_operations','hyperliquid_execute_plan') and isinstance(v['result'],dict) and v['result'].get('ok') is False]
    missing_plan=None
    if required=='order' and not failures and not observations:
        analyzed=any(v['tool']=='hyperliquid_analyze' and isinstance(v['result'],dict) and v['result'].get('data_ready') for v in evidence)
        missing_plan={'code':'model_plan_not_produced' if analyzed else 'market_analysis_not_completed',
                      'message':('Il modello si è fermato senza produrre un piano operativo né un HOLD strutturato.' if analyzed else 'Il modello si è fermato dopo le letture, senza completare l’analisi di un candidato né produrre un piano o un HOLD. Nessun ordine inviato.')}
    return {'verified':False,'action':required,'code':'execution_not_verified','receipts':observations[-5:],
            'last_error':{k:failures[-1].get(k) for k in ('code','message')} if failures else missing_plan}

def render(result):
    if result.get('decision')=='hold':
        from secret_output import redact
        return 'HOLD · '+redact(result['reason'])+'\nNessun ordine inviato. È una decisione del modello, non una garanzia sull’andamento del mercato.'
    if result['verified']:
        if result['action']=='watch':
            return ('Monitor '+result['mode'].upper()+' verificato nel registro locale: '+str(result.get('coin'))+' · '+result['id']+' · '+result['state']+
                    '.\nTP: '+str(result.get('take_profit'))+' · SL: '+str(result.get('stop_loss'))+'. '+
                    ('Il setup attende la condizione; non è un ordine già eseguito. ' if result['state']=='watching' else 'Il trigger è in verifica/esecuzione; consulta la ricevuta e lo stato corrente delle protezioni. ')+
                    'Nessun rinnovo automatico dei limiti o delle autorizzazioni. /trading monitor mostra lo stato effettivo del servizio.')
        prefix='Anteprima locale verificata' if result['action']=='preview' else 'Invio verificato nel registro locale'
        bracket=result.get('bracket');protection=result.get('protection') or {}
        detail=''
        if bracket:
            detail='\nTP: '+str(bracket.get('take_profit'))+' · SL: '+str(bracket.get('stop_loss'))
            if protection.get('state')=='active':detail+=' · entrambi aperti alla verifica ms '+str(protection.get('checked_ms'))+'.'
            elif protection.get('state')=='pending_entry':detail+=' · accettati nel gruppo, differiti fino al riempimento completo dell’entry; non ancora protezioni attive.'
            elif protection.get('state')=='exit_filled':detail+=' · una delle uscite risulta eseguita; rileggi la posizione attuale.'
        return prefix+': '+str(result.get('coin') or result['action'])+' · ricevuta '+result['id']+' · stato '+result['state']+'.'+detail+'\nLo stato della ricevuta non prova da solo una posizione attualmente aperta.'
    rows=result.get('receipts',[])
    detail='; '.join(r['id']+'='+r['state'] for r in rows) or 'nessuna ricevuta di esecuzione verificata per questa richiesta'
    from secret_output import redact
    error=result.get('last_error')
    reason=('\nUltimo errore nativo: '+str(error.get('code'))+' · '+redact(str(error.get('message')))[:800]) if error else ''
    ending=('\nInvio fermato: verifica entry, TP e SL tramite le ricevute esistenti. Nessun nuovo ingresso o retry automatico.' if result.get('halt') else '\nUn’analisi o una lettura del saldo non completa l’invio richiesto.')
    return 'Richiesta di '+('anteprima' if result['action']=='preview' else 'esecuzione')+' incompleta: '+detail+'.'+reason+ending+' Contesto e ricevute conservati; nessun vecchio ordine viene reinviato dal controllo.'


def assess_hold(agent,evidence,home):
    import hashlib,json,hyperliquid_tools as h
    if not (Path(home)/'hyperliquid/operations.db').exists():return None
    for item in reversed(evidence):
        value=item.get('result')
        if item.get('tool')!='hyperliquid_execute_plan' or not isinstance(value,dict) or value.get('decision')!='hold':continue
        ident=value.get('decision_id')
        if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{64}',ident):continue
        with h.database(home) as db:
            if not db.execute("SELECT name FROM sqlite_master WHERE name='chat_executions'").fetchone():return None
            row=db.execute("SELECT result FROM chat_executions WHERE id=? AND state='held' AND operation_id IS NULL",(ident,)).fetchone()
        if not row:continue
        stored=json.loads(row[0]);p=h.profile(home,stored['profile'])
        if stored.get('scope')!=h.fingerprint(p):continue
        request_id=getattr(agent,'financial_request_id',None)
        if getattr(agent,'hyperliquid_controller',None):
            with h.database(home) as db:grant=h.current_grant(db,p['name'])
            request_id='cycle:'+str((grant or {}).get('pending'))
        expected=hashlib.sha256((str(Path(agent.session).resolve())+':'+str(request_id)+':'+h.fingerprint(p)).encode()).hexdigest()
        if ident!=expected:continue
        prompt=next((m['content'] for m in reversed(agent.messages) if m.get('role')=='user' and isinstance(m.get('content'),str)),'')
        delegated=bool(getattr(agent,'hyperliquid_controller',None)) or delegated_signal(prompt)
        return {'verified':delegated,'halt':True,'action':'hold','decision':'hold','reason':stored['reason'],'id':ident,'order_executed':False}
    return None


def assess_watch(agent,request,evidence,home):
    from trading_engine import Store,fingerprint
    import trading_service as service
    from hyperliquid_protection import completion
    config=service.load(home);path=service.folder(home)/(config['mode']+'.sqlite')
    start=getattr(agent,'financial_turn_started_at',None)
    if path.exists() and isinstance(start,(int,float)):
        with Store(path).db() as db:
            for item in reversed(evidence):
                if item['tool']!='hyperliquid_monitor' or item['arguments'].get('action')!='watch':continue
                result=item['result'];ident=result.get('id') if isinstance(result,dict) else None
                if not isinstance(ident,str) or not re.fullmatch(r'[a-f0-9]{32}',ident) or result.get('ok') is False:continue
                row=db.execute('SELECT * FROM entry_watches WHERE id=?',(ident,)).fetchone()
                if not row or row['created']<start or row['state'] not in ('watching','triggering','validated','prepared','dispatching','effect_observed','done'):continue
                body=__import__('json').loads(row['body']);watch=body.get('watch') or {};plan=watch.get('plan') or {}
                if body.get('policy_hash')!=fingerprint(config) or (request.get('coin') and request['coin']!=plan.get('coin')):continue
                receipt=(body.get('receipt') or {}).get('receipt') or {}
                if row['state']=='done' and config['mode']=='live' and not completion(receipt.get('body',{}),receipt.get('response')):continue
                return {'verified':True,'action':'watch','id':ident,'coin':plan.get('coin'),'state':row['state'],'mode':config['mode'],'take_profit':plan.get('take_profit'),'stop_loss':plan.get('stop_loss')}
    return {'verified':False,'action':'watch','code':'watch_not_verified','receipts':[],'last_error':{'code':'watch_not_verified','message':'Nessun setup condizionato corrente verificato nel registro del monitor. Leggi hyperliquid_monitor status; non ricreare intenti già registrati.'}}
