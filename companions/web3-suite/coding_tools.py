"""Independent coding primitives: persistent tasks, notebooks, grep and processes."""
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import threading
import time
import uuid
from contextlib import closing
from reliable_runtime import atomic_bytes,replace_file,LOCK

SPECS=[
 ('tool_search','Find and expose tools by name or description. Loads up to 8 matching schemas on the next model request without executing them.',{'query':'string'},['query']),
 ('schedule_create','Schedule a prompt in this session while CLI is running. delay seconds 1–86400, interval 0 or 60–86400. Expires after 24h; not an OS/cloud scheduler.',{'prompt':'string','delay':'integer','interval':'integer'},['prompt']),
 ('schedule_list','List session scheduled prompts and last dispatch status.',{},[]),
 ('schedule_delete','Cancel session schedule by ID.',{'id':'string'},['id']),
 ('pdf_read','Read selected PDF pages as text; optional page images can be sent with image_read. One-based start, up to 5 pages. Requires PyMuPDF.',{'path':'string','start':'integer','pages':'integer','render':'boolean'},['path']),
 ('lsp_start','Start a user-approved local language server with stdio arguments. If command omitted use installed private Pyright for Python; no downloads during tool calls.',{'command':'string','args':'array'},[]),
 ('lsp_query','Query active language server: definition, references, hover, symbols, diagnostics. Lines one-based, character zero-based UTF-16 offset.',{'path':'string','action':'string','line':'integer','character':'integer','language':'string'},['path','action']),
 ('lsp_close','Stop language server started by this agent.',{},[]),
 ('task_create','Create persistent project task. Completion is declared, not proof of tested code.',{'title':'string','description':'string'},['title']),
 ('task_list','List persistent project tasks, IDs, status and blockers.',{},[]),
 ('task_get','Read task details by ID.',{'id':'string'},['id']),
 ('task_update','Update task status pending/in_progress/completed/blocked and optional dependencies. Cannot complete/start until dependencies complete.',{'id':'string','status':'string','description':'string','dependencies':'array'},['id','status']),
 ('grep','Search regex using local ripgrep. Bounded result, no shell interpolation.',{'pattern':'string','glob':'string','case_sensitive':'boolean'},['pattern']),
 ('notebook_read','Read cells and source from Jupyter notebook without executing code.',{'path':'string','offset':'integer'},['path']),
 ('notebook_edit','Insert, replace or delete one notebook cell, zero-based index. Clears stale outputs of edited code cells. Requires review; never executes cells.',{'path':'string','index':'integer','action':'string','source':'string','cell_type':'string'},['path','index','action']),
 ('process_start','Start approved PowerShell command in background with output file, deadline and tracked PID. Returns immediately. No automatic restart.',{'command':'string','timeout':'integer'},['command']),
 ('process_list','List tracked shell processes. Archived records are not proof that a PID is still the same process.',{},[]),
 ('process_read','Read current process state and output at byte offset.',{'id':'string','offset':'integer'},['id']),
 ('process_stop','Stop only a process owned by this runtime and its child processes. No PID-only recovery/kill after restart.',{'id':'string'},['id']),
 ('file_checkpoints','List native edit checkpoints for this session.',{},[]),
 ('file_rewind','Restore one native edit checkpoint only if no later changes exist. Does not revert shell/API operations.',{'id':'string'},['id']),
 ('tool_output_read','Read paginated output spilled by a tool; offsets in Unicode characters.',{'id':'string','offset':'integer'},['id']),
 ('mcp_resources','List MCP resources or templates on a connected server; optional pagination cursor.',{'server':'string','templates':'boolean','cursor':'string'},['server']),
 ('mcp_resource_read','Read a resource URI on a connected MCP server after approval.',{'server':'string','uri':'string'},['server','uri']),
]
NAMES={s[0] for s in SPECS}


