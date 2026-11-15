"""Versioned JSON transformation tools: restricted imports, subprocess tests, rollback."""
import ast,hashlib,json,os,re,subprocess,sys,tempfile,uuid
from pathlib import Path
from repair_lab import store

RUNNER=r"""import json,sys,math,re,types
allowed={'json':types.SimpleNamespace(loads=json.loads,dumps=json.dumps), 'math':types.SimpleNamespace(sqrt=math.sqrt,ceil=math.ceil,floor=math.floor,isfinite=math.isfinite), 're':types.SimpleNamespace(search=re.search,match=re.match,findall=re.findall,sub=re.sub,split=re.split,escape=re.escape)}
def importer(name,*args):
    if name not in allowed: raise ValueError('Import not allowed')
    return allowed[name]
safe={k:__builtins__[k] for k in []} if isinstance(__builtins__,dict) else {}
import builtins
for k in ['len','sum','min','max','abs','round','int','float','str','bool','list','dict','set','tuple','range','enumerate','zip','sorted','all','any','isinstance','Exception','ValueError','TypeError']:
    safe[k]=getattr(builtins,k)
safe['__import__']=importer
scope={'__builtins__':safe}
source=sys.argv[1]
with open(source,encoding='utf-8') as f: exec(compile(f.read(),source,'exec'),scope)
args=json.load(sys.stdin)
result=scope['run'](args)
text=json.dumps(result,ensure_ascii=False,allow_nan=False)
if len(text)>1000000: raise ValueError('Output too large')
print(text)
"""

def validate(code):
    if len(code)>20000: raise ValueError('Codice troppo grande.')
    tree=ast.parse(code)
    blocked={'open','eval','exec','compile','getattr','setattr','delattr','vars','globals','locals','input','help','dir','breakpoint','type','object','super'}
    for node in ast.walk(tree):
        if isinstance(node,(ast.ClassDef,ast.Global,ast.Nonlocal,ast.AsyncFunctionDef)): raise ValueError('Usa solo funzioni pure JSON.')
        if isinstance(node,ast.Name) and (node.id.startswith('_') or node.id in blocked): raise ValueError('Nome non ammesso: '+node.id)
        if isinstance(node,ast.Attribute) and node.attr.startswith('_'): raise ValueError('Introspezione privata non ammessa.')
        if isinstance(node,ast.Import) and any(a.name not in ('json','math','re') for a in node.names): raise ValueError('Import ammessi: json, math, re.')
        if isinstance(node,ast.ImportFrom): raise ValueError('Usa import json/math/re, non from.')
    if not any(isinstance(n,ast.FunctionDef) and n.name=='run' for n in tree.body): raise ValueError('Definisci run(args) che restituisce un valore JSON.')

def name_check(name):
    if not re.fullmatch(r'[a-z][a-z0-9_]{1,40}',name): raise ValueError('Nome strumento non valido.')

def execute(source,args):
    if len(json.dumps(args))>100000: raise ValueError('Input troppo grande.')
    with tempfile.TemporaryDirectory(prefix='freellmapi-tool-') as tmp:
        runner=Path(tmp)/'runner.py';runner.write_text(RUNNER,encoding='utf-8')
        env={k:v for k,v in os.environ.items() if k.upper() in ('SYSTEMROOT','WINDIR','TEMP','TMP','PATH')}
        result=subprocess.run([sys.executable,'-I','-X','utf8',str(runner),str(source)],input=json.dumps(args),text=True,encoding='utf-8',stdout=subprocess.PIPE,stderr=subprocess.PIPE,cwd=tmp,env=env,timeout=5)
        if result.returncode: raise ValueError('Strumento fallito: '+result.stderr[-1500:])
        if len(result.stdout)>1000000: raise ValueError('Output oltre limite.')
        return json.loads(result.stdout)

