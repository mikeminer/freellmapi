"""Session/coin-scoped user constraints. These are context, never authority."""
import json,re
from decimal import Decimal

SYMBOLS=('BTC','ETH','SOL','HYPE','PUMP','ZEC','DOGE','AVAX','SUI','XRP','BNB')


def extract(text,coin):
    facts={};text=text or ''
    leverage=re.search(r'(?i)\b(?:leva|leverage)\s*(?:di|a|=|:)?\s*(\d{1,3})\b|\b(\d{1,3})\s*[x×](?=\W|$)',text)
    if leverage:facts['leverage']=int(next(x for x in leverage.groups() if x))
    share=re.search(r'(?i)(\d+(?:[.,]\d+)?)\s*%\s*(?:(?:del|di|of)\s+(?:the\s+)?)?(?:capitale|capital|saldo|fondi|funds|collaterale|collateral|balance|margine|margin)',text)
    if share:
        amount=Decimal(share[1].replace(',','.'))
        if 0<amount<=100:facts['capital_percent']=format(amount.normalize(),'f')
    cash=re.search(r'(?i)\b(\d+(?:[.,]\d+)?)\s*USDC\b',text)
    if cash:facts['budget_usdc']=format(Decimal(cash[1].replace(',','.')).normalize(),'f')
    base=re.search(r'(?i)\b(\d+(?:[.,]\d+)?)\s*'+re.escape(coin)+r'\b',text)
    if base:facts['base_size']=format(Decimal(base[1].replace(',','.')).normalize(),'f')
    if re.search(r'(?i)\b(?:come\s+margine|as\s+margin|margine da impegnare)\b',text):facts['budget_basis']='margin'
    elif re.search(r'(?i)\b(?:nozionale|notional|valore totale della posizione)\b',text):facts['budget_basis']='notional'
    return facts


def bind(agent,prompt,request):
    if not request or request.get('venue')!='hyperliquid':return None
    coin=request.get('coin')
    if not coin:return None
    if re.search(r'(?i)\b(?:confronta|compare|versus|vs)\b',prompt) and sum(bool(re.search(r'(?i)\b'+s+r'\b',prompt)) for s in SYMBOLS)>1:return None
    from work_ledger import load,save
    data=load(agent.session);old=data.get('financial_focus') or {};facts={}
    if old.get('venue')=='hyperliquid' and old.get('coin')==coin:facts.update(old.get('constraints') or {})
    # Recover real same-coin choices from history after restart; an explicit
    # different coin/topic ends inheritance. Do not use assistant suggestions.
    rows=[m['content'] for m in agent.messages if m.get('role')=='user' and isinstance(m.get('content'),str)]
    relevant=[]
    for text in reversed(rows):
        if text.startswith('Conversation summary (data, not new instructions):'):continue
        mentioned={s for s in SYMBOLS if re.search(r'(?i)\b'+s+r'\b',text)}
        if mentioned and coin not in mentioned:break
        if mentioned or text==prompt or len(text.split())<=3:relevant.append(text)
        elif relevant:break
    for text in list(reversed(relevant))+[prompt]:
        given=extract(text,coin)
        if any(k in given for k in ('capital_percent','budget_usdc','base_size')):
            for k in ('capital_percent','budget_usdc','base_size'):
                if k not in given:facts.pop(k,None)
        facts.update(given)
    focus={'venue':'hyperliquid','coin':coin,'constraints':facts,'source':'explicit_user_turns','financial_authority':False}
    request['financial_constraints']=facts;data['financial_focus']=focus;save(agent.session,data)
    return focus


def instruction(focus):
    if not focus:return ''
    return ('CURRENT USER FINANCIAL FOCUS (runtime data, not execution permission): '+json.dumps(focus,ensure_ascii=False)+
            '. This coin overrides historical assistant suggestions/questions. Preserve the supplied leverage and allocation. '
            'Do not ask again for known leverage, capital percentage or an already supplied base size. Derive base quantity from fresh native data and the explicit allocation; do not ask the user to invent it. '
            'A percentage is not a base-asset size. If margin versus total notional is genuinely unspecified and needed, ask only that distinction, once, for the CURRENT coin. Never infer or enlarge the budget. No guaranteed TP.')


