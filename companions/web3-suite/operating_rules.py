"""Versioned bounded operational settings, paired evaluation and continuous monitoring."""
import hashlib,json,re,sqlite3,time,uuid
from pathlib import Path
from contextlib import closing
import repair_lab as lab

DEFAULT={'mode':'verify_first','max_tokens':4096,'repeat_limit':3,'delegation':'tool_calls'}
MODES={'verify_first':'Check assumptions and evidence before claiming success.','concise':'Prefer a short direct solution; preserve all required checks.','stepwise':'Split the task into small verifiable steps before concluding.'}

def folder(home,project):
    path=Path(home)/'operating-rules'/hashlib.sha256(str(Path(project).resolve()).encode()).hexdigest()[:24]
    path.mkdir(parents=True,exist_ok=True);return path

def settings(value):
    if not isinstance(value,dict) or set(value)-set(DEFAULT): raise ValueError('Regole ammesse: mode, max_tokens, repeat_limit, delegation. Permessi e controlli non modificabili.')
    value={**DEFAULT,**value}
    if value['mode'] not in MODES or type(value['max_tokens'])!=int or value['max_tokens'] not in (512,1024,2048,4096) or type(value['repeat_limit'])!=int or not 1<=value['repeat_limit']<=3 or value['delegation'] not in ('tool_calls','coding','reasoning','vision'): raise ValueError('Valori operativi non ammessi.')
    return value

def signature(value): return hashlib.sha256(json.dumps(value,sort_keys=True).encode()).hexdigest()
def read(home,project,ident):
    if ident=='default': return {'id':'default','settings':dict(DEFAULT)}
    if not isinstance(ident,str) or not re.fullmatch('[a-f0-9]{32}',ident): raise ValueError('ID regola non valido. Chiama strategy_status e copia esattamente un ID restituito; non creare una proposta sostitutiva per correggere un ID.')
    record=json.loads((folder(home,project)/(ident+'.json')).read_text(encoding='utf-8'))
    if signature(record['settings'])!=record['settings_hash']: raise ValueError('Regola modificata dopo creazione.')
    settings(record['settings']);return record

def current(home,project):
    pointer=folder(home,project)/'active.json'
    if not pointer.exists(): return read(home,project,'default')
    try: ref=json.loads(pointer.read_text(encoding='utf-8'))
    except (ValueError,OSError):
        lab.store(pointer,{'id':'default','previous':'default','reason':'corrupt_pointer'});return read(home,project,'default')
    if not isinstance(ref,dict):
        lab.store(pointer,{'id':'default','previous':'default','reason':'corrupt_pointer'});return read(home,project,'default')
    try: return read(home,project,ref['id'])
    except (ValueError,OSError,KeyError,TypeError):
        old=ref.get('previous','default')
        try: result=read(home,project,old)
        except (ValueError,OSError,KeyError,TypeError): result=read(home,project,'default')
        lab.store(pointer,{'id':result['id'],'previous':'default','reason':'active_rule_corrupt'});return result

def propose(home,project,value):
    value=settings(value);ident=uuid.uuid4().hex
    record={'id':ident,'settings':value,'settings_hash':signature(value),'state':'proposed','baseline_id':current(home,project)['id']}
    lab.store(folder(home,project)/(ident+'.json'),record);return record

