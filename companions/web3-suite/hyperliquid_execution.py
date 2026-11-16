"""One typed chat plan -> native preview/review/OMS/receipt, without model hops.

No exchange writes outside the existing signer. A durable request binding prevents
changed plans, new tool IDs, model handoffs or crashes from replacing an intent.
"""
import hashlib,json,os,re,time,uuid
from decimal import Decimal,ROUND_DOWN
from pathlib import Path
from trade_plan import TradePlan,PlanError

def decode_plan(value):
    if not isinstance(value,str) or len(value)>12000:raise PlanError('plan_json_string_required')
    decoded=json.loads(value)
    if isinstance(decoded,str) and len(decoded)<=12000:decoded=json.loads(decoded)
    if isinstance(decoded,dict) and set(decoded)=={'schema','version','plan'}:
        if decoded['schema']!='freellmapi.trade-plan' or type(decoded['version']) is not int or decoded['version']!=1:raise PlanError('unsupported_plan_version')
        decoded=decoded['plan']
    return decoded


def normalize_execution_args(args):
    """Move explicit execution options out of plan; never invent financial values."""
    args=dict(args);decoded=decode_plan(args.get('plan'));repairs=[]
    if isinstance(decoded,dict):
        decoded=dict(decoded)
        for key in ('order_kind','slippage_bps','bracket_slippage_bps'):
            if key not in decoded:continue
            if key in args and (args[key]!=decoded[key] or type(args[key]) is not type(decoded[key])):
                raise PlanError('conflicting_execution_options',field=key,conflicting_paths=[key,'plan.'+key],
                                recovery='I due valori espliciti sono diversi. Correggi il conflitto senza cambiare piano, importo o intenti.')
            args.setdefault(key,decoded[key]);del decoded[key];repairs.append('plan.'+key+' -> '+key)
        if repairs:args['plan']=json.dumps(decoded,ensure_ascii=False)
    return args,repairs


def parse_plan(value):
    """Normalize unambiguous wire representations; ENTER fields stay strict."""
    decoded=decode_plan(value)
    if isinstance(decoded,dict) and isinstance(decoded.get('decision'),str):
        decoded={**decoded,'decision':decoded['decision'].strip().lower()}
        allowed={'decision','reason','coin','side','size','entry','take_profit','stop_loss','leverage','trigger'}
        if decoded['decision']=='hold' and not set(decoded)-allowed:
            # A HOLD with optional order hints remains strictly a NO-ORDER.
            decoded={k:decoded[k] for k in ('decision','reason') if k in decoded}
    return TradePlan.parse(decoded)


def preflight(home,name=None):
    import hyperliquid_tools as h
    p=h.profile(home,name)
    with h.database(home) as db:
        rows=[h.receipt(r) for r in db.execute("SELECT * FROM actions WHERE state IN ('sending','authorized','uncertain','partial')")]
    unresolved=[{'id':r['id'],'state':r['state'],'coin':r['body'].get('coin')} for r in rows
                if r['body'].get('network')==p['network'] and str(r['body'].get('account','')).lower()==p['account'].lower()]
    blockers=[]
    if unresolved: blockers.append({'code':'outcome_unknown','message':'Ricevute precedenti da riconciliare: nessun nuovo ingresso sullo stesso master/rete.','operations':unresolved})
    if not h.secret_path(home,p['name']).is_file():blockers.append({'code':'signer_missing','message':'Firma locale non collegata per il profilo selezionato.'})
    from hyperliquid_authority import configured,current
    if configured(p) and not (current(home,p) or {}).get('active'):blockers.append({'code':'key_authority_inactive','message':'Collegamento della firma revocato o scope cambiato.'})
    return {'profile':p['name'],'network':p['network'],'scope':h.fingerprint(p),'ready':not blockers,
            'blockers':blockers,'sent':False,'authority_changed':False,
            'instruction':'Controllo locale preliminare, non autorizzazione né verifica RPC. Se bloccato, mostra subito la causa e gli ID; non analizzare o creare altri ordini.'}


