"""Shared typed proposal -> actual keyboard/Remote choice -> one native send.

An offered/confirmed/final journal prevents restart or model handoff from
replaying a click. No keys or exchange writes before the explicit choice.
"""
import hashlib,json,re,time

OPTIONS=['Conferma invio','Aggiorna analisi','Annulla']


def asks_confirmation(message):
    text=message.get('content') or ''
    concrete=any(c.get('function',{}).get('name') in ('hyperliquid_execute_plan','hyperliquid_propose_plan') for c in message.get('tool_calls',[])) or re.search(r'(?i)\b(?:entry|TP|SL|take.profit|stop.loss)\b',text)
    return bool(concrete and '?' in text and re.search(r'(?i)\b(?:esegu[oei]|eseguire|invi[oa]|inviare|conferm\w*|proced[oaie]\w*|execute|proceed|submit)\b[^?]{0,90}\?',text))


def propose(agent,home,args):
    import hyperliquid_tools as h,hyperliquid_execution as x
    from work_ledger import load,save
    if getattr(agent,'depth',0) or getattr(agent,'hyperliquid_controller',None):h.problem('coordinator_only','La proposta interattiva è disponibile nella chat, non nei cicli H24.')
    try:args,_=x.normalize_execution_args(args);plan=x.parse_plan(args.get('plan'))
    except (ValueError,TypeError) as exc:h.problem('invalid_trade_plan',str(exc),**getattr(exc,'details',{}))
    if plan.decision!='enter' or plan.trigger is not None:h.problem('entry_plan_required','Presenta un piano immediato completo; per livelli futuri usa il monitor.')
    if args.get('order_kind') not in ('market','limit'):h.problem('order_fields_invalid','Specificare market o limit.')
    if type(args.get('bracket_slippage_bps')) is not int or not 1<=args['bracket_slippage_bps']<=1000:h.problem('bracket_slippage','Slippage TP/SL richiesto.')
    if args['order_kind']=='market' and (type(args.get('slippage_bps')) is not int or not 1<=args['slippage_bps']<=1000):h.problem('slippage_required','Slippage ingresso richiesto.')
    p=h.profile(home,args.get('profile'));scope=h.fingerprint(p)
    proposal={'plan':plan.to_dict(),'order_kind':args['order_kind'],'slippage_bps':args.get('slippage_bps'),'bracket_slippage_bps':args['bracket_slippage_bps']}
    source=next((m['content'] for m in reversed(agent.messages) if m.get('role')=='user' and isinstance(m.get('content'),str)),'')
    if getattr(agent,'mode','ask')=='plan' or re.search(r'(?i)\b(?:solo analisi|sola analisi|analysis only|non inviare ordini|senza inviare ordini|do not (?:send|place|execute) orders)\b',source):
        return {'proposal':proposal,'profile':p['name'],'sent':False,'choice':'read_only','terminal':True}
    request=getattr(agent,'financial_request_id',None) or x.bind_request(agent,retry=getattr(agent,'financial_turn_retry',False))
    canonical=json.dumps(proposal,sort_keys=True)
    active=load(agent.session).get('financial_request') or {}
    revision=active.get('proposal_revision',0)
    key=hashlib.sha256((str(agent.session.resolve())+':'+request+':'+scope+':'+canonical+':'+str(revision)).encode()).hexdigest()
    with h.database(home) as db:
        db.execute('CREATE TABLE IF NOT EXISTS chat_proposals(id TEXT PRIMARY KEY,request_id TEXT,proposal TEXT,state TEXT,result TEXT,created REAL)')
        row=db.execute('SELECT * FROM chat_proposals WHERE id=?',(key,)).fetchone()
        if row:
            if row['result']:return {**json.loads(row['result']),'reused':True,'terminal':True}
            if row['state']=='confirmed':
                return {'ok':False,'code':'proposal_confirmation_interrupted','sent':None,'proposal_id':key,'terminal':True,
                        'message':'Conferma già registrata prima di un’interruzione: consulta le ricevute esistenti; nessun reinvio.'}
        else:db.execute("INSERT INTO chat_proposals VALUES(?,?,?,'offered',NULL,?)",(key,request,canonical,time.time()));db.commit()
    from hyperliquid_menu import choose
    data=load(agent.session);active=data.get('financial_request') or {}
    if active.get('id')!=request:h.problem('proposal_context_changed','Richiesta cambiata: nessuna scelta o invio.')
    active['proposal_pending']=key;save(agent.session,data)
    label=(f"Proposta {p['network']} · {p['name']} · {plan.coin} {'LONG' if plan.side=='buy' else 'SHORT'}\n"
           f"Quantità: {plan.size} {plan.coin} · leva: {plan.leverage}x · {args['order_kind']}\n"
           f"Entry: {plan.entry} · TP: {plan.take_profit} · SL: {plan.stop_loss}\n"
           f"Slippage ingresso: {args.get('slippage_bps','n/a')} bps · TP/SL: {args['bracket_slippage_bps']} bps\nCome vuoi procedere?")
    answer=choose(agent,label,OPTIONS)
    if answer==OPTIONS[0]:
        # Mark the click BEFORE execution. If execution crashes, a repeated
        # proposal is read-only even when no result message reached the phone.
        with h.database(home) as db:
            changed=db.execute("UPDATE chat_proposals SET state='confirmed' WHERE id=? AND state='offered'",(key,)).rowcount;db.commit()
        if not changed:h.problem('proposal_already_confirmed','Conferma già presa in carico; nessun reinvio.')
        confirmation={'confirmation_of':'proposal:'+key,'confirmed_proposal':proposal,'profile':p['name'],'scope':scope}
        data=load(agent.session);active=data.get('financial_request') or {}
        if active.get('id')!=request:h.problem('proposal_context_changed','Richiesta cambiata durante la scelta: nessun invio.')
        active.update(confirmation_of=confirmation['confirmation_of'],confirmed_proposal=proposal,
                      confirmed_profile=p['name'],confirmed_scope=scope,user_confirmation=answer)
        active.pop('proposal_pending',None)
        save(agent.session,data)
        current=getattr(agent,'current_market_request',None)
        if current is None:current={};agent.current_market_request=current
        current.update(confirmation,venue='hyperliquid',coin=plan.coin,execution_action='order',trading=True)
        from freellmapi import output
        result=x.execute(agent,home,{**args,'profile':p['name']},output)
    elif answer==OPTIONS[1]:
        data=load(agent.session);active=data.get('financial_request') or {}
        active['proposal_revision']=revision+1;save(agent.session,data)
        getattr(agent,'hyperliquid_analysis',{}).pop((p['name'],plan.coin),None)
        from freellmapi import output
        output('Hyperliquid · aggiornamento analisi · sola lettura')
        refreshed=h.dispatch(agent,'hyperliquid_analyze',{'profile':p['name'],'coin':plan.coin},home)
        result={'choice':'refresh','sent':False,'terminal':False,'proposal_id':key,
                'analysis':refreshed,
                'instruction':'L’analisi aggiornata è inclusa in questo risultato: riusala senza altre letture identiche, poi presenta il piano aggiornato con hyperliquid_propose_plan. Non inviare il piano precedente; se i dati non sono disponibili dichiara il blocco senza ripetere il ciclo.'}
    elif answer is None or answer==OPTIONS[2]:result={'choice':'cancelled','sent':False,'terminal':True,'proposal_id':key}
    else:result={'choice':'custom','answer':answer,'sent':False,'terminal':False,'proposal_id':key,
                 'instruction':'Risposta aperta: aggiorna la proposta con queste indicazioni. Nessuna autorizzazione di invio.'}
    if result.get('choice') in ('refresh','custom'):
        current=getattr(agent,'current_market_request',None)
        if current is not None:current.update(execution_action=None,trading=False)
    with h.database(home) as db:
        db.execute("UPDATE chat_proposals SET state='finished',result=? WHERE id=?",(json.dumps(result),key));db.commit()
    return result