class Tasks:
    def __init__(self,home,root):
        import hashlib
        folder=Path(home)/'tasks'; folder.mkdir(parents=True,exist_ok=True)
        self.path=folder/(hashlib.sha256(str(Path(root).resolve()).encode()).hexdigest()[:24]+'.sqlite')
        with closing(sqlite3.connect(self.path)) as db, db: db.execute('CREATE TABLE IF NOT EXISTS tasks(id TEXT PRIMARY KEY, data TEXT)')

    def apply(self,name,args):
        with LOCK,closing(sqlite3.connect(self.path)) as db, db:
            db.execute('BEGIN IMMEDIATE')
            rows={key:json.loads(value) for key,value in db.execute('SELECT id,data FROM tasks')}
            if name=='task_list': return list(rows.values())
            if name=='task_create':
                if not args['title'].strip(): raise ValueError('Titolo vuoto.')
                ident=uuid.uuid4().hex[:12]
                row={'id':ident,'title':args['title'],'description':args.get('description',''),'status':'pending','dependencies':[]}
            else:
                ident=args['id']
                if ident not in rows: raise ValueError('ID sconosciuto. Consulta task_list.')
                row=dict(rows[ident])
                if name=='task_get': return row
                if args['status'] not in ('pending','in_progress','completed','blocked'): raise ValueError('Stato non valido.')
                dependencies=args.get('dependencies',row['dependencies'])
                if not isinstance(dependencies,list) or any(not isinstance(d,str) or d not in rows or d==ident for d in dependencies): raise ValueError('Dipendenze non valide: usa gli ID di task_list.')
                def reaches(key,visited):
                    if key==ident: return True
                    if key in visited: return False
                    visited.add(key)
                    return any(reaches(d,visited) for d in rows[key]['dependencies'])
                if any(reaches(d,set()) for d in dependencies): raise ValueError('Dipendenza ciclica rifiutata.')
                if args['status'] in ('in_progress','completed') and any(rows[d]['status']!='completed' for d in dependencies): raise ValueError('Completa prima le dipendenze.')
                if row['status']=='completed' and args['status']!='completed' and any(ident in r['dependencies'] and r['status'] in ('in_progress','completed') for r in rows.values()): raise ValueError('Riapri prima i task dipendenti.')
                row.update(status=args['status'],dependencies=dependencies)
                if 'description' in args: row['description']=args['description']
            row['updated']=time.time()
            db.execute('INSERT OR REPLACE INTO tasks VALUES(?,?)',(ident,json.dumps(row,ensure_ascii=False)))
            return row


class Processes:
    def __init__(self,home):
        self.folder=Path(home)/'processes'; self.folder.mkdir(parents=True,exist_ok=True)
        self.live={}; self.lock=threading.RLock()

    def persist(self,row): atomic_bytes(self.folder/(row['id']+'.json'),json.dumps(row).encode())

    def start(self,command,root,timeout=600):
        with self.lock:
            if sum(p.poll() is None for p,r in self.live.values())>=4: raise ValueError('Massimo 4 processi in background.')
            ident=uuid.uuid4().hex[:12]; log=self.folder/(ident+'.log')
            timeout=max(1,min(int(timeout),3600))
            log.write_text('',encoding='utf-8')
            from command_environment import environment
            proc=subprocess.Popen(['pwsh','-NoProfile','-Command',command],cwd=root,stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,env=environment(),creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            from secret_output import collect_to_log
            output_stop=threading.Event()
            output_thread=threading.Thread(target=collect_to_log,args=(proc,log,output_stop),daemon=True,name='process-output-'+ident)
            output_thread.start()
            row={'id':ident,'pid':proc.pid,'state':'running','root':str(root),'output':str(log),'started':time.time(),'timeout':timeout,'exit_code':None}
            self.live[ident]=(proc,row); self.persist(row)
        def monitor():
            try:
                deadline=time.monotonic()+timeout
                while proc.poll() is None:
                    if log.stat().st_size>50_000_000: self.stop(ident,'output_limit_reached'); return
                    if time.monotonic()>=deadline: raise subprocess.TimeoutExpired(command,timeout)
                    time.sleep(.1)
                with self.lock:
                    output_thread.join(timeout=.2);output_stop.set()
                    if row['state']=='running': row['state']='completed' if proc.returncode==0 else 'failed'
                    row['exit_code']=proc.returncode
            except subprocess.TimeoutExpired: self.stop(ident,'timed_out')
            finally:
                with self.lock: row['exit_code']=proc.poll(); self.persist(row)
        threading.Thread(target=monitor,daemon=True,name='shell-'+ident).start()
        return dict(row)

    def stop(self,ident,state='cancelled'):
        with self.lock:
            if ident not in self.live: raise ValueError('Processo non posseduto da questa esecuzione; non termino un PID archiviato.')
            proc,row=self.live[ident]
            if proc.poll() is None:
                subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=10)
                proc.wait(timeout=5); row['state']=state
            row['exit_code']=proc.poll(); self.persist(row); return dict(row)

    def rows(self):
        with self.lock:
            result=[]
            for path in self.folder.glob('*.json'):
                row=json.loads(path.read_text())
                if row['id'] in self.live: row=dict(self.live[row['id']][1])
                elif row['state']=='running': row['state']='unknown_after_restart'
                result.append(row)
            return result[-100:]

    def read(self,ident,offset=0):
        if not isinstance(ident,str) or len(ident)!=12 or any(c not in '0123456789abcdef' for c in ident): raise ValueError('ID processo non valido.')
        row=next((r for r in self.rows() if r['id']==ident),None)
        if not row: raise ValueError('ID sconosciuto: consulta process_list.')
        offset=max(0,int(offset))
        with (self.folder/(ident+'.log')).open('rb') as stream: stream.seek(offset); data=stream.read(24000)
        return {'process':row,'output':data.decode('utf-8',errors='replace'),'next_offset':offset+len(data)}

    def close(self):
        for ident in list(self.live): self.stop(ident)


