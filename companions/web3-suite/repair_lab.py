"""Staged multi-module repairs, baseline regression, full suite gate and rollback."""
import hashlib,json,os,re,shutil,subprocess,sys,uuid,time
from pathlib import Path
from contextlib import contextmanager

ROOT=Path(__file__).resolve().parent
PROTECTED={'repair_lab.py','generated_tools.py','improvement_tools.py','improvement_runtime.py','operating_rules.py','release_transaction.py','dependency_env.py','managed_state.py','runtime_bootstrap.py','evolution_tools.py','work_ledger.py','qa_evidence.py','qa_interruption.py','loop_guard.py','reliable_runtime.py'}
REUSABLE={'staged','edited','failed','tested','activating','activated_restart_required','rolled_back','recovery_conflict'}
EDITABLE={'staged','edited','failed','tested','rolled_back'}
STOP=frozenset('the a an di il lo la i le un una per che del della dei degli da in su con nel nella non senza from and or to of fix bug bugfix ripara correggere correggi please pls just only anche solo questo questa folder cartella directory file files modulo module test tests'.split())

def digest(path): return hashlib.sha256(Path(path).read_bytes()).hexdigest()
def store(path,data):
    path=Path(path);tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
    tmp.write_text(json.dumps(data,indent=2),encoding='utf-8');os.replace(tmp,path)
def load(home,ident):
    if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident): raise ValueError('invalid_id: ID riparazione non valido. Chiama repair_status e usa un ID esadecimale di 32 caratteri.')
    folder=Path(home)/'repair-lab'/ident
    if not (folder/'record.json').is_file(): raise ValueError('invalid_id: ID riparazione inesistente. Chiama repair_status; non inventare un ID e non ometterlo per ripiegare sul runtime.')
    return folder,json.loads((folder/'record.json').read_text(encoding='utf-8'))
def manifest(root):
    return {str(p.relative_to(root)):digest(p) for p in root.rglob('*') if p.is_file() and '__pycache__' not in p.parts and 'test-home' not in p.parts}

def problem_tokens(text):
    text=(text or '').lower()
    text=re.sub(r'[a-z]:\\[^\s]+',' ',text)
    text=re.sub(r'/[^\s]+',' ',text)
    text=re.sub(r'\b[0-9a-f]{6,}\b',' ',text)
    return sorted({token for token in re.findall(r'[a-zàèéìòù]{3,}',text) if token not in STOP})

def tokens_equivalent(left,right):
    left,right=set(left),set(right)
    if not left or not right: return False
    if left==right or left<=right or right<=left: return True
    return len(left&right)/len(left|right)>=0.6

REPORT_NAMES={'results.json','report.md','prepare.json','baseline.json','regression.json','suite.json','activation.json','rollback.json','post_activation.json'}
REPORT_DIRS={'repair-evidence','__pycache__','test-home'}

def content_fingerprint(source):
    source=Path(source); rows=[]
    if not source.is_dir(): return 'missing'
    for path in sorted(source.rglob('*')):
        if not path.is_file() or path.is_symlink(): continue
        if any(part in REPORT_DIRS for part in path.parts) or path.name.lower() in REPORT_NAMES: continue
        if path.suffix not in ('.py','.md','.json','.csv','.cmd') and path.name!='requirements.txt': continue
        rows.append(path.relative_to(source).as_posix()+':'+digest(path))
    return hashlib.sha256('\n'.join(rows).encode()).hexdigest()

def assert_identity(folder,ident,data):
    if data.get('id')!=ident or Path(folder).name!=ident: raise ValueError('repair_identity_mismatch: ID '+ident+' non coincide con il registro '+str(data.get('id'))+'.')
    root=Path(data.get('source_root','')).resolve()
    if data.get('scope')=='qa_project':
        if 'qa_runs' not in root.parts or root==ROOT.resolve(): raise ValueError('repair_identity_mismatch: source_root non è il progetto di prova '+str(root)+'.')
    elif root!=ROOT.resolve(): raise ValueError('repair_identity_mismatch: la riparazione runtime non punta al runtime installato.')
    if data.get('scope') not in ('qa_project','runtime'): raise ValueError('repair_identity_mismatch: scope assente.')
    return root