def bind_request(agent,retry=False,confirmation=None):
    """Stable across compaction/retry/resume; independent of the routed model."""
    from work_ledger import load,save
    data=load(agent.session)
    active=data.get('financial_request') or {}
    if not retry or not active.get('id'):
        text=next((m['content'] for m in reversed(agent.messages) if m.get('role')=='user' and isinstance(m.get('content'),str)),'')
        active={'id':uuid.uuid4().hex,'created':time.time(),'request':text[:2000]}
        if confirmation:
            active.update(request=confirmation['request_text'],confirmation_of=confirmation['confirmation_of'],
                          user_confirmation=text,confirmed_proposal=confirmation['confirmed_proposal'],
                          confirmed_profile=confirmation['profile'],confirmed_scope=confirmation['scope'])
        data['financial_request']=active;save(agent.session,data)
    agent.financial_request_id=active['id']
    return active['id']


def existing_result(home,old,canonical):
    import hyperliquid_tools as h
    saved=json.loads(old['result']) if old.get('result') else None
    if old['state']=='held' and saved:return {**saved,'reused':True,'resent':False}
    if old['proposal']!=canonical:
        if old['state']=='blocked' and not old['operation_id'] and saved and saved.get('sent') is False:
            return {**saved,'reused':True,'terminal':True,'resent':False,
                    'instruction':'Nessuna anteprima o ricevuta di invio associata: il piano precedente fallì nella validazione. Mostra la causa originale; non cercare un ordine inesistente o cambiare implicitamente la leva.'}
        h.problem('execution_plan_bound','Una richiesta ha già un piano operativo associato: non sostituirlo cambiando quantità/TP/SL. Consulta la ricevuta esistente.',operation_id=old['operation_id'])
    if saved:return {**saved,'reused':True,'resent':False}
    if old['operation_id']:
        return {**h.operations(home,old['operation_id']),'reused':True,'terminal':True,
                'instruction':'Ripresa in sola lettura del piano già preso in carico. Nessun reinvio: consulta la ricevuta, anche se la chiamata originale non ha restituito il risultato.'}
    h.problem('execution_interrupted','Piano già preso in carico senza risultato durevole. Consulta operations: nessun nuovo intento o reinvio automatico.')