def dispatch(agent,name,args,home):
    if name=='tool_search':
        from tool_routing import search_score
        rows=agent.catalog_tools+agent.extensions.definitions
        ranked=sorted(rows,key=lambda row:search_score(row['function'],args['query']),reverse=True)
        chosen=[row for row in ranked if search_score(row['function'],args['query'])>0][:8]
        agent.enabled_tools.update(row['function']['name'] for row in chosen)
        return {'loaded':[row['function'] for row in chosen]}
    if name.startswith('schedule_'):
        from session_schedule import Schedule
        scheduler=Schedule(agent.session)
        if name=='schedule_list': return list(scheduler.read().values())
        if agent.depth: raise ValueError('Pianificazioni solo nel coordinatore.')
        if not agent.approve(name+' '+json.dumps(args,ensure_ascii=False)): return {'denied':True}
        if name=='schedule_create': return scheduler.create(args['prompt'],args.get('delay',60),args.get('interval',0))
        return scheduler.delete(args['id'])
    if name=='pdf_read':
        import pymupdf
        path=agent.path(args['path'])
        if path.stat().st_size>50_000_000: raise ValueError('PDF oltre 50 MB.')
        with pymupdf.open(path) as document:
            if document.needs_pass: raise ValueError('PDF protetto da password.')
            start=max(1,args.get('start',1)); count=max(1,min(5,args.get('pages',1))); pages=[]
            for index in range(start-1,min(len(document),start-1+count)):
                page=document[index]; row={'page':index+1,'text':page.get_text()[:30000]}
                if args.get('render'):
                    folder=agent.session.with_suffix('.pdf-images'); folder.mkdir(parents=True,exist_ok=True)
                    image=folder/(uuid.uuid4().hex+'.png'); scale=min(1.5,1600/max(page.rect.width,page.rect.height))
                    page.get_pixmap(matrix=pymupdf.Matrix(scale,scale)).save(image); row['image_path']=str(image)
                pages.append(row)
            return {'pages':pages,'total_pages':len(document),'note':'Nessun OCR implicito; le scansioni possono richiedere image_read.'}
    if name=='lsp_start':
        if agent.language_server.process and agent.language_server.process.poll() is None:
            return {'already_running':True,'next_tool':'lsp_query','instruction':'Server funzionante preservato. Usa lsp_close solo se devi esplicitamente riconfigurarlo.'}
        command=args.get('command'); argv=args.get('args',[])
        default_server=command is None
        if command is None:
            import shutil
            entry=Path(home)/'language-servers'/'node_modules'/'pyright'/'dist'/'pyright-langserver.js'
            command=shutil.which('node')
            if not command or not entry.is_file(): raise ValueError('Pyright predefinito assente. Specifica command e args di un language server installato.')
            argv=[str(entry),'--stdio']
        if not agent.approve('Avviare language server: '+json.dumps([command,*argv])): return {'denied':True}
        result=agent.language_server.start(command,argv,agent.root)
        if default_server:
            import sys
            agent.language_server.notify('workspace/didChangeConfiguration',{'settings':{'python':{'pythonPath':sys.executable,'analysis':{'diagnosticMode':'openFilesOnly','typeCheckingMode':'basic'}}}})
        return result
    if name=='lsp_close': agent.language_server.close(); return {'closed':True}
    if name=='lsp_query': return agent.language_server.query(agent.path(args['path']),args['action'],args.get('line',1),args.get('character',0),args.get('language'))
    if name.startswith('task_'): return Tasks(home,agent.root).apply(name,args)
    if name=='grep':
        import shutil
        if not shutil.which('rg'): raise ValueError('ripgrep non installato: usa search per ricerca letterale.')
        argv=['rg','--json','--max-count','150','--max-filesize','2M','--glob','!node_modules/**','--glob','!.git/**']
        if not args.get('case_sensitive',False): argv.append('-i')
        if args.get('glob'): argv+=['--glob',args['glob']]
        argv+=['--',args['pattern'],str(agent.root)]
        # Disk capture bounds memory; result is then available in paginated journal output.
        import tempfile
        with tempfile.TemporaryFile() as log:
            result=subprocess.run(argv,stdout=log,stderr=subprocess.STDOUT,timeout=20)
            log.seek(0); raw=log.read(2_000_001)
        if result.returncode not in (0,1): raise ValueError(raw[:1000].decode(errors='replace'))
        return {'matches_jsonl':raw[:2_000_000].decode('utf-8',errors='replace'),'truncated':len(raw)>2_000_000,'exit_code':result.returncode}
    if name.startswith('notebook_'):
        path=agent.path(args['path'])
        if path.stat().st_size>10_000_000: raise ValueError('Notebook oltre 10 MB.')
        raw=path.read_bytes(); notebook=json.loads(raw)
        if notebook.get('nbformat')!=4 or not isinstance(notebook.get('cells'),list): raise ValueError('Richiesto notebook nbformat 4.')
        cells=notebook['cells']
        if name=='notebook_read':
            offset=max(0,args.get('offset',0))
            return {'cells':[{'index':i,'cell_type':c['cell_type'],'source':c['source']} for i,c in enumerate(cells) if offset<=i<offset+20],'total':len(cells)}
        index=args['index']; action=args['action']
        if action not in ('insert','replace','delete') or not 0<=index<(len(cells)+int(action=='insert')): raise ValueError('Azione/indice non valido: consulta notebook_read.')
        if action=='delete': cells.pop(index)
        else:
            kind=args.get('cell_type',cells[index]['cell_type'] if action=='replace' else 'code')
            if kind not in ('code','markdown','raw') or 'source' not in args: raise ValueError('cell_type code/markdown/raw e source richiesti.')
            cell=dict(cells[index]) if action=='replace' else {'id':uuid.uuid4().hex[:8],'metadata':{}}
            cell.update(cell_type=kind,source=args['source'].splitlines(True))
            if kind=='code': cell.update(outputs=[],execution_count=None)
            else: cell.pop('outputs',None); cell.pop('execution_count',None)
            if action=='insert': cells.insert(index,cell)
            else: cells[index]=cell
        result=replace_file(path,raw,json.dumps(notebook,ensure_ascii=False,indent=1).encode(),agent.session,agent.approve)
        if not result.get('denied') and action!='delete':
            result.update(cell_index=index,cell_type=cells[index]['cell_type'],source=cells[index]['source'])
            try: encoded=json.loads(args['source'])
            except (ValueError,TypeError): encoded=None
            if isinstance(encoded,list) and all(isinstance(s,str) for s in encoded):
                result['warning']='Il source è una espressione lista JSON/Python, NON il testo delle righe contenute. Verifica il significato richiesto; source deve contenere codice grezzo, non righe serializzate.'
        return result
    if name.startswith('process_'):
        manager=agent.process_manager()
        if name=='process_list': return manager.rows()
        if name=='process_read': return manager.read(args['id'],args.get('offset',0))
        if not agent.approve(name+' '+json.dumps(args,ensure_ascii=False)): return {'denied':True}
        if name=='process_start':
            from node_preflight import script_paths,check_script
            from work_ledger import load as task_ledger
            for script in script_paths(args['command'],agent.root):check_script(script,agent.root,task_ledger(agent.session).get('token_mint'))
            return manager.start(args['command'],agent.root,args.get('timeout',600))
        return manager.stop(args['id'])
    if name in ('file_checkpoints','file_rewind','tool_output_read'):
        import reliable_runtime as rt
        if name=='file_checkpoints': return rt.checkpoints(agent.session)
        if name=='file_rewind': return rt.rewind(agent.session,args['id'],agent.root,agent.approve)
        return rt.output_read(agent.session,args['id'],args.get('offset',0))
    if name.startswith('mcp_resource'):
        client=agent.extensions.clients.get(args['server'])
        if not client: raise ValueError('Server non connesso. Consulta connections_status e /reload.')
        if name=='mcp_resources': return client.request('resources/templates/list' if args.get('templates') else 'resources/list',{'cursor':args['cursor']} if args.get('cursor') else {})
        if not agent.approve('Leggere risorsa MCP '+args['server']+' '+args['uri']): return {'denied':True}
        return client.request('resources/read',{'uri':args['uri']})
    raise ValueError('Strumento sconosciuto.')