def resolve_project(project_path,workspace=None):
    raw=str(project_path or '').strip()
    if not raw: raise ValueError('repair_destination_invalid: project_path vuoto. Indica la directory del progetto sotto qa_runs. Nessun ripiego sul runtime installato. Non modificare i controllori protetti: '+', '.join(sorted(PROTECTED))+'.')
    path=Path(raw)
    if not path.is_absolute():
        if workspace is None: raise ValueError('repair_destination_invalid: percorso relativo senza workspace. Nessun ripiego sul runtime installato.')
        path=Path(workspace)/path
    try: path=path.resolve()
    except OSError as exc: raise ValueError('repair_destination_invalid: percorso non risolvibile. Nessun ripiego sul runtime installato.') from exc
    if not path.is_dir() or 'qa_runs' not in path.parts or path==ROOT.resolve():
        raise ValueError('repair_destination_invalid: '+str(path)+' non è una directory di progetto sotto qa_runs. Nessun ripiego sul runtime installato. Non modificare i controllori protetti: '+', '.join(sorted(PROTECTED))+'.')
    return path

def _records(home):
    rows=[]
    for previous in (Path(home)/'repair-lab').glob('*/record.json'):
        try: rows.append((previous,json.loads(previous.read_text(encoding='utf-8'))))
        except (OSError,ValueError): continue
    return rows

def _reuse(home,source,problem):
    source=Path(source).resolve(); tokens=problem_tokens(problem); fingerprint=content_fingerprint(source)
    exact=similar=None
    active={'staged','edited','failed','tested','activating','recovery_conflict'}
    for previous,old in _records(home):
        if old.get('state') not in REUSABLE: continue
        old_tokens=old.get('problem_tokens') or problem_tokens(old.get('problem',''))
        same_words=old.get('problem')==problem[:4000] or tokens_equivalent(tokens,old_tokens)
        try: same_path=Path(old.get('source_root','')).resolve()==source
        except OSError: same_path=False
        same_bytes=bool(fingerprint!='missing' and old.get('content_fingerprint')==fingerprint)
        # An open repair keeps its attempt count across a reworded problem or a copied folder.
        if same_path and (same_words or (same_bytes and old.get('state') in active)):
            exact=(previous,old)
        elif same_bytes and (same_words or old.get('state') in active):
            similar=(previous,old)
    return exact or similar

def _reuse_payload(previous,old):
    note='Riparazione equivalente già presente. Cambiare cartella o formulazione non azzera i tentativi e non crea un altro ID. source_root resta '+old['source_root']+'.'
    if old.get('state')=='activated_restart_required': note+=' Già attivata: non ripetere repair_activate.'
    if old.get('state')=='recovery_conflict': note+=' Conflitto di ripristino aperto: non sovrascrivere i file e non creare un\'altra copia.'
    return {'id':old['id'],'state':old['state'],'stage':str(previous.parent/'stage'),'source_root':old['source_root'],'scope':old.get('scope','runtime'),'existing':True,'equivalent':True,'attempts':old.get('attempts',0),'blocked':old.get('state')=='recovery_conflict' or old.get('attempts',0)>=2 and old.get('state')=='failed','note':note,'content_fingerprint':old.get('content_fingerprint')}

def start(home,problem,source_root=None):
    source=Path(source_root).resolve() if source_root is not None else ROOT.resolve()
    if not source.is_dir(): raise ValueError('repair_destination_invalid: directory sorgente inesistente. Nessun ripiego su un\'altra destinazione.')
    if source_root is not None and ('qa_runs' not in source.parts or source==ROOT.resolve()): raise ValueError('repair_destination_invalid: il progetto di prova deve essere una directory sotto qa_runs, distinta dal runtime. Non modificare i controllori protetti.')
    reused=_reuse(home,source,problem)
    if reused: return _reuse_payload(*reused)
    ident=uuid.uuid4().hex;folder=Path(home)/'repair-lab'/ident;stage=folder/'stage';stage.mkdir(parents=True)
    for p in source.iterdir():
        if p.is_file() and not p.is_symlink() and (p.suffix in ('.py','.md','.cmd') or p.name=='requirements.txt'): shutil.copy2(p,stage/p.name)
    total=0
    for name in ('qa_runs','integrations'):
        base=source/name
        if not base.exists(): continue
        for p in base.rglob('*'):
            if p.is_file() and not p.is_symlink() and p.suffix in ('.py','.md','.json','.csv') and p.stat().st_size<2_000_000:
                total+=p.stat().st_size
                if total>50_000_000: raise ValueError('Fixture troppo grandi: riduci la riparazione manualmente.')
                dest=stage/p.relative_to(source);dest.parent.mkdir(parents=True,exist_ok=True);shutil.copy2(p,dest)
    shutil.copytree(stage,folder/'baseline')
    scope='qa_project' if source_root is not None else 'runtime'
    data={'id':ident,'problem':problem[:4000],'problem_tokens':problem_tokens(problem),'content_fingerprint':content_fingerprint(source),'state':'staged','changed':None,'baseline':manifest(stage),'source_root':str(source),'scope':scope}
    store(folder/'record.json',data)
    report=publish_report(folder,data)
    return {'id':ident,'state':'staged','stage':str(stage),'source_root':str(source),'scope':scope,'files':len(data['baseline']),'content_fingerprint':data['content_fingerprint'],'note':'Copia dei file, non sandbox del sistema operativo. Aggiornamento coordinato dei moduli; il runtime caricato resta invariato fino al riavvio.','report_path':report.get('report_path'),'report_error':report.get('report_error'),'report_validation':report.get('report_validation')}