def check_args(schema,args):
    if not isinstance(schema,dict) or schema.get('type')!='object' or not isinstance(schema.get('properties'),dict): raise ValueError('Schema richiesto: object con properties e required.')
    required=schema.get('required',[])
    if not isinstance(required,list) or any(not isinstance(k,str) or k not in schema['properties'] for k in required): raise ValueError('required deve elencare proprietà esistenti.')
    if any(not isinstance(v,dict) or v.get('type') not in ('string','integer','number','boolean','object','array') for v in schema['properties'].values()): raise ValueError('Tipo schema non supportato.')
    if not isinstance(args,dict): raise ValueError('Gli argomenti devono essere un oggetto JSON.')
    if set(args)-set(schema['properties']) or set(schema.get('required',[]))-set(args): raise ValueError('Argomenti sconosciuti o obbligatori mancanti.')
    types={'string':str,'integer':int,'number':(int,float),'boolean':bool,'object':dict,'array':list}
    for key,value in args.items():
        kind=schema['properties'][key].get('type')
        if kind not in types or not isinstance(value,types[kind]) or kind in ('integer','number') and isinstance(value,bool): raise ValueError('Tipo non valido: '+key)

def build(home,name,description,code,schema,cases):
    name_check(name);validate(code)
    if not isinstance(cases,list) or not 2<=len(cases)<=20: raise ValueError('Servono 2..20 casi indipendenti con input e expected, includendo casi limite.')
    if len({json.dumps(c.get('input'),sort_keys=True) for c in cases})<2: raise ValueError('Servono input distinti.')
    folder=Path(home)/'generated-tools'/name;folder.mkdir(parents=True,exist_ok=True)
    version=hashlib.sha256(json.dumps([code,schema,cases],sort_keys=True).encode()).hexdigest()
    source=folder/(version+'.py');source.write_text(code,encoding='utf-8')
    results=[]
    for case in cases:
        check_args(schema,case['input'])
        try:
            actual=execute(source,case['input']);results.append({'passed':actual==case['expected'],'actual':actual})
        except Exception as exc: results.append({'passed':False,'error':str(exc)[:200]})
    report={'name':name,'version':version,'description':description[:500],'schema':schema,'cases':cases,'tests':results,'source_hash':hashlib.sha256(source.read_bytes()).hexdigest(),'passed':all(r['passed'] for r in results)}
    store(folder/(version+'.json'),report)
    if report['passed']:
        pointer=folder/'active.json';old=json.loads(pointer.read_text(encoding='utf-8')) if pointer.exists() else {}
        previous=old.get('version') if old.get('version')!=version else old.get('previous')
        store(pointer,{'version':version,'previous':previous})
    return report

def listing(home):
    result=[]
    for pointer in (Path(home)/'generated-tools').glob('*/active.json'):
        try:
            ref=json.loads(pointer.read_text(encoding='utf-8'));data=json.loads((pointer.parent/(ref['version']+'.json')).read_text(encoding='utf-8'))
            result.append({k:data[k] for k in ('name','description','version','schema','passed')})
        except (OSError,ValueError,KeyError): continue
    return result

def run(home,name,args):
    name_check(name);folder=Path(home)/'generated-tools'/name;pointer=folder/'active.json'
    ref=json.loads(pointer.read_text(encoding='utf-8'));data=json.loads((folder/(ref['version']+'.json')).read_text(encoding='utf-8'));source=folder/(ref['version']+'.py')
    check_args(data['schema'],args)
    if hashlib.sha256(source.read_bytes()).hexdigest()!=data['source_hash']: raise ValueError('Codice modificato dopo i test: ricostruisci lo strumento.')
    validate(source.read_text(encoding='utf-8'))
    try: return {'ok':True,'version':ref['version'],'result':execute(source,args)}
    except Exception as exc:
        if ref.get('previous'): store(pointer,{'version':ref['previous'],'previous':None})
        else: pointer.unlink(missing_ok=True)
        return {'ok':False,'error':str(exc)[:500],'rolled_back':ref.get('previous'),'disabled':not ref.get('previous'),'reexecuted':False}