def evaluate(agent,home,ident,model,cases):
    candidate=read(home,agent.root,ident);baseline=current(home,agent.root)
    if candidate.get('state') not in ('proposed','not_better','evaluated'): raise ValueError('Regola non valutabile.')
    if not isinstance(cases,list) or not 3<=len(cases)<=8 or any(not isinstance(c,dict) or not isinstance(c.get('prompt'),str) or not c['prompt'].strip() or 'expected' not in c for c in cases) or len({c['prompt'] for c in cases})!=len(cases): raise ValueError('Servono 3..8 casi distinti {prompt, expected}, identici per le due strategie. Esempio: {"prompt":"Return JSON sum of 2+3", "expected":5}. expected_model non è una risposta verificabile.')
    progress=candidate.get('evaluation_progress')
    identity={'cases':cases,'model':model,'baseline_id':baseline['id']}
    if progress and progress['identity']!=identity:
        raise ValueError('Valutazione incompleta: riprendi con stessi casi, modello e baseline oppure crea una nuova proposta. Le risposte già verificate sono conservate.')
    results=progress['pairs'] if progress else []
    for i,case in enumerate(cases):
        if not isinstance(case.get('prompt'),str) or not case['prompt'].strip() or 'expected' not in case: raise ValueError('Caso non valido.')
        if i<len(results): pair=results[i]
        else: pair={};results.append(pair)
        # Alternate order so the candidate is not always the second request.
        for tag,row in ([('baseline',baseline),('candidate',candidate)] if i%2==0 else [('candidate',candidate),('baseline',baseline)]):
            if tag in pair: continue
            conf=row['settings'];start=time.monotonic()
            candidate['evaluation_progress']={'identity':identity,'pairs':results}
            lab.store(folder(home,agent.root)/(ident+'.json'),candidate)
            try:
                response=agent.api('chat/completions',{'model':model,'max_tokens':conf['max_tokens'],'messages':[{'role':'system','content':MODES[conf['mode']]+' Return only the requested JSON. No tools or external actions.'},{'role':'user','content':case['prompt']}]})
            except Exception as error:
                candidate['last_evaluation_error']={'type':type(error).__name__,'message':str(error)[:1200],'case':i,'side':tag}
                lab.store(folder(home,agent.root)/(ident+'.json'),candidate)
                raise
            choice=response['choices'][0];body=choice['message'].get('content') or ''
            try: passed=json.loads(body)==case['expected'] and choice.get('finish_reason')!='length'
            except ValueError: passed=False
            route=getattr(agent,'last_request',{}).get('routing',{}).get('x-routed-via') or response.get('model')
            pair[tag]={'passed':passed,'model':response.get('model'),'route':route,'tokens':response.get('usage',{}).get('total_tokens'),'seconds':round(time.monotonic()-start,3)}
            candidate['evaluation_progress']={'identity':identity,'pairs':results}
            lab.store(folder(home,agent.root)/(ident+'.json'),candidate)
        pair['comparable']=bool(pair['baseline']['route'] and pair['baseline']['route']==pair['candidate']['route'])
    b=sum(p['baseline']['passed'] for p in results);c=sum(p['candidate']['passed'] for p in results)
    known=all(type(p[t]['tokens']) is int and p[t]['tokens']>0 for p in results for t in ('baseline','candidate'))
    gain=c>b or (c==b and known and sum(p['candidate']['tokens'] for p in results)<=.9*sum(p['baseline']['tokens'] for p in results))
    eligible=all(p['comparable'] and p['candidate']['passed'] for p in results) and gain
    candidate.pop('evaluation_progress',None)
    candidate.update(state='evaluated' if eligible else 'not_better',baseline_id=baseline['id'],evidence={'cases':cases,'model_requested':model,'pairs':results,'baseline_correct':b,'candidate_correct':c,'eligible':eligible,'settings_hash':candidate['settings_hash'],'criterion':'All candidate cases correct, same observed route per pair, and correctness gain or >=10% fewer reported tokens. Narrow benchmark only.'})
    lab.store(folder(home,agent.root)/(ident+'.json'),candidate);return candidate

def activate(home,project,ident):
    with lab.deployment_lock(home): return _activate(home,project,ident)

def _activate(home,project,ident):
    record=read(home,project,ident);baseline=current(home,project)
    if record.get('state')!='evaluated' or not record.get('evidence',{}).get('eligible') or record['evidence']['settings_hash']!=record['settings_hash']: raise ValueError('Manca una valutazione migliore verificata.')
    if record['baseline_id']!=baseline['id']: raise ValueError('Baseline cambiata: rivaluta la strategia.')
    lab.store(folder(home,project)/'active.json',{'id':ident,'previous':baseline['id']})
    return {'active':ident,'previous':baseline['id'],'effective':'next turn; permissions and safety controls unchanged'}

def rollback(home,project,reason='manual',expected=None):
    with lab.deployment_lock(home): return _rollback(home,project,reason,expected)

def _rollback(home,project,reason,expected):
    pointer=folder(home,project)/'active.json'
    if not pointer.exists(): return {'active':'default'}
    ref=json.loads(pointer.read_text(encoding='utf-8'));old=ref.get('previous','default');read(home,project,old)
    if expected is not None and ref['id']!=expected: return {'ignored':'active_version_changed'}
    failed=read(home,project,ref['id'])
    if failed['id']!='default':
        failed['state']='reverted';failed['rollback_reason']=reason;lab.store(folder(home,project)/(failed['id']+'.json'),failed)
    lab.store(pointer,{'id':old,'previous':'default','reason':reason});return {'active':old,'reverted':ref['id'],'reason':reason}

def observe(home,project,version,outcome,tool_errors):
    failed=outcome!='completed' or tool_errors>=2
    with closing(sqlite3.connect(folder(home,project)/'observations.sqlite',timeout=5)) as db,db:
        db.execute('CREATE TABLE IF NOT EXISTS observations(id INTEGER PRIMARY KEY,version TEXT, failed INTEGER, outcome TEXT, tool_errors INTEGER)')
        db.execute('INSERT INTO observations(version,failed,outcome,tool_errors) VALUES(?,?,?,?)',(version,int(failed),outcome,tool_errors))
        window=db.execute('SELECT failed FROM observations WHERE version=? ORDER BY id DESC LIMIT 5',(version,)).fetchall()
    # Operational circuit breaker, not a causal claim about intelligence or task quality.
    if version!='default' and current(home,project)['id']==version and len(window)==5 and sum(r[0] for r in window)>=3:
        return rollback(home,project,'three_or_more_mechanical_failures_in_last_five_turns',expected=version)
    return {'observed':True,'window':len(window),'mechanical_failures':sum(r[0] for r in window)}

def status(home,project):
    root=folder(home,project)
    return {'active':current(home,project),'versions':[json.loads(p.read_text(encoding='utf-8')) for p in root.glob('*.json') if p.name!='active.json'][-20:],'monitor':'last five turns per version; rollback on at least three incomplete/failed turns or >=2 tool errors per turn; not semantic verification'}