def changed_files(data):
    value=data.get('changed')
    return set(value if isinstance(value,list) else [value] if value else [])

def edit(home,ident,path,old,new):
    folder,data=load(home,ident);assert_identity(folder,ident,data)
    if data['state'] not in EDITABLE: raise ValueError('Riparazione già attivata o chiusa.' if data['state']!='recovery_conflict' else 'recovery_conflict: ripristino in conflitto. Non modificare altri file.')
    if Path(path).name!=path or not path.endswith('.py') or path.startswith('test_') or path in PROTECTED or path not in set(data['baseline'])|set(data.get('added',[])): raise ValueError('repair_target_mismatch_or_protected: sorgente '+data['source_root']+'. Chiama repair_read senza path e usa un modulo elencato, non un test o un controllore protetto. Per il banco di prova usa repair_start con project_path del banco; non ripetere la modifica su questo ID e non modificare '+', '.join(sorted(PROTECTED))+'.')
    target=folder/'stage'/path;text=target.read_text(encoding='utf-8')
    if not old or text.count(old)!=1: raise ValueError('invalid_arguments: il testo da sostituire deve comparire esattamente una volta. Rileggi il file nello stage con repair_read; non ripetere lo stesso tentativo.')
    updated=text.replace(old,new,1);compile(updated,path,'exec');target.write_text(updated,encoding='utf-8')
    paths=changed_files(data); paths.add(path)
    data.update(changed=sorted(paths),state='edited');data.pop('tested_manifest',None);store(folder/'record.json',data)
    return {'stage_file':str(target),'sha256':digest(target),'state':'edited','source_root':data['source_root'],'id':ident}

def add_module(home,ident,path,code):
    folder,data=load(home,ident);assert_identity(folder,ident,data)
    if data['state'] not in EDITABLE: raise ValueError('Release non modificabile.')
    if not re.fullmatch(r'[a-z][a-z0-9_]*\.py',path) or path.startswith('test_') or path in PROTECTED or path in data['baseline'] or (folder/'stage'/path).exists() or (Path(data['source_root'])/path).exists(): raise ValueError('Serve un nome di nuovo modulo Python, non un test o controllore protetto.')
    compile(code,path,'exec');(folder/'stage'/path).write_text(code,encoding='utf-8')
    data.setdefault('added',[]).append(path);data['changed']=sorted(changed_files(data)|{path});data['state']='edited';data.pop('tested_manifest',None);store(folder/'record.json',data)
    return {'path':path,'state':'edited','id':ident,'source_root':data['source_root']}

