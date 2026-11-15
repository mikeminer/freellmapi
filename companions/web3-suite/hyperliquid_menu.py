"""Shared native actions: terminal arrows, Remote and desktop buttons."""
import json,re,uuid
from decimal import Decimal
import questions

ACTIONS=['Saldo e posizioni','Miglior segnale long/short','Analizza ed esegui con TP/SL automatici','Monitor H24 con ingressi automatici','Controlla TP/SL delle posizioni','Ricevute operazioni','Firma locale e account','Torna alla chat']

def choose(agent,question,options):
    ident='hyperliquid_menu_'+uuid.uuid4().hex
    agent.messages.append({'role':'assistant','content':None,'tool_calls':[{'id':ident,'type':'function','function':{'name':'ask_user','arguments':json.dumps({'question':question,'options':options},ensure_ascii=False)}}]});agent.save()
    try:answer=questions.ask(question,options)
    except KeyboardInterrupt:
        agent.messages.append({'role':'tool','tool_call_id':ident,'content':json.dumps({'question':question,'cancelled':True})});agent.save();raise
    agent.messages.append({'role':'tool','tool_call_id':ident,'content':json.dumps(answer,ensure_ascii=False)});agent.save()
    return None if answer.get('cancelled') else answer.get('answer')

def read(agent,name,args):
    import reliable_runtime
    ident='hyperliquid_menu_'+uuid.uuid4().hex
    agent.messages.append({'role':'assistant','content':None,'tool_calls':[{'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]});agent.save()
    try:result=reliable_runtime.execute(agent,ident,name,args,agent.tool_definitions())
    except Exception as error:result=json.dumps({'ok':False,'message':str(error)[:700]})
    agent.messages.append({'role':'tool','tool_call_id':ident,'content':result});agent.save()
    return json.loads(result)

def capital(text):
    match=re.fullmatch(r'\s*(\d+(?:[.,]\d+)?)\s*(%|USDC)\s*',text or '',re.I)
    if not match:raise ValueError('Scegli una percentuale oppure scrivi un importo seguito da USDC.')
    value=Decimal(match[1].replace(',','.'));unit=match[2].upper()
    if not value.is_finite() or value<=0 or unit=='%' and value>100:raise ValueError('Percentuale positiva fino a 100 oppure importo USDC positivo.')
    number=format(value.normalize(),'f')
    return number+'% del collaterale USDC disponibile come margine' if unit=='%' else number+' USDC di margine'

def run(agent,home,output):
    import advanced_tools,hyperliquid_tools as h
    if getattr(agent,'depth',0):raise ValueError('Menu disponibile nel coordinatore principale.')
    agent.enabled_tools.update(advanced_tools.GROUPS['hyperliquid']);agent.command_outcome='completed'
    agent.messages.append({'role':'user','content':'/hyperliquid'});agent.save()
    try:
        p=h.profile(home);output('Hyperliquid · '+p['name']+' · '+p['network'])
    except h.HLProblem:output('Hyperliquid · collega un account da Firma locale e account.')
    action=choose(agent,'Hyperliquid: cosa vuoi fare?',ACTIONS)
    if action in (None,'Torna alla chat'):return
    if action=='Firma locale e account':output(json.dumps(h.panel(agent,home),ensure_ascii=False));return
    if action=='Ricevute operazioni':
        agent.messages.append({'role':'user','content':'Mostra le ricevute storiche Hyperliquid. Solo lettura: non inviare ordini.'});agent.save()
        output(json.dumps(read(agent,'hyperliquid_operations',{}),ensure_ascii=False,indent=2));return
    if action in ('Saldo e posizioni','Controlla TP/SL delle posizioni'):
        agent.messages.append({'role':'user','content':'Hyperliquid: mostra saldo e posizioni attuali. Solo analisi: non inviare ordini.'});agent.save()
        value=read(agent,'hyperliquid_read',{'resource':'risk','limit':20})
        if action=='Saldo e posizioni':
            from market_routing import account_state,render_state
            state=account_state([{'tool':'hyperliquid_read','arguments':{'resource':'risk'},'result':value}])
            output(render_state(state) if state else json.dumps(value,ensure_ascii=False,indent=2));return
        if value.get('ok') is False:output(json.dumps(value,ensure_ascii=False));agent.command_outcome='incomplete';return
        positions=(value.get('data') or {}).get('positions') or []
        if not positions:output('Nessuna posizione aperta nella lettura corrente: nessun TP/SL da modificare.');return
        coins=sorted({p.get('coin') or p.get('position',{}).get('coin') for p in positions}-{None})
        if len(coins)>8:output('Posizioni aperte: '+', '.join(coins)+'. La risposta libera permette di indicarne una.')
        coin=choose(agent,'Quale posizione vuoi controllare?',coins[:8] if len(coins)>1 else [coins[0],'Torna alla chat'])
        if coin not in coins:return
        prompt='Controlla i TP/SL realmente aperti della posizione '+coin+' su Hyperliquid. Solo analisi: non inviare ordini e non modificare leva, posizioni o protezioni. Distingui stato attuale e ricevute storiche.'
    elif action=='Miglior segnale long/short':
        prompt='Trova la migliore opportunità long o short tra i mercati perpetual Hyperliquid: scan corrente, analisi dei candidati, entry, TP/SL e scenario contrario, oppure HOLD se nessun setup è giustificato. Solo analisi: non inviare ordini e non modificare leva o posizioni. Scegli autonomamente il coin; non chiedermi di scegliere il mercato. Nessun TP garantito.'
    elif action in ('Analizza ed esegui con TP/SL automatici','Monitor H24 con ingressi automatici'):
        if agent.mode=='plan':output('La sessione è in pianificazione: usa il selettore Permessi per passare alla modalità operativa prima di un ingresso.');agent.command_outcome='incomplete';return
        answer=choose(agent,'Quanto capitale vuoi impiegare come margine per questa operazione?', ['10%','25%','50%','90%','100%'])
        if answer is None:return
        try:budget=capital(answer)
        except ValueError as error:output(str(error)+' Nessun ordine richiesto.');agent.command_outcome='incomplete';return
        mode='Trova il miglior segnale long o short su Hyperliquid ed esegui un solo nuovo ingresso' if action.startswith('Analizza') else 'Trova un setup condizionato su Hyperliquid e arma un monitor H24 che attenda i livelli futuri prima di un solo nuovo ingresso'
        prompt=(mode+'. Usa '+budget+'. Scegli autonomamente coin, direzione, leva valida per l’exchange e quantità BASE compatibile con saldo, costi e margine. '
                'Calcola TP e SL dall’analisi corrente e impostali nello stesso gruppo dell’entry. Nessun TP garantito; HOLD se i dati non giustificano l’ingresso. '
                'Le scelte del menu sono confermate: non richiedere di nuovo quota di capitale o coin. Non reinviare vecchi ordini e non cambiare profilo. '
                'Le opzioni order_kind, slippage_bps e bracket_slippage_bps sono argomenti esterni al JSON plan.')
    else:prompt='Hyperliquid: '+action
    agent.turn(prompt);agent.command_outcome=agent.turn_outcome
