"""Reviewable connector setup. Credentials stay in process environment, not manifests."""
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess


def github(resource,repo=None,number=None):
    """Existing gh credential store, read-only REST paths only. Never export tokens."""
    if resource=='profile': endpoint='user'
    elif resource=='repositories': endpoint='user/repos?per_page=30&sort=updated'
    else:
        if not repo or not re.fullmatch(r'[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+',repo): raise ValueError('Repository nel formato proprietario/nome.')
        if resource in ('issues','pulls'): endpoint=f'repos/{repo}/{resource}?state=open&per_page=30'
        elif resource in ('issue','pull'):
            if not isinstance(number,int) or number<1: raise ValueError('Numero issue/PR positivo richiesto.')
            endpoint=f'repos/{repo}/{"issues" if resource=="issue" else "pulls"}/{number}'
        else: raise ValueError('resource: profile, repositories, issues, pulls, issue, pull.')
    result=subprocess.run(['gh','api','--method','GET',endpoint],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=30)
    if result.returncode: raise RuntimeError('GitHub CLI: '+result.stderr[:1500])
    data=json.loads(result.stdout)
    fields=('login','name','full_name','html_url','number','title','state','body','updated_at','private','description')
    def project(row): return {k:(v[:12000] if isinstance(v,str) else v) for k,v in row.items() if k in fields}
    return [project(row) for row in data] if isinstance(data,list) else project(data)


def health():
    result={}
    for name,args in [('docker',['docker','info','--format','{{.ServerVersion}}']),('github',['gh','api','user','--jq','.login'])]:
        try:
            run=subprocess.run(args,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=10)
            result[name]={'ready':run.returncode==0,'detail':run.stdout.strip()[:100] if run.returncode==0 else 'Non raggiungibile o non autenticato; nessuna credenziale stampata.'}
        except (OSError,subprocess.TimeoutExpired): result[name]={'ready':False,'detail':'Assente o verifica scaduta.'}
    return result


def validate(config):
    if not isinstance(config,dict) or set(config)-{'mcpServers','plugins'}: raise ValueError('Usa solo mcpServers e plugins.')
    servers=config.get('mcpServers',{})
    if not isinstance(servers,dict): raise ValueError('mcpServers deve essere un oggetto.')
    for name,spec in servers.items():
        if not isinstance(name,str) or not re.fullmatch(r'[\w-]{1,80}',name): raise ValueError('Nome server non valido.')
        if not isinstance(spec,dict) or set(spec)-{'command','args','cwd','env'}: raise ValueError('Server stdio: command, args, cwd, env.')
        if not isinstance(spec.get('command'),str) or not spec['command'].strip(): raise ValueError('command mancante.')
        if not isinstance(spec.get('args',[]),list) or not all(isinstance(x,str) for x in spec.get('args',[])): raise ValueError('args deve essere un elenco di stringhe.')
        if 'cwd' in spec and (not isinstance(spec['cwd'],str) or not Path(spec['cwd']).is_dir()): raise ValueError('cwd inesistente.')
        env=spec.get('env',{})
        if not isinstance(env,dict) or not all(isinstance(k,str) and isinstance(v,str) and re.fullmatch(r'\$\{[A-Za-z_][A-Za-z0-9_]*\}',v) for k,v in env.items()):
            raise ValueError('I valori env devono essere riferimenti ${NOME_VARIABILE}, senza credenziali nel file.')
    plugins=config.get('plugins',[])
    if not isinstance(plugins,list) or not all(isinstance(p,str) and (Path(p)/'plugin.json').is_file() for p in plugins): raise ValueError('plugins deve contenere cartelle con plugin.json.')
    return config


def resolve_env(spec):
    spec=dict(spec); env={}
    for key,value in spec.get('env',{}).items():
        match=re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',value)
        if match:
            if match[1] not in os.environ: raise ValueError('Variabile ambiente non impostata: '+match[1])
            env[key]=os.environ[match[1]]
        else: env[key]=value  # Legacy configurations stay compatible; never printed.
    spec['env']=env; return spec