# Runner outside the candidate tree. It records the files Python actually loads.
RUNNER=r'''import hashlib, json, os, sys, unittest
from pathlib import Path
root, pattern, result_path, dependencies, forbidden_json = sys.argv[1:6]
expected = Path(root).resolve()
forbidden = []
for item in json.loads(forbidden_json):
    try: forbidden.append(Path(item).resolve())
    except OSError: continue
opened = []
def audit(event, args):
    if event != 'open' or not args: return
    raw = args[0]
    if isinstance(raw, bytes):
        try: raw = raw.decode()
        except Exception: return
    if not isinstance(raw, (str, os.PathLike)): return
    try: opened.append(str(Path(raw).resolve()))
    except OSError: return
sys.addaudithook(audit)
script_dir = Path(__file__).resolve().parent
kept = []
for entry in sys.path:
    if not entry: continue
    try:
        if Path(entry).resolve() == script_dir: continue
    except OSError: pass
    kept.append(entry)
sys.path[:] = kept
sys.path[:0] = json.loads(dependencies)
sys.path.insert(0, str(expected))
def under(path, parent):
    try:
        Path(path).resolve().relative_to(parent)
        return True
    except (OSError, ValueError): return False
def digest(path):
    try: return hashlib.sha256(Path(path).read_bytes()).hexdigest()
    except OSError: return None
project_names = set()
if expected.is_dir():
    for candidate in expected.rglob('*.py'):
        if '__pycache__' in candidate.parts: continue
        project_names.add(candidate.parent.name if candidate.name == '__init__.py' else candidate.stem)
suite = unittest.defaultTestLoader.discover(str(expected), pattern=pattern)
outcome = unittest.TextTestRunner(verbosity=2).run(suite)
def classify(path):
    if under(path, expected): return 'tree'
    for bad in forbidden:
        if under(path, bad): return 'forbidden'
    return 'external'
modules = []; contaminated = []
for name, mod in sorted(sys.modules.items()):
    file_name = getattr(mod, '__file__', None)
    if not file_name: continue
    try: path = Path(file_name).resolve()
    except OSError: continue
    kind = classify(path); top = name.split('.')[0]
    if kind == 'external' and top not in project_names: continue
    if kind == 'tree' and top not in project_names: continue
    row = {'module': name, 'file': str(path), 'sha256': digest(path), 'origin': kind}
    if kind != 'tree':
        expected_file = expected / (top + '.py')
        if not expected_file.is_file(): expected_file = expected / top / '__init__.py'
        row['expected_file'] = str(expected_file) if expected_file.is_file() else None
        row['expected_sha256'] = digest(expected_file) if expected_file.is_file() else None
        row['forbidden'] = True
        contaminated.append(row)
    modules.append(row)
opened_hits = []; seen = set()
for raw in opened:
    path = Path(raw)
    if path.suffix not in ('.py', '.pyc') or classify(path) != 'forbidden': continue
    if str(path) in seen: continue
    seen.add(str(path))
    opened_hits.append({'file': str(path), 'sha256': digest(path), 'origin': 'open'})
    if len(opened_hits) >= 40: break
dirty = bool(contaminated or opened_hits)
payload = {'tests': outcome.testsRun, 'failures': len(outcome.failures), 'errors': len(outcome.errors), 'skipped': len(outcome.skipped), 'ok': bool(outcome.wasSuccessful() and outcome.testsRun > len(outcome.skipped) and not dirty), 'observed': True, 'origin': 'repair_runner', 'contamination': dirty, 'provenance': {'modules': modules, 'contaminated_modules': contaminated, 'contaminated_opens': opened_hits}}
Path(result_path).write_text(json.dumps(payload), encoding='utf-8')
sys.exit(0 if payload['ok'] else 1)
'''

def _trim(result):
    provenance=result.get('provenance') or {}
    modules=provenance.get('modules') or []
    result=dict(result)
    result['provenance']={'module_count':len(modules),'modules':modules[:40],'contaminated_modules':provenance.get('contaminated_modules') or [],'contaminated_opens':(provenance.get('contaminated_opens') or [])[:40]}
    return result

