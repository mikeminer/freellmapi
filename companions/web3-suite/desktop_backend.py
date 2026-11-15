"""Desktop operations use the same authenticated Remote coordinator as Seeker."""
import base64,hashlib,json,subprocess,sys,threading,uuid
from pathlib import Path
from remote_client import RemoteClient
from memory_vault import redact

class DesktopBackend:
    def __init__(self,home):
        self.home=Path(home);self.client=RemoteClient(home,'desktop');self.files={};self.markdown_cache={};self.lock=threading.RLock();self.speech=None
        self.draft_path=self.client.folder/'desktop-files.json'
        if self.draft_path.exists():
            try:self.files={k:{**v,'path':Path(v['path'])} for k,v in json.loads(self.draft_path.read_text(encoding='utf-8')).items()}
            except (OSError,ValueError):self.files={}
    def connect(self):
        state=self.client.connect(auto_start=True)
        if 'local_commands' not in state.get('capabilities',[]):raise RuntimeError('Coordinatore precedente: aggiorna il bridge prima di usare FREELLMAPI Desktop.')
        from chat_library import Library
        library=Library(self.home)
        if library.path.exists():library.sync()
        return self.format(state)
    def format(self,state):
        from markdown_it import MarkdownIt
        parser=MarkdownIt('commonmark',{'html':False,'breaks':True})
        parser.disable('image')
        state=dict(state);state['messages']=[dict(m) for m in state.get('messages',[]) if (m.get('content') or '').strip()]
        from chat_library import Library
        from session_store import Sessions
        library=Library(self.home);rows=Sessions(self.home).list(include_deleted=True)
        state['sessions']=[r for r in rows if not r.get('deleted_at')]
        state['deleted_sessions']=[r for r in rows if r.get('deleted_at')]
        state['projects']=library.projects()
        sync=self.home/'sessions/library-vault-sync.json'
        try:state['vault_links']=json.loads(sync.read_text(encoding='utf-8')) if sync.exists() else {}
        except (OSError,ValueError):state['vault_links']={'state':'unavailable'}
        state['draft_files']=[{k:v for k,v in row.items() if k not in ('path','session')}|{'id':ident} for ident,row in self.files.items() if row.get('session')==state.get('session')]
        for m in state['messages']:
            text=redact(m.get('content') or '');key=hashlib.sha256(text.encode()).hexdigest()
            if key not in self.markdown_cache:self.markdown_cache[key]=parser.render(text)
            m['html']=self.markdown_cache[key]
        if len(self.markdown_cache)>200:self.markdown_cache={}
        state['connection_url']=self.client.metadata['url'];return state
    def state(self):return self.format(self.client.state())
    def command(self,text,files=()):
        ids=[]
        for ident in files:
            item=self.files.get(ident)
            if not item:raise ValueError('Allegato non selezionato in questa finestra.')
            if item.get('session')!=self.client.last_state.get('session'):raise ValueError('Allegato selezionato in un’altra sessione: rileggi prima di inviare.')
            path=item['path'];raw=path.read_bytes()
            if len(raw)>20_000_000:raise ValueError('Allegato oltre 20 MB.')
            if 'attachments' not in self.client.last_state.get('capabilities',[]):raise ValueError('Aggiorna il coordinatore prima di inviare allegati.')
            uploaded=self.client.request('/v1/attachment',{'session':self.client.last_state['session'],'name':item['name'],'data':base64.b64encode(raw).decode()},timeout=40)
            ids.append(uploaded['id'])
        result=self.client.send(text or 'Analizza i file allegati.',ids)
        if result.get('state')!='unconfirmed':
            for ident in files:self.files.pop(ident,None)
            self.save_files()
        return result
    def add_files(self,paths):
        from attachment_store import IMAGES,TEXT
        result=[]
        for raw in paths[:8]:
            path=Path(raw).resolve();ext=path.suffix.casefold()
            display_name=path.name
            if not path.is_file() or path.stat().st_size>20_000_000:raise ValueError('File assente o oltre 20 MB.')
            if ext in ('.wav','.mp3','.m4a','.ogg','.flac','.webm'):
                spoken=self.transcribe(path);folder=self.home/'desktop/transcripts';folder.mkdir(parents=True,exist_ok=True)
                transcript=folder/(uuid.uuid4().hex+'.txt');transcript.write_text('Trascrizione locale di '+path.name+'\n\n'+spoken['text'],encoding='utf-8')
                display_name=path.name+'.trascrizione.txt';path=transcript;ext='.txt'
            if ext not in IMAGES|TEXT|{'.pdf','.docx'}:raise ValueError('Formato non supportato: '+path.name)
            ident=uuid.uuid4().hex
            row={'id':ident,'name':display_name,'bytes':path.stat().st_size,'image':ext in IMAGES}
            if ext in IMAGES:
                from vision_input import attachment
                row['preview']=attachment(path)['image_url']['url']
            self.files[ident]={**row,'path':path,'session':self.client.last_state.get('session')}
            result.append(row)
        self.save_files()
        return result
    def save_files(self):
        from reliable_runtime import atomic_bytes
        atomic_bytes(self.draft_path,json.dumps(self.files,default=str).encode())
    def drop_file(self,ident):self.files.pop(ident,None);self.save_files();return True
    def last_input(self):
        p=self.client.folder/(self.client.kind+'-last-input.json')
        return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {'text':''}
    def library(self,data):
        from chat_library import Library
        library=Library(self.home);action=data.get('action');sid=data.get('session')
        if action=='create_project':return library.create(data.get('name'),data.get('root') or self.client.state()['project'],data.get('parent'))
        if action=='rename_project':library.edit_project(data.get('project'),name=data.get('name'))
        elif action=='delete_project':library.edit_project(data.get('project'),delete=True)
        elif action=='move_project':library.move_project(data.get('project'),data.get('parent'))
        elif action=='sync_vault':return library.sync()
        elif action=='move_many':library.move_many(data.get('sessions'),data.get('project'))
        elif action=='new_project_chat':
            project=library.project(data.get('project'));new=self.local_switch('/project '+project['root'])
            library.chat(new,'move',project=project['id']);return {'session':new}
        elif action in ('rename','move','delete','restore'):
            if action=='delete' and self.client.state()['session']==sid:self.local_switch('/new')
            library.chat(sid,action,name=data.get('name'),project=data.get('project'))
        else:raise ValueError('Azione libreria sconosciuta.')
        return {'ok':True,'vault_sync':getattr(library,'last_sync',None)}
    def local_switch(self,command):
        import time
        if self.client.state().get('busy'):raise ValueError('Attendi la fine del turno prima di cambiare la chat attiva.')
        receipt=self.client.send(command)
        if receipt.get('state')=='unconfirmed':raise RuntimeError('Cambio chat non confermato: verifica la ricevuta. Nessun reinvio.')
        until=time.monotonic()+20
        while time.monotonic()<until:
            state=self.client.request('/v1/state?request_id='+receipt['request_id']);exact=state.get('request_receipt') or {};row=exact.get('receipt') or {}
            if row.get('state')=='finished':
                if row.get('outcome')!='completed':raise ValueError('Cambio chat non completato; contenuto conservato.')
                self.client.last_state=state;return state['session']
            time.sleep(.1)
        raise TimeoutError('Cambio chat ancora da verificare. Nessuna conversazione eliminata o operazione reinviata.')
    def open_connector(self,name):
        if name=='hyperliquid':
            from session_store import Sessions
            from types import SimpleNamespace
            import hyperliquid_tools
            state=self.client.state();session=Sessions(self.home).path(state['session'])
            result=hyperliquid_tools.panel(SimpleNamespace(session=session,interactive=True,depth=0),self.home)
        elif name=='solana':
            import solana_tools
            result=solana_tools.panel(self.home)
        else:raise ValueError('Connettore non disponibile.')
        if not result.get('panel_opened'):raise RuntimeError('Connettore non avviato: '+str(result.get('code','errore locale')))
        return {'panel_opened':True,'connector':name,'pid':result.get('pid')}
    def workspace_action(self,area,action):
        solana={
            'balance':'Leggi saldo SOL, token SPL/Token-2022 e rete del profilo Solana già collegato, usando solana_status e solana_read. Solo letture; nessuna transazione o richiesta di chiavi.',
            'rpc':'Verifica rete e RPC Solana/Helius del profilo salvato con gli strumenti nativi. Non mostrare URL privati o credenziali. Nessuna firma o transazione.',
            'token':'Analizza un token Solana o Pump.fun con letture, quotazione e simulazione. Chiedi il mint mancante con ask_user, una domanda alla volta. Non inviare transazioni.',
            'receipts':'Mostra le operazioni e ricevute Solana/Pump.fun esistenti, distinguendo confermate, fallite e incerte. Solo letture: non firmare, reinviare o creare nuove transazioni.'}
        game={
            'new':'Avvia un nuovo gioco con DevFridge Game Builder nella cartella corrente. Raccogli solo le scelte mancanti con ask_user, una domanda alla volta e risposta aperta alternativa. Non rifare domande già risolte. Poi procedi secondo il brief confermato e conserva i checkpoint.',
            'resume':'Riprendi il gioco di questa chat e cartella con DevFridge Game Builder. Leggi stato e checkpoint e verifica i file reali. Continua dal punto raggiunto, senza ricominciare o rifare domande già risolte.',
            'art':'Usa DevFridge Game Builder per migliorare grafica e asset 3D del gioco corrente: silhouette, materiali, luci e prestazioni su Seeker. Conserva gameplay e contesto e verifica il risultato in esecuzione.',
            'apk':'Usa DevFridge Game Builder per verificare il progetto corrente, compilare l’APK e collaudarlo sul Seeker collegato. Riusa le scelte già confermate e le build valide; non ricominciare e non dichiarare installato ciò che non hai verificato.'}
        if area=='solana' and action in solana:return self.command(solana[action])
        if area=='game' and action in game:
            from skills_manager import Skills
            skills=Skills(self.home);matches=[row for row in skills.list() if str(row.get('name','')).startswith('devfridge-game-builder')]
            if not matches:raise ValueError('DevFridge Game Builder non è installato nel profilo FREELLMAPI corrente.')
            latest=max(matches,key=lambda row:(skills.path(row['name'])/'source.json').stat().st_mtime)
            return self.command('/skill use '+latest['name']+' '+game[action])
        raise ValueError('Azione non disponibile.')
    def transcribe(self,path):
        with self.lock:
            if not self.speech or self.speech.poll() is not None:
                import os
                python=Path(sys.executable).with_name('python.exe')
                with (self.client.folder/'speech.log').open('a',encoding='utf-8') as log:
                    self.speech=subprocess.Popen([str(python),'-X','utf8','-u',str(Path(__file__).parent/'desktop/speech_worker.py')],stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=log,text=True,encoding='utf-8',creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                ready=json.loads(self.speech.stdout.readline())
                if not ready.get('ready'):raise RuntimeError(ready.get('error') or 'Riconoscimento vocale locale non pronto.')
            ident=uuid.uuid4().hex;self.speech.stdin.write(json.dumps({'id':ident,'path':str(path)})+'\n');self.speech.stdin.flush()
            result=json.loads(self.speech.stdout.readline())
            if result.get('error'):raise RuntimeError(result['error'])
            return result
    def close(self):
        if self.speech:
            if self.speech.poll() is None:
                self.speech.terminate()
                try:self.speech.wait(timeout=3)
                except subprocess.TimeoutExpired:self.speech.kill();self.speech.wait(timeout=3)
            if self.speech.stdin:self.speech.stdin.close()
            if self.speech.stdout:self.speech.stdout.close()
