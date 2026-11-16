"""Deterministic sizing from explicit allocation; never generates authority."""
import hashlib,json,time
from dataclasses import replace
from decimal import Decimal,ROUND_DOWN

BASIS_OPTIONS=['Come margine della posizione','Come nozionale totale della posizione','Annulla']

def size_plan(agent,home,profile,plan):
    import hyperliquid_tools as h
    from work_ledger import load,save
    focus=getattr(agent,'financial_focus',None) or {}
    facts=focus.get('constraints',{})
    if plan.decision=='hold' or not facts.get('capital_percent'):return plan,None
    basis=facts.get('budget_basis')
    if basis not in ('margin','notional'):
        if getattr(agent,'depth',0) or getattr(agent,'hyperliquid_controller',None):
            h.problem('allocation_basis_required','Specificare margine o nozionale per la percentuale di capitale; nessun invio.')
        from hyperliquid_menu import choose
        label=f"Come devo interpretare il {facts['capital_percent']}% del capitale disponibile per {plan.coin}?"
        answer=choose(agent,label,BASIS_OPTIONS)
        if answer not in BASIS_OPTIONS[:2]:h.problem('allocation_cancelled','Scelta della base annullata o non definita; nessun ordine inviato.')
        basis='margin' if answer==BASIS_OPTIONS[0] else 'notional'
        facts['budget_basis']=basis
        focus.setdefault('choice_sources',{})['budget_basis']={'question':label,'answer':answer,'kind':'native_user_selection'}
        data=load(agent.session);data['financial_focus']=focus;save(agent.session,data)
    # One immutable calculation per request/revision/entry. Re-reading after a
    # confirmed click must never create a different intent or larger position.
    data=load(agent.session);request=data.get('financial_request') or {}
    key=hashlib.sha256(json.dumps({'request':request.get('id'),'revision':request.get('proposal_revision',0),
        'scope':h.fingerprint(profile),'coin':plan.coin,'entry':plan.entry,'leverage':plan.leverage,
        'percent':facts['capital_percent'],'basis':basis},sort_keys=True).encode()).hexdigest()
    cached=(request.get('allocation') or {})
    if cached.get('key')==key:return replace(plan,size=cached['base_size']),cached
    state=getattr(agent,'current_account_state',None)
    if not state or state.get('profile')!=profile['name'] or time.time()*1000-(state.get('retrieved_ms') or 0)>30000:
        from market_routing import account_state
        value=h.read(home,{'profile':profile['name'],'resource':'risk','coin':plan.coin})
        state=account_state([{'tool':'hyperliquid_read','arguments':{'resource':'risk'},'result':value}])
    if not state or state.get('network')!=profile['network']:h.problem('allocation_snapshot_required','Manca il capitale disponibile verificato per questo profilo; nessun dimensionamento inventato.')
    available=Decimal(str(state['available_usdc']));percent=Decimal(facts['capital_percent'])
    if not available.is_finite() or available<=0:h.problem('allocation_unavailable','Il capitale disponibile verificato non permette il dimensionamento richiesto.')
    budget=available*percent/100
    notional=budget*plan.leverage if basis=='margin' else budget
    meta,_=h.market(profile,plan.coin);precision=meta.get('szDecimals')
    if type(precision) is not int or not 0<=precision<=12:h.problem('allocation_precision_required','Precisione della quantità non verificata.')
    size=(notional/Decimal(plan.entry)).quantize(Decimal(1).scaleb(-precision),rounding=ROUND_DOWN)
    if size<=0:h.problem('allocation_too_small','La quota richiesta è inferiore alla precisione dell’asset; nessun aumento automatico.')
    if facts.get('base_size') and size!=Decimal(facts['base_size']):h.problem('allocation_conflicting_constraints','La quantità base esplicita e la quota percentuale non coincidono; nessuna sostituzione automatica.')
    result={'key':key,'percent':facts['capital_percent'],'basis':basis,'available_usdc':str(available),
        'allocated_usdc':str(budget),'target_notional_usdc':str(notional),'base_size':format(size,'f'),
        'size_decimals':precision,'retrieved_ms':state['retrieved_ms'],'source':'native_available_capital_and_asset_precision'}
    if request.get('id'):
        request['allocation']=result;data['financial_request']=request;save(agent.session,data)
    return replace(plan,size=result['base_size']),result

def label(allocation):
    if not allocation:return ''
    basis='margine' if allocation['basis']=='margin' else 'nozionale totale'
    return f"Allocazione: {allocation['percent']}% del disponibile · {basis}: {Decimal(allocation['allocated_usdc']):.4f} USDC\n"