def run_tests(root,folder,label,pattern='test_*.py',timeout=240,python=None,forbidden=None):
    root=Path(root);folder=Path(folder);folder.mkdir(parents=True,exist_ok=True);runner=folder/'trusted_test_runner.py';runner.write_text(RUNNER,encoding='utf-8')
    result=folder/(label+'.json');log=folder/(label+'.log');result.unlink(missing_ok=True)
    env=dict(os.environ);test_home=folder/'test-home';test_home.mkdir(exist_ok=True)
    for key in list(env):
        if any(word in key.upper() for word in ('API_KEY','TOKEN','SECRET')): env.pop(key,None)
    env.update(APPDATA=str(test_home),HOME=str(test_home),USERPROFILE=str(test_home),PYTHONUTF8='1')
    forbidden_json=json.dumps([str(Path(path).resolve()) for path in (forbidden or [])])
    with log.open('w',encoding='utf-8') as output:
        dependencies=json.dumps([] if python else [p for p in sys.path if p and Path(p).name in ('site-packages','dist-packages')])
        process=subprocess.Popen([str(python or sys.executable),'-X','utf8',str(runner),str(root),pattern,str(result),dependencies,forbidden_json],cwd=root,env=env,stdout=output,stderr=subprocess.STDOUT)
        started=time.monotonic()
        try:
            while process.poll() is None:
                remaining=timeout-(time.monotonic()-started)
                if remaining<=0: raise subprocess.TimeoutExpired(process.args,timeout)
                try: process.wait(timeout=min(10,remaining))
                except subprocess.TimeoutExpired:
                    print(f'Verifica {label}: {time.monotonic()-started:.0f}s · log {log}',flush=True)
        except (KeyboardInterrupt,subprocess.TimeoutExpired) as stopped:
            if os.name=='nt': subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else: process.kill()
            process.wait()
            if isinstance(stopped,KeyboardInterrupt): raise
            return {'ok':False,'observed':True,'origin':'repair_runner','timeout_or_interrupted':True,'exit_code':None,'log':str(log),'code':'test_error'}
    try: data=json.loads(result.read_text(encoding='utf-8'))
    except (ValueError,OSError): data={'ok':False,'observed':False,'error':'Nessun risultato strutturato.','code':'test_error'}
    data['exit_code']=process.returncode
    data['ok']=bool(data.get('ok') and process.returncode==0 and not data.get('contamination'))
    data['log']=str(log); data['observed']=True; data['origin']=data.get('origin') or 'repair_runner'
    try: result.write_text(json.dumps(data),encoding='utf-8')
    except OSError: pass
    return _trim(data)

def _forbidden(folder,data,current):
    paths=[folder/'baseline',folder/'stage',Path(data['source_root'])]
    current=Path(current).resolve()
    return [path for path in paths if Path(path).resolve()!=current]

def _contaminated(result):
    provenance=result.get('provenance') or {}
    return list(provenance.get('contaminated_modules') or [])+list(provenance.get('contaminated_opens') or [])

def _contamination_text(label,rows,hints):
    lines=['test_contamination: il test di '+label+' ha eseguito moduli che non appartengono a quella copia. Il successo dei test non è una regressione. Il gate deve continuare a rifiutare una baseline che non esegue il codice difettoso.']
    for row in rows[:8]:
        lines.append('- '+str(row.get('module') or row.get('file'))+' caricato da '+str(row.get('file'))+' sha256='+str(row.get('sha256'))+' ; copia attesa '+str(row.get('expected_file'))+' sha256='+str(row.get('expected_sha256')))
    if hints: lines.append('Il testo del test contiene anche questi percorsi: '+', '.join(hints)+'. La decisione si basa sui file caricati, non soltanto sulla stringa.')
    lines.append('Correzione: elimina sys.path.insert, importlib e ogni percorso assoluto verso stage, baseline o la cartella del progetto. Importa i moduli per nome (from math_lib import ...). Il runner mette già sul path la copia sotto test. Riscrivi test_code e, dopo aver letto il log con repair_status o read_file, richiama repair_verify. Non modificare il gate e non toccare i controllori protetti.')
    return '\n'.join(lines)

def _static_hints(test_code,folder,data):
    hints=[]
    for label,path in (('stage',folder/'stage'),('baseline',folder/'baseline'),('source',Path(data['source_root']))):
        if str(path) in test_code: hints.append(label+':'+str(path))
    return hints