def execute(agent,home,args,output=lambda text:None):
    import hyperliquid_tools as h
    if os.getenv('FREELLMAPI_READ_ONLY')=='1':h.problem('read_only_signing_forbidden','Processo di sola lettura: nessuna esecuzione.')
    if getattr(agent,'mode','ask')=='plan' or getattr(agent,'depth',0):h.problem('coordinator_only','Esecuzione disponibile solo nel coordinatore operativo.')
    from work_ledger import load as load_ledger
    pending=load_ledger(agent.session).get('financial_request') or {}
    if pending.get('id')==getattr(agent,'financial_request_id',None) and pending.get('proposal_pending'):
        h.problem('proposal_confirmation_required','La proposta attende una scelta esplicita: usa i pulsanti, senza inviare il piano precedente.',sent=False)
    if hasattr(agent,'current_market_request') and not getattr(agent,'hyperliquid_controller',None):
        from financial_completion import requirement
        request=getattr(agent,'current_market_request',None) or {}
        text=next((m['content'] for m in reversed(agent.messages) if m.get('role')=='user' and isinstance(m.get('content'),str)),'')
        if (request.get('execution_action') or requirement(text,{'venue':'hyperliquid'}))!='order':
            h.problem('execution_not_requested','La richiesta corrente non chiede un ingresso immediato: nessuna firma. Analisi, scelta e monitor non autorizzano un ordine immediato.')
    try:
        args,wire_repairs=normalize_execution_args(args)
        plan=parse_plan(args.get('plan'))
    except (ValueError,TypeError) as exc:
        details=getattr(exc,'details',{})
        message=str(exc)+(' · campi non ammessi: '+', '.join(details['unknown_fields']) if details.get('unknown_fields') else '')
        h.problem('invalid_trade_plan',message,**details,plan_contract={'container':'plan is a JSON string',
            'enter_fields':['decision','reason','coin','side','size','entry','take_profit','stop_loss','leverage'],
            'execution_options_location':'order_kind, slippage_bps and bracket_slippage_bps are sibling arguments OUTSIDE plan',
            'size_unit':'BASE asset decimal string, not margin USDC','side_values':['buy','sell']},
            correction='Correggi solo i campi indicati, mantenendo coin, budget, direzione e richiesta corrente. Nessun invio è avvenuto.')
    if plan.decision not in ('enter','hold') or plan.trigger is not None:
        h.problem('entry_plan_required','Questo strumento esegue solo un ingresso immediato completo. Per livelli futuri usa hyperliquid_monitor; HOLD non invia nulla.')
    from financial_focus import validate_plan
    validate_plan(agent,plan)
    kind=args.get('order_kind');slippage=args.get('slippage_bps');bracket_slippage=args.get('bracket_slippage_bps')
    if plan.decision=='enter':
        if kind not in ('market','limit'):h.problem('order_fields_invalid','order_kind richiesto: market o limit.')
        if type(bracket_slippage) is not int or not 1<=bracket_slippage<=1000:h.problem('bracket_slippage','bracket_slippage_bps richiesto: intero 1–1000.')
        if kind=='market' and (type(slippage) is not int or not 1<=slippage<=1000):h.problem('slippage_required','slippage_bps richiesto per ingresso market: intero 1–1000.')
    p=h.profile(home,args.get('profile'))
    from hyperliquid_authority import configured as key_only_mode
    key_only=key_only_mode(p)
    if plan.decision=='enter':h.requested_coin(agent,{'action':'order','coin':plan.coin})
    request=getattr(agent,'financial_request_id',None)
    if getattr(agent,'hyperliquid_controller',None):
        if key_only and getattr(agent,'key_only_cycle',None):request='cycle:'+agent.key_only_cycle
        else:
            with h.database(home) as db:grant=h.current_grant(db,p['name'])
            if not grant or not grant.get('pending'):h.problem('cycle_missing','Ciclo locale non attivo.')
            request='cycle:'+grant['pending']
    elif not request:request=bind_request(agent,retry=True)
    key=hashlib.sha256((str(Path(agent.session).resolve())+':'+request+':'+h.fingerprint(p)).encode()).hexdigest()
    proposal={'plan':plan.to_dict(),'order_kind':kind,'slippage_bps':slippage,'bracket_slippage_bps':bracket_slippage}
    canonical=json.dumps(proposal,sort_keys=True)
    from work_ledger import load as load_ledger
    active=load_ledger(agent.session).get('financial_request') or {}
    if active.get('id')==request and active.get('confirmation_of') and canonical!=json.dumps(active.get('confirmed_proposal'),sort_keys=True):
        h.problem('confirmation_plan_changed','La conferma riguarda il piano mai inviato già mostrato: mantieni gli stessi parametri, oppure chiedi una nuova richiesta esplicita.',
                  expected_proposal=active.get('confirmed_proposal'),sent=False)
    with h.database(home) as db:
        db.execute('CREATE TABLE IF NOT EXISTS chat_executions(id TEXT PRIMARY KEY,proposal TEXT,operation_id TEXT,state TEXT,result TEXT,created REAL)')
        db.commit();old=db.execute('SELECT * FROM chat_executions WHERE id=?',(key,)).fetchone()
    if old:return existing_result(home,dict(old),canonical)
    if plan.decision=='hold':
        # This persists a model decision, NOT market truth or an execution.
        held={'decision':'hold','reason':plan.reason,'decision_id':key,'profile':p['name'],'scope':h.fingerprint(p),
              'classification':'model_decision_no_order','sent':False,'terminal':True,'resent':False}
        with h.database(home) as db:
            inserted=db.execute("INSERT OR IGNORE INTO chat_executions VALUES(?,?,NULL,'held',?,?)",(key,canonical,json.dumps(held),time.time())).rowcount;db.commit()
            old=dict(db.execute('SELECT * FROM chat_executions WHERE id=?',(key,)).fetchone())
        return held if inserted else existing_result(home,old,canonical)
    phases=[];started=time.monotonic();ident=None;submitting=False;claimed=False;quantity_adjustment=None
    def phase(name):
        phases.append({'phase':name,'elapsed_ms':round((time.monotonic()-started)*1000)})
        output('Hyperliquid · '+name+' · '+str(round(time.monotonic()-started,1))+'s')
    def store(result,state):
        result={**result,'phases':phases,'duration_ms':round((time.monotonic()-started)*1000),'terminal':True}
        if wire_repairs:result['wire_repairs']=wire_repairs
        if quantity_adjustment:result['quantity_adjustment']=quantity_adjustment
        if claimed:
            with h.database(home) as db:
                db.execute('UPDATE chat_executions SET result=?,state=? WHERE id=?',(json.dumps(result),state,key));db.commit()
        if not getattr(agent,'hyperliquid_controller',None):
            from work_ledger import load,save
            data=load(agent.session);active=data.get('financial_request') or {}
            if active.get('id')==request:
                active.update(plan=plan.envelope(),operation_id=ident,state=state)
                data['financial_request']=active;save(agent.session,data)
        return result
    try:
        phase('controllo preliminare')
        ready=preflight(home,p['name'])
        if not ready['ready']:
            first=ready['blockers'][0];h.problem(first['code'],first['message'],blockers=ready['blockers'])
        if key_only:
            from hyperliquid_authority import require_current,request_context
            permission=require_current(home,p)
            request_context(agent,home,p,{'action':'order','execution_generation':permission['generation']})
        phase('verifica leva e dati')
        actual=h.checked_leverage(h.info(p,{'type':'activeAssetData','user':p['target'],'coin':plan.coin}),True)
        if not key_only and actual['value']!=plan.leverage:h.problem('leverage_action_required','Leva effettiva '+str(actual['value'])+'× diversa dal piano '+str(plan.leverage)+'×. Nessun ingresso o modifica implicita della leva.',observed=actual)
        # Validation failures above cannot have reached preview/signing. Bind
        # only after those checks, so an invalid plan cannot create a fake
        # pending order. The durable atomic claim still precedes any preview.
        with h.database(home) as db:
            inserted=db.execute("INSERT OR IGNORE INTO chat_executions VALUES(?,?,NULL,'claimed',NULL,?)",(key,canonical,time.time())).rowcount;db.commit()
            old=dict(db.execute('SELECT * FROM chat_executions WHERE id=?',(key,)).fetchone())
        if not inserted:return existing_result(home,old,canonical)
        claimed=True
        if key_only:
            from hyperliquid_authority import leverage_once
            agent.key_only_plan={'request_id':request}
            def record_leverage(row):
                nonlocal ident
                ident=row['id']
                with h.database(home) as db:
                    db.execute("UPDATE chat_executions SET operation_id=?,state='leverage_prepared' WHERE id=?",(ident,key));db.commit()
            leverage_proof=leverage_once(agent,home,p,plan.coin,plan.leverage,record_leverage)
            phase('leva Hyperliquid verificata: '+str(leverage_proof['observed']['value'])+'x')
        analysis=getattr(agent,'hyperliquid_analysis',{}).get((p['name'],plan.coin)) or {}
        if analysis.get('scope')!=h.fingerprint(p) or not 0<=time.time()*1000-analysis.get('as_of_ms',0)<=120000:
            analysis=h.dispatch(agent,'hyperliquid_analyze',{'profile':p['name'],'coin':plan.coin},home)
        if not analysis.get('data_ready'):h.problem('market_analysis_unavailable','Dati analitici incompleti: HOLD senza invio.',analysis_errors=analysis.get('errors'))
        phase('preparazione entry + TP + SL')
        from financial_completion import delegated_signal
        text=next((m['content'] for m in reversed(agent.messages) if m.get('role')=='user' and isinstance(m.get('content'),str)),'')
        exact_quantity=bool(re.search(r'(?i)\b(?:size|qty|quantity|quantit[aà])\s*[:=]?\s*\d|\b\d+(?:[.,]\d+)?\s*'+re.escape(plan.coin)+r'\b',text))
        if (getattr(agent,'hyperliquid_controller',None) or delegated_signal(text)) and not exact_quantity:
            meta,_=h.market(p,plan.coin);decimals=meta.get('szDecimals')
            if type(decimals) is not int or not 0<=decimals<=18:h.problem('size_precision','Precisione della quantità non verificata.')
            size=Decimal(plan.size);rounded=size.quantize(Decimal(10)**-decimals,rounding=ROUND_DOWN)
            if rounded<=0:h.problem('quantity_below_precision','Quantità proposta troppo piccola per il mercato: nessun ordine. Rivaluta il piano o HOLD.')
            if rounded!=size:
                quantity_adjustment={'requested':plan.size,'effective':h.textnum(rounded),'szDecimals':decimals,'method':'floor_before_native_review','increased':False}
                plan=TradePlan.parse({**plan.to_dict(),'size':h.textnum(rounded)})
                output('Hyperliquid · quantità arrotondata per difetto prima della revisione: '+quantity_adjustment['requested']+' → '+plan.size+' '+plan.coin)
        order={'profile':p['name'],'action':'order','coin':plan.coin,'side':plan.side,'size':plan.size,
               'order_kind':kind,'take_profit':plan.take_profit,'stop_loss':plan.stop_loss,'bracket_slippage_bps':bracket_slippage}
        if kind=='market':order.update(slippage_bps=slippage,tighten_to_bracket=True,entry_reference=plan.entry)
        else:order['price']=plan.entry
        previous=getattr(agent,'hyperliquid_execution_context',None)
        agent.hyperliquid_execution_context={'id':key,'leverage':plan.leverage}
        try:row=h.preview(agent,home,order)['preview']
        finally:agent.hyperliquid_execution_context=previous
        ident=row['id']
        with h.database(home) as db:
            db.execute("UPDATE chat_executions SET operation_id=?,state='prepared' WHERE id=?",(ident,key));db.commit()
        phase('controlli finali / autorizzazione locale / invio unico')
        submitting=True
        result=h.submit(agent,home,ident)
        native=result.get('receipt') or {};response=native.get('response') or {}
        if native.get('state') in ('blocked','rejected'):
            result={**result,'ok':False,'code':response.get('code') or 'exchange_rejected',
                    'message':response.get('message') or response.get('reason') or 'Operazione rifiutata: consulta la ricevuta esistente.',
                    'sent':False if native['state']=='blocked' else True}
        elif result.get('denied'):
            result={**result,'ok':False,'code':'user_declined','message':'Invio annullato nella conferma locale. Nessun nuovo tentativo.'}
        phase('ricevuta e protezioni')
        return store({**result,'operation_id':ident},'finished')
    except h.HLProblem as exc:
        result={'ok':False,'code':exc.code,'message':str(exc),'sent':False if not submitting else None,
                'operation_id':ident,'resent':False,'binding_created':claimed,**exc.details}
        if ident:
            with h.database(home) as db:row=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
            result['receipt']=row
        return store(result,'blocked' if claimed else 'validation_failed')
