"""Runtime-owned memory recovery; never relies on provider tool_choice support."""
import hashlib
import json
import re
import time
import uuid


def begin(agent,active=True):
    agent.checkpoint_recovery={'active':active,'failures':0,'failure_stage':-1,'inspection_epoch':0,'inspections':0,'suspended':False,'inspection':None,'saved':{},'duplicates':0}


def native_call(agent,name,args):
    """Journal a native recovery action with an ordinary tool result, once."""
    import reliable_runtime
    import terminal_ui as ui
    ident='memory_recovery_'+uuid.uuid4().hex
    agent.messages.append({'role':'assistant','content':None,'tool_calls':[
        {'id':ident,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]});agent.save()
    ui.action(name,args,getattr(agent,'depth',0));started=time.monotonic()
    try:result=reliable_runtime.execute(agent,ident,name,args,agent.tool_definitions())
    except Exception as exc:result=json.dumps({'ok':False,'code':'memory_recovery_failed','message':str(exc)[:500]})
    agent.messages.append({'role':'tool','tool_call_id':ident,'content':result});agent.save()
    ui.result(name,result,time.monotonic()-started)
    return result


def inspect_pending(agent):
    state=getattr(agent,'checkpoint_recovery',None)
    if not state or not state['active'] or state['suspended'] or not getattr(agent,'context_recovery_pending',False):return
    if state['inspections']>=2:
        state['suspended']=True;agent.context_recovery_pending=False;return
    state['inspections']+=1
    result=native_call(agent,'context_inspect',{})
    try:value=json.loads(result)
    except (ValueError,TypeError):value={}
    if 'recent_evidence' not in value:
        state['suspended']=True;agent.context_recovery_pending=False


def inspected(agent,value):
    state=getattr(agent,'checkpoint_recovery',None)
    if state is not None:state['inspection']=value;state['inspection_epoch']+=1


def sources(agent,args):
    """Resolve timestamp aliases only by exact, unique, inspected native results.

    Unknown references are never replaced by an arbitrary 'latest' evidence ID.
    """
    state=getattr(agent,'checkpoint_recovery',None)
    if state and not state['active']:return args,{}
    inspection=(state or {}).get('inspection') or {}
    available={e['id'] for e in inspection.get('recent_evidence',[]) if e.get('kind')=='observed'}
    if not isinstance(args,dict) or not isinstance(args.get('sources'),list):return args,{}
    if any(not isinstance(ref,str) for ref in args['sources']):return args,{}
    calls={c['id']:c.get('function',{}) for m in agent.messages for c in m.get('tool_calls',[])}
    aliases={}
    for m in agent.messages:
        if m.get('role')!='tool' or calls.get(m.get('tool_call_id'),{}).get('name')!='hyperliquid_read':continue
        ident=hashlib.sha256((str(agent.session)+json.dumps(m,sort_keys=True,ensure_ascii=False)).encode()).hexdigest()[:20]
        if ident not in available:continue
        try:value=json.loads(m['content'])
        except (ValueError,TypeError):continue
        if not isinstance(value,dict) or value.get('ok') is False:continue
        timestamp=value.get('retrieved_ms')
        if type(timestamp) is int:aliases.setdefault(str(timestamp),set()).add(ident)
    fixed=[];changes={}
    for ref in args['sources']:
        matches=aliases.get(ref,set()) if isinstance(ref,str) else set()
        if isinstance(ref,str) and re.fullmatch(r'\d{13}',ref) and len(matches)==1:
            replacement=next(iter(matches));fixed.append(replacement);changes[ref]=replacement
        else:fixed.append(ref)
    return dict(args,sources=list(dict.fromkeys(fixed))) if changes else args,changes


def failed(agent):
    state=getattr(agent,'checkpoint_recovery',None)
    if state is None or not state['active']:return
    # A batch of repeated calls gets one recovery opportunity, rather than
    # exhausting every retry before the runtime can perform its inspection.
    if state['failure_stage']==state['inspection_epoch']:return
    state['failure_stage']=state['inspection_epoch']
    state['failures']+=1
    if state['failures']>=3:
        state['suspended']=True;agent.context_recovery_pending=False


def finish_suspended(agent,request,evidence):
    """Optional consolidation cannot hold a completed read-only request hostage."""
    from market_routing import account_state,render_state
    state=account_state(evidence) if request and request.get('kind')=='account' else None
    text=(render_state(state)+'\nFonte: '+str(state['source'])+'\n' if state else '')
    text+='Ulteriori checkpoint sospesi dopo tentativi ripetuti. Conversazione ed evidenze conservate; nessuna memoria inventata.'
    agent.messages.append({'role':'assistant','content':text});agent.save()
    agent.turn_outcome='completed' if state else 'incomplete'
    return text