def verify(home,ident,test_code):
    folder,data=load(home,ident);assert_identity(folder,ident,data)
    if data['state']=='activated_restart_required': raise ValueError('repair_already_activated: ID '+ident+' già attivato su '+data['source_root']+'. Non ripetere l\'attivazione e non ricreare il progetto.')
    if data['state']=='recovery_conflict': raise ValueError('recovery_conflict: ripristino in conflitto su '+ident+'. Non continuare questa riparazione.')
    if data['state'] not in ('edited','failed','tested','rolled_back') or not (data['changed'] or data.get('migrations') or data.get('dependencies')): raise ValueError('Prepara prima una modifica.')
    if data.get('attempts',0)>=2: raise ValueError('Due verifiche già tentate: fermati e riporta log/problema irrisolto. Non creare copie, cartelle o formulazioni diverse per aggirare il limite.')
    data['attempts']=data.get('attempts',0)+1;store(folder/'record.json',data)
    compile(test_code,'test_repair_regression.py','exec')
    for name in ('baseline','stage'):
        (folder/name/'test_repair_regression.py').write_text(test_code,encoding='utf-8')
    current=manifest(folder/'stage')
    original=manifest(folder/'baseline')
    if any(original.get(p)!=h for p,h in data['baseline'].items()): raise ValueError('Baseline modificata: crea una nuova riparazione.')
    changed={p for p in current.keys()|data['baseline'].keys() if current.get(p)!=data['baseline'].get(p)}
    if not changed<=changed_files(data)|{'test_repair_regression.py'}: raise ValueError('Lo stage contiene modifiche non ammesse: '+str(sorted(changed)))
    import managed_state,dependency_env
    managed_state.check_plan(home,folder,data)
    hints=_static_hints(test_code,folder,data)
    managed_state.seed_test_home(folder,data,baseline=True)
    baseline=run_tests(folder/'baseline',folder,'baseline','test_repair_regression.py',forbidden=_forbidden(folder,data,folder/'baseline'))
    managed_state.seed_test_home(folder,data)
    python=dependency_env.checked_python(data)
    regression=run_tests(folder/'stage',folder,'regression','test_repair_regression.py',python=python,forbidden=_forbidden(folder,data,folder/'stage'))
    contaminated=_contaminated(baseline)+_contaminated(regression)
    code=None; message=None
    if contaminated:
        code='test_contamination'
        message=_contamination_text('baseline' if _contaminated(baseline) else 'candidata',contaminated,hints)
        suite={'ok':False,'not_run':message,'code':code}
        state='failed'
    else:
        reproduced=baseline.get('failures',0)>0 and baseline.get('errors',0)==0 and baseline.get('tests',0)>0 and not baseline.get('ok')
        if reproduced and regression.get('ok'):
            managed_state.seed_test_home(folder,data)
            suite=run_tests(folder/'stage',folder,'suite',python=python,forbidden=_forbidden(folder,data,folder/'stage'))
            if _contaminated(suite):
                code='test_contamination'; message=_contamination_text('suite',_contaminated(suite),hints); suite={'ok':False,'not_run':message,'code':code}
        else:
            suite={'ok':False,'not_run':'baseline_not_reproduced: la baseline ha eseguito i propri moduli e il test non è fallito per asserzione. Il gate rifiuta correttamente. Non allentarlo e non accusarlo: scrivi un test che fallisce sul codice difettoso della baseline e passa sulla candidata, senza percorsi assoluti.','code':'baseline_not_reproduced'}
            code='baseline_not_reproduced'; message=suite['not_run']
        state='tested' if reproduced and regression.get('ok') and suite.get('ok') and not code else 'failed'
    if not contaminated and baseline.get('errors',0):
        message='Baseline non eseguibile: consulta il log '+str(baseline.get('log'))+'. Un import di un modulo aggiunto solo nello stage non dimostra un bug preesistente. Non modificare la baseline per forzare il test.'
        code='test_error'
    evidence={'baseline':baseline,'regression':regression,'suite':suite,'provenance':{'baseline':baseline.get('provenance'),'regression':regression.get('provenance')}}
    if code: evidence['code']=code; evidence['message']=message; evidence['next_step']='Leggi il log o repair_status, poi correggi test_code. Non ripetere repair_verify senza quella lettura e non cambiare cartella per azzerare i tentativi.'
    data.update(state=state,evidence=evidence,tested_manifest=current)
    store(folder/'record.json',data)
    report=publish_report(folder,data)
    result={k:data[k] for k in ('id','state','changed','attempts','evidence')}
    result.update(ok=state=='tested',code=code,source_root=data['source_root'],next_step=evidence.get('next_step'),report_path=report.get('report_path'),report_error=report.get('report_error'),report_validation=report.get('report_validation'))
    return result

def run_directory(source):
    """Parent run folder qa_runs/<run>, never the project directory itself."""
    source=Path(source); parts=source.parts
    if 'qa_runs' not in parts: return None
    index=parts.index('qa_runs')
    if len(parts)<index+3: return None
    run=Path(*parts[:index+2])
    if run.resolve()==Path(source).resolve(): return None
    return run

def _phase(status,reason='',evidence=None,mode='local'):
    row={'status':status,'mode':mode}
    if reason: row['reason']=reason
    if evidence: row['evidence']=evidence
    return row

