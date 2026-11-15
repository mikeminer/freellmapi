"""Persistent coordinator evidence and tested model routing. Not model training."""
import json, sqlite3, time, uuid, hashlib
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone

def record(agent,kind,data):
    data={**data,'project':str(agent.root)}
    try:
        path=Path(agent.session).parent/'coordination.sqlite';path.parent.mkdir(parents=True,exist_ok=True)
        with closing(sqlite3.connect(path,timeout=5)) as db,db:
            db.execute('CREATE TABLE IF NOT EXISTS events(id INTEGER PRIMARY KEY, time TEXT, session TEXT, kind TEXT, data TEXT)')
            db.execute('INSERT INTO events(time,session,kind,data) VALUES(?,?,?,?)',(datetime.now(timezone.utc).isoformat(),str(agent.session),kind,json.dumps(data,ensure_ascii=False)))
    except (OSError,sqlite3.Error): pass # Observability must not repeat a completed action.

def state(agent):
    path=Path(agent.session).parent/'coordination.sqlite'
    if not path.exists(): return {'events':[]}
    with closing(sqlite3.connect(path)) as db:
        events=[{'time':r[0],'session':r[1],'kind':r[2],'data':json.loads(r[3])} for r in db.execute('SELECT time,session,kind,data FROM events ORDER BY id DESC LIMIT 60')]
    events=[e for e in events if e['data'].get('project')==str(agent.root)][:20]
    for event in events:
        if 'prompt' in event['data']: event['data']['prompt']=event['data']['prompt'][:500]
    return {'events':events,'note':'Turn completion is a runtime outcome, not independent proof of task correctness. Probe scores apply only to measured tasks.'}

def benchmark(agent,model,home,capability='tool_calls'):
    import secrets
    nonce=secrets.token_hex(5)
    schema={'type':'function','function':{'name':'probe_echo','description':'Diagnostic only; echo value.', 'parameters':{'type':'object','properties':{'value':{'type':'string'}},'required':['value'],'additionalProperties':False}}}
    started=time.monotonic()
    if capability not in ('tool_calls','coding','reasoning','vision'): raise ValueError('Capacità: tool_calls, coding, reasoning, vision.')
    if capability=='vision':
        from model_probe import vision_probe
        previous=agent.model
        try:
            agent.model=model;probe=vision_probe(agent)
        finally: agent.model=previous
        response={'model':probe['model'],'choices':[{'message':{}}]};passed=probe['passed']
    else:
        prompt='Call probe_echo with value '+nonce
        if capability=='coding': prompt="Return only Python code defining run(args), returning sorted distinct integers from args['values'], ignoring booleans and non-integers. No I/O or private attributes."
        if capability=='reasoning':
            a=int(nonce[:4],16);b=int(nonce[4:8],16);prompt=f'Return only JSON with key answer for ({a} * 7) - ({b} * 3).'
        payload={'model':model,'messages':[{'role':'user','content':prompt}],'max_tokens':2048 if capability=='coding' else 600}
        if capability=='tool_calls': payload.update(tools=[schema],tool_choice={'type':'function','function':{'name':'probe_echo'}})
        response=agent.api('chat/completions',payload)
    message=response['choices'][0]['message']
    if capability!='vision': passed=False
    for call in message.get('tool_calls',[]) if capability=='tool_calls' else []:
        try: passed=call['function']['name']=='probe_echo' and json.loads(call['function']['arguments'])=={'value':nonce}
        except (KeyError,ValueError,TypeError): continue
        if passed: break
    if capability=='reasoning':
        try: passed=json.loads(message.get('content') or '{}')=={'answer':a*7-b*3}
        except ValueError: passed=False
    if capability=='coding':
        import tempfile,re
        from generated_tools import validate,execute
        code=message.get('content') or ''
        code=re.sub(r'^```(?:python)?\s*|\s*```$','',code.strip())
        try:
            validate(code)
            with tempfile.TemporaryDirectory() as tmp:
                source=Path(tmp)/'probe.py';source.write_text(code,encoding='utf-8')
                passed=all(execute(source,{'values':values})==expected for values,expected in [([3,1,3,-2],[-2,1,3]),([True,False,2,'3',2.5,None],[2]),([],[])])
        except Exception: passed=False
    actual=response.get('model')
    finish=response['choices'][0].get('finish_reason')
    if not passed and (finish=='length' or capability in ('coding','reasoning') and not message.get('content')): passed=None
    routing=getattr(agent,'last_request',{}).get('routing',{})
    route=routing.get('x-routed-via') or actual
    report={'requested':model,'actual':actual,'capability':capability,'passed':passed,'seconds':round(time.monotonic()-started,3),'time':datetime.now(timezone.utc).isoformat(),'usage':response.get('usage',{}),'criterion':{'tool_calls':'Random exact tool argument; no real tool executed.','coding':'Three checked cases of a pure integer-filtering program, not general coding ability.','reasoning':'One random arithmetic expression, not general reasoning ability.','vision':'Random characters in an image, not general visual understanding.'}[capability]}
    if capability=='tool_calls': report['tool_call_passed']=passed
    report.update(route=route,finish_reason=finish,inconclusive=passed is None)
    folder=Path(home)/'model-evidence';folder.mkdir(parents=True,exist_ok=True)
    (folder/(uuid.uuid4().hex+'.json')).write_text(json.dumps(report,indent=2),encoding='utf-8')
    record(agent,'model_probe',report)
    return report

def select(agent,home,capability='tool_calls'):
    if capability not in ('tool_calls','coding','reasoning','vision'): raise ValueError('Capacità non valida.')
    from server_integration import eligible
    rows=agent._api('models?execution_status=ready').get('data',[])
    payload={'messages':agent.messages[-6:],'tools':agent.tool_definitions(),'max_tokens':4096}
    ready={r['id'] for r in eligible(rows,payload)}
    latest={}
    for p in (Path(home)/'model-evidence').glob('*.json'):
        try:
            r=json.loads(p.read_text(encoding='utf-8'));actual=r.get('route') or r.get('actual')
            if actual and r.get('capability','tool_calls')==capability and r['time']>latest.get(actual,{}).get('time',''): latest[actual]=r
        except (ValueError,OSError,KeyError): continue
    qualified=[]
    for name,r in latest.items():
        # The server catalog uses canonical aliases while responses can report provider/model.
        canonical=name if name in ready else (r.get('actual') or '').split('/')[-1]
        if canonical not in ready: continue
        if r.get('passed',r.get('tool_call_passed')) and time.time()-datetime.fromisoformat(r['time']).timestamp()<7*86400 and max(agent.cooldowns.get(name,0),agent.cooldowns.get(canonical,0))<time.time():
            qualified.append({**r,'catalog_model':canonical})
    qualified.sort(key=lambda r:r['seconds'])
    return {'model':qualified[0]['catalog_model'] if qualified else ('auto:reliable' if agent.server_router else 'auto'),'observed_route':qualified[0].get('route') if qualified else None,'capability':capability,'basis':'recent measured probe success matched to ready catalog alias; provider may change' if qualified else 'no matching measured candidate; delegate selection to server','ready_count':len(ready)}