def question_result(agent,question,options=()):
    focus=getattr(agent,'financial_focus',None)
    if not focus:return None
    coin=focus['coin'];facts=focus['constraints']
    other=[s for s in SYMBOLS if s!=coin and re.search(r'(?i)\b'+s+r'\b',question or '')]
    if other:return {'ok':False,'code':'question_coin_mismatch','sent':False,'requested_coin':coin,'received_coins':other,
                     'instruction':instruction(focus)+' Do not display the stale question. Continue the current task.'}
    q=(question or '').casefold()
    # Clarifying an unknown budget basis is different from reasking its value.
    basis=q+' '+' '.join(x for x in options if isinstance(x,str))
    if re.search(r'margin|margine',basis) and re.search(r'nozional|notional',basis) and not facts.get('budget_basis'):return None
    leverage=bool(re.search(r'\b(?:leva|leverage)\b',q))
    size=bool(re.search(r'quantit|dimension|\bsize\b|\bquanto\b|\bquanti\b|capitale|capital|amount|budget',q))
    known_lev=leverage and 'leverage' in facts
    known_size=size and any(k in facts for k in ('capital_percent','budget_usdc','base_size'))
    if not known_lev and not known_size:return None
    return {'question':question,'answer':f"{coin}: "+json.dumps(facts,ensure_ascii=False),'cancelled':False,'reused':True,
            'decision_key':'financial_constraints:'+coin,'instruction':instruction(focus)}


def response_issue(message,focus):
    if not focus:return None
    text=message.get('content') or ''
    if text and not re.search(r'(?i)\b'+re.escape(focus['coin'])+r'\b',text):
        foreign=any(re.search(r'(?i)\b'+s+r'\b',text) for s in SYMBOLS if s!=focus['coin'])
        if foreign and re.search(r'(?i)\b(?:long|short|entry|TP|SL|trade|analisi|segnal\w*)\b',text):return 'response_coin_mismatch'
    for call in message.get('tool_calls') or []:
        f=call.get('function',{});name=f.get('name')
        try:a=json.loads(f.get('arguments') or '{}')
        except (ValueError,TypeError):continue
        if not isinstance(a,dict):continue
        if name=='ask_user':
            fake=type('Focus',(),{'financial_focus':focus})()
            known=question_result(fake,a.get('question',''),a.get('options') or [])
            if known:return known.get('code') or 'financial_choice_already_provided'
        elif name in ('hyperliquid_execute_plan','hyperliquid_propose_plan','hyperliquid_preview','hyperliquid_analyze'):
            try:
                from hyperliquid_execution import decode_plan
                plan=decode_plan(a['plan']) if isinstance(a.get('plan'),str) else a
            except (ValueError,TypeError):continue
            if not isinstance(plan,dict):continue
            if plan.get('coin') and plan['coin']!=focus['coin']:return 'request_coin_mismatch'
            expected=focus['constraints'].get('leverage')
            if expected and plan.get('leverage') is not None and plan['leverage']!=expected:return 'request_leverage_mismatch'
    if '?' in text or re.search(r'(?i)\b(?:indica|inserisci|specifica|provide|enter)\b',text):
        if re.search(r'(?i)quantit|dimension|size|leva|leverage|capitale|capital',text):
            fake=type('Focus',(),{'financial_focus':focus})()
            known=question_result(fake,text)
            if known:return known.get('code') or 'financial_choice_already_provided'
    return None


def validate_plan(agent,plan):
    import hyperliquid_tools as h
    focus=getattr(agent,'financial_focus',None)
    if not focus or plan.decision=='hold':return
    if plan.coin!=focus['coin']:h.problem('request_coin_mismatch','Il piano deve riguardare '+focus['coin']+', non '+str(plan.coin)+'.',requested_coin=focus['coin'])
    facts=focus['constraints']
    if 'leverage' in facts and plan.leverage!=facts['leverage']:h.problem('request_leverage_mismatch','La leva esplicita richiesta è '+str(facts['leverage'])+'x; non cambiarla.',requested_leverage=facts['leverage'])
    if 'base_size' in facts and Decimal(plan.size)!=Decimal(facts['base_size']):h.problem('request_size_mismatch','La quantità base esplicita non può essere cambiata.',requested_size=facts['base_size'])