def _evidence_pointer(folder,name):
    path=Path(folder)/name
    if not path.is_file(): return None
    return {'path':name,'sha256':hashlib.sha256(path.read_bytes()).hexdigest()}

def _measured_phase(status,pointer,reason=''):
    if pointer is None: return _phase('NOT_VERIFIED',reason or 'Prova non registrata.')
    return _phase(status,reason,pointer)

def _write_observed(folder,name,payload):
    payload=dict(payload,observed=True,origin=payload.get('origin') or 'repair_runner')
    store(Path(folder)/name,payload)
    return _evidence_pointer(folder,name)

def publish_report(folder,data):
    """Write JSON and Markdown from the repair record. Failures stay visible."""
    folder=Path(folder)
    try:
        evidence=data.get('evidence') or {}
        baseline,regression,suite=evidence.get('baseline') or {},evidence.get('regression') or {},evidence.get('suite') or {}
        phases={}
        if data['state'] in ('staged','edited'):
            phases['prepare']=_measured_phase('PASS',_write_observed(folder,'prepare.json',{'ok':True,'exit_code':0,'files':len(data.get('baseline') or {}),'note':'Copia dei file, non sandbox del sistema operativo.'}),'Copia creata.')
        base_ptr=_evidence_pointer(folder,'baseline.json'); reg_ptr=_evidence_pointer(folder,'regression.json'); suite_ptr=_evidence_pointer(folder,'suite.json')
        base_dirty=bool(_contaminated(baseline))
        if baseline and base_ptr:
            phases['baseline_isolation']=_measured_phase('FAIL' if base_dirty or not baseline.get('provenance') else 'PASS',base_ptr,'Import fuori dalla copia baseline.' if base_dirty else 'Moduli caricati dalla copia baseline.')
            reproduced=baseline.get('failures',0)>0 and baseline.get('errors',0)==0 and baseline.get('tests',0)>0 and not baseline.get('ok') and not _contaminated(baseline)
            phases['baseline_regression']=_measured_phase('PASS' if reproduced else 'FAIL',base_ptr,'Bug riprodotto sulla baseline.' if reproduced else (evidence.get('message') or 'Baseline non fallita per asserzione o contaminata.'))
        elif 'baseline_regression' not in phases:
            phases['baseline_regression']=_phase('NOT_VERIFIED','Verifica non eseguita.')
        if regression and reg_ptr:
            candidate_ok=bool(regression.get('ok') and not _contaminated(regression))
            phases['candidate_regression']=_measured_phase('PASS' if candidate_ok else 'FAIL',reg_ptr,'Candidata eseguita sui propri moduli.' if candidate_ok else 'Candidata non superata o contaminata.')
        elif data['state'] not in ('staged','edited'):
            phases['candidate_regression']=_phase('NOT_VERIFIED','Candidata non eseguita.')
        if suite.get('not_run'): phases['suite']=_phase('NOT_VERIFIED',suite['not_run'])
        elif suite and suite_ptr: phases['suite']=_measured_phase('PASS' if suite.get('ok') and not _contaminated(suite) else 'FAIL',suite_ptr)
        else: phases['suite']=_phase('NOT_VERIFIED','Suite non eseguita.')
        post=data.get('post_activation')
        if isinstance(post,dict) and (folder/'post_activation.json').is_file():
            phases['post_activation']=_measured_phase('PASS' if post.get('ok') else 'FAIL',_evidence_pointer(folder,'post_activation.json'))
        if (folder/'activation.json').is_file() and data['state']=='activated_restart_required':
            phases['activation']=_measured_phase('PASS',_evidence_pointer(folder,'activation.json'),'Hash installati confrontati con il manifest testato.')
        elif 'activation' not in phases:
            phases['activation']=_phase('NOT_VERIFIED','Attivazione non eseguita.')
        if (folder/'rollback.json').is_file() and data['state']=='rolled_back':
            phases['rollback']=_measured_phase('PASS',_evidence_pointer(folder,'rollback.json'),'Hash ripristinati e file nuovi rimossi.')
        else:
            phases['rollback']=_phase('NOT_VERIFIED','Rollback non eseguito.' if data['state']!='recovery_conflict' else 'Rollback in conflitto: file successivi non sovrascritti.')
        status={'tested':'TESTED','failed':'FAILED','activated_restart_required':'ACTIVATED','rolled_back':'ROLLED_BACK','staged':'STAGED','edited':'STAGED','activating':'STAGED','recovery_conflict':'FAILED'}.get(data['state'],'FAILED')
        report={'status':status,'repair_id':data['id'],'source_root':data.get('source_root'),'state':data['state'],'attempts':data.get('attempts',0),'api_requests':None,'provider_tokens':None,'request_count_verified':False,'require_markdown':True,'kind':'repair','phases':phases,'scope':'Prove del repair runner. Un hash non certifica da solo il significato: lo stato deve concordare con tests, failures e provenance. Metriche API non misurate: null.','code':evidence.get('code'),'message':evidence.get('message')}
        report_path=folder/'results.json'; markdown=folder/'REPORT.md'
        _write_pair(report_path,markdown,report,data)
        checked=_validate_pair(report_path)
        mirror,mirror_checked=_mirror_report(folder,data,report)
        result={'report_path':str(mirror or report_path),'report_validation':mirror_checked or checked}
        errors=(checked.get('errors') or [])+(mirror_checked.get('errors') or [] if mirror_checked else [])
        if errors: result['report_error']=errors
        if mirror: result['mirrored_report']=str(mirror)
        return result
    except Exception as exc:
        return {'report_error':type(exc).__name__+': '+str(exc),'report_path':str(folder/'results.json')}

