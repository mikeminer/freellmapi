"""Bounded, native READ prerequisites. Never submits or changes a proposal."""
import json


def halt(agent,evidence):
    from market_routing import account_state,render_state
    observed=account_state(evidence)
    failures=[r['result'] for r in evidence if r['tool'] in ('hyperliquid_preview','hyperliquid_submit') and isinstance(r['result'],dict) and r['result'].get('ok') is False]
    text=(render_state(observed)+'\n' if observed else '')
    if failures:text+='Ultimo errore: '+str(failures[-1].get('code'))+' · '+str(failures[-1].get('message'))+'\n'
    text+='Fase finanziaria sospesa: i recuperi limitati in sola lettura non hanno prodotto un piano valido. Contesto e ricevute conservati. Nessun invio o modifica automatica dei limiti dal recupero.'
    agent.messages.append({'role':'assistant','content':text});agent.save();agent.turn_outcome='incomplete'
    return text


def pending(agent,evidence):
    guard=getattr(agent,'loop_guard',None);needed=getattr(guard,'financial_recovery',None)
    if not needed:return None
    state=getattr(agent,'financial_read_recovery',None)
    if state is None:state={'rounds':0};agent.financial_read_recovery=state
    if not all(row.get('name') in ('hyperliquid_read','hyperliquid_analyze','hyperliquid_status','hyperliquid_operations') for row in needed):return None
    # Two repeated capacity reads must not starve a DIFFERENT analyst prerequisite.
    analysis=any(row.get('name')=='hyperliquid_analyze' for row in needed)
    extra_analysis=analysis and not state.get('analysis_refreshed') and state['rounds']==2
    if state['rounds']>=2 and not extra_analysis:
        return halt(agent,evidence)
    if analysis:state['analysis_refreshed']=True
    state['rounds']+=1
    from checkpoint_recovery import native_call
    from market_routing import account_state
    for row in list(needed):
        args=row.get('arguments',{})
        result=native_call(agent,row['name'],args)
        guard.observe(row['name'],args,result)
        try:value=json.loads(result)
        except (ValueError,TypeError):value={'ok':False,'message':result[:500]}
        evidence.append({'tool':row['name'],'arguments':args,'result':value})
    agent.current_account_state=account_state(evidence)
    if guard.financial_recovery and state['rounds']>=2:return halt(agent,evidence)
    return None