def inventory(agent,home):
    path=Path(home)/'extensions.json'
    config=json.loads(path.read_text(encoding='utf-8-sig')) if path.exists() else {}
    rows=[]
    servers=config.get('mcpServers',{})
    for name in dict.fromkeys([*servers,*agent.extensions.clients]):
        spec=servers.get(name,{})
        client=agent.extensions.clients.get(name)
        missing=[]
        for value in spec.get('env',{}).values():
            match=re.fullmatch(r'\$\{([A-Za-z_][A-Za-z0-9_]*)\}',value)
            if match and match[1] not in os.environ: missing.append(match[1])
        rows.append({'name':name,'configured':name in servers,'process_running':bool(client and client.process.poll() is None),
                     'missing_environment':missing,'verified_tool_calls':'not tracked'})
    return {'servers':rows,'plugins_configured':len(config.get('plugins',[])),
            'plugins_loaded':list(agent.extensions.plugins),'transport':'stdio','config_path':str(path),
            'note':'Un processo avviato non prova che autenticazione e tutte le funzioni remote funzionino.'}


def import_config(path,home,approve):
    path=Path(path).expanduser().resolve()
    if path.stat().st_size>1_000_000: raise ValueError('Configurazione oltre 1 MB.')
    config=validate(json.loads(path.read_text(encoding='utf-8-sig')))
    target=Path(home)/'extensions.json'
    existing=json.loads(target.read_text(encoding='utf-8-sig')) if target.exists() else {}
    merged={'mcpServers':{**existing.get('mcpServers',{}),**config.get('mcpServers',{})},
            'plugins':list(dict.fromkeys(existing.get('plugins',[])+config.get('plugins',[])))}
    if not approve('Importare questi connettori (i comandi possono eseguire codice quando caricati)?\n'+json.dumps(config,ensure_ascii=False,indent=2)): return 'Importazione negata.'
    target.parent.mkdir(parents=True,exist_ok=True)
    if target.exists(): shutil.copy2(target,target.with_suffix('.json.bak'))
    temp=target.with_suffix('.tmp'); temp.write_text(json.dumps(merged,indent=2),encoding='utf-8'); temp.replace(target)
    return 'Configurazione importata. Usa /reload per avviare e verificare i server.'


def panel(agent,home):
    from questions import ask
    import terminal_ui as ui
    ui.write(json.dumps(inventory(agent,home),ensure_ascii=False,indent=2))
    answer=ask('Come vuoi gestire i connettori?', ['Importa configurazione JSON','Avvia/verifica connettori configurati','Torna alla chat']).get('answer')
    if answer=='Importa configurazione JSON':
        path=ask('Quale file JSON vuoi importare? Usa Altro per scrivere il percorso.', ['Annulla','Apri un esempio di configurazione']).get('answer')
        if path=='Apri un esempio di configurazione':
            ui.write(json.dumps({'mcpServers':{'mio-server':{'command':'percorso-eseguibile','args':[], 'env':{'API_KEY':'${MIO_SERVIZIO_API_KEY}'}}},'plugins':[]},indent=2))
        elif path and path!='Annulla': ui.write(import_config(path,home,agent.approve))
    elif answer=='Avvia/verifica connettori configurati': agent.extensions.load(agent)


def query(path,sql,limit=100):
    path=Path(path).resolve()
    if not path.is_file(): raise ValueError('Database inesistente.')
    connection=sqlite3.connect(path.as_uri()+'?mode=ro',uri=True,timeout=5)
    try:
        connection.execute('PRAGMA query_only=ON')
        connection.setlimit(sqlite3.SQLITE_LIMIT_LENGTH,1_000_000)
        connection.setlimit(sqlite3.SQLITE_LIMIT_SQL_LENGTH,100_000)
        allowed={sqlite3.SQLITE_SELECT,sqlite3.SQLITE_READ,sqlite3.SQLITE_FUNCTION,sqlite3.SQLITE_RECURSIVE}
        def authorize(action,first,second,database,trigger):
            if action==sqlite3.SQLITE_FUNCTION and str(second).lower()=='load_extension': return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK if action in allowed else sqlite3.SQLITE_DENY
        connection.set_authorizer(authorize) # query_only alone still permits ATTACH to create files.
        import time
        deadline=time.monotonic()+5
        connection.set_progress_handler(lambda: int(time.monotonic()>deadline),1000)
        cur=connection.execute(sql)
        count=max(1,min(limit,1000))
        rows=[]; size=0; truncated=False
        for row in cur:
            size+=len(json.dumps(row,default=str).encode('utf-8'))
            if len(rows)>=count or size>2_000_000: truncated=True; break
            rows.append(row)
        return {'columns':[d[0] for d in cur.description or []],'rows':rows,'truncated':truncated,'mode':'read-only'}
    finally: connection.close()