def _write_pair(report_path,markdown,report,data):
    lines=['# Report riparazione', '', 'repair_id: '+str(data.get('id')), 'source_root: '+str(data.get('source_root')), 'state: '+str(data.get('state')), '']
    lines.append('api_requests: null')
    lines.append('')
    for name,row in report['phases'].items():
        lines.append('- '+name+': '+row['status'])
        if row.get('reason'): lines.append('  reason: '+row['reason'][:500])
    if report.get('message'): lines += ['', '## Diagnosi runtime', '', report['message'][:2000]]
    lines.append('')
    lines.append('Le misure non osservate restano null. Lo stato di una fase è accettato solo se il JSON della prova contiene i conteggi o gli hash corrispondenti.')
    store(report_path,report); markdown.write_text('\n'.join(lines)+'\n',encoding='utf-8')

def _validate_pair(report_path):
    from qa_evidence import validate
    return validate(report_path)

def _mirror_report(folder,data,report):
    run=run_directory(data.get('source_root',''))
    if run is None or Path(run).resolve()==Path(folder).resolve(): return None,None
    run=Path(run); target=run/'repair-evidence'; target.mkdir(parents=True,exist_ok=True)
    mirrored=json.loads(json.dumps(report))
    for name in ('prepare.json','baseline.json','regression.json','suite.json','post_activation.json','activation.json','rollback.json'):
        source=Path(folder)/name
        if source.is_file(): shutil.copy2(source,target/name)
    for row in mirrored['phases'].values():
        evidence=row.get('evidence') or {}
        if evidence.get('path'):
            evidence['path']='repair-evidence/'+Path(evidence['path']).name
            copied=run/evidence['path']
            if copied.is_file(): evidence['sha256']=hashlib.sha256(copied.read_bytes()).hexdigest()
            else: row.pop('evidence',None); row['status']='NOT_VERIFIED'; row['reason']='Prova non copiata nel report.'
            row['evidence']=evidence if copied.is_file() else None
            if row.get('evidence') is None: row.pop('evidence',None)
    path=run/'results.json'; markdown=run/'REPORT.md'
    _write_pair(path,markdown,mirrored,data)
    return path,_validate_pair(path)

@contextmanager
def deployment_lock(home):
    lock=Path(home)/'repair-lab'/'deployment.lock';lock.parent.mkdir(parents=True,exist_ok=True)
    stream=lock.open('a+b');stream.seek(0)
    if stream.read(1)==b'': stream.write(b'0');stream.flush()
    stream.seek(0)
    try:
        if os.name=='nt':
            import msvcrt
            msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
        else:
            import fcntl
            fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
        yield
    finally: stream.close()

def replace(source,dest):
    dest=Path(dest);temp=dest.with_name(dest.name+'.repair-'+uuid.uuid4().hex)
    shutil.copy2(source,temp);os.replace(temp,dest)

def promote(home,ident):
    from release_transaction import promote
    return promote(home,ident)

def rollback(home,ident):
    from release_transaction import rollback
    return rollback(home,ident)
