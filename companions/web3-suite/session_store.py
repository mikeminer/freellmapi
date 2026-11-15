"""Independent durable conversations, with a read-only migration of legacy history."""
import hashlib
import json
from pathlib import Path
import uuid
import threading
import time
from functools import wraps
from datetime import datetime, timezone

SESSION_LOCK=threading.RLock()

def serialized(function):
    @wraps(function)
    def locked(*args,**kwargs):
        with SESSION_LOCK:return function(*args,**kwargs)
    return locked


class Sessions:
    def __init__(self, home):
        self.home=Path(home)/'sessions'
        self.folder=self.home/'conversations'
        self.folder.mkdir(parents=True,exist_ok=True)

    @serialized
    def atomic(self,path,data):
        tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        try:
            tmp.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf-8')
            for attempt in range(3):
                try:tmp.replace(path);return
                except PermissionError:
                    if attempt==2:raise
                    time.sleep(.03*(attempt+1))
        finally:tmp.unlink(missing_ok=True)

    def path(self,ident):
        if not isinstance(ident,str) or len(ident)!=32 or any(c not in '0123456789abcdef' for c in ident):
            raise ValueError('ID sessione non valido.')
        return self.folder/(ident+'.json')

    def metadata(self,ident):
        return self.path(ident).with_suffix('.meta.json')

    @serialized
    def create(self,root,messages=None,ident=None,title=None):
        ident=ident or uuid.uuid4().hex
        if self.path(ident).exists(): return ident
        self.atomic(self.path(ident),messages or [])
        self.update(self.path(ident),root,messages or [],title)
        return ident

    @serialized
    def update(self,path,root,messages,title=None,active_skills=None):
        ident=Path(path).stem; meta=self.metadata(ident)
        try: data=json.loads(meta.read_text(encoding='utf-8'))
        except (OSError,ValueError): data={}
        stamp=datetime.now(timezone.utc).isoformat()
        if title is not None:data['custom_title']=True
        first=next((m.get('content','') for m in messages if m.get('role')=='user' and isinstance(m.get('content'),str)),'')
        from memory_vault import redact
        data.update(id=ident,root=str(Path(root).resolve()),updated=stamp,created=data.get('created',stamp),
                    title=title or (data.get('title') if data.get('custom_title') else None) or (redact(' '.join(first.split()))[:90] if first else data.get('title','Nuova sessione')),
                    messages=len(messages))
        if active_skills is not None:data['active_skills']=list(active_skills)[:3]
        self.atomic(meta,data)
        if title is not None:
            from chat_library import Library
            library=Library(self.home.parent)
            if library.path.exists():library.chat(ident,'rename',name=title)

    @serialized
    def migrate(self,root):
        root=Path(root).resolve()
        legacy=self.home/(hashlib.sha256(str(root).encode()).hexdigest()[:16]+'.json')
        if not legacy.exists(): return
        ident=hashlib.sha256(('legacy:'+str(legacy)).encode()).hexdigest()[:32]
        if self.path(ident).exists(): return
        messages=json.loads(legacy.read_text(encoding='utf-8'))
        self.validate(messages)
        self.create(root,messages,ident,title='Sessione precedente · '+root.name)

    @serialized
    def list(self,root=None,include_deleted=False):
        result=[]
        for path in self.folder.glob('*.meta.json'):
            try:
                data=json.loads(path.read_text(encoding='utf-8'))
                if not self.path(data['id']).exists(): continue
                if root and Path(data['root']).resolve()!=Path(root).resolve(): continue
                result.append(data)
            except (ValueError,KeyError,OSError): continue
        from chat_library import Library
        return sorted(Library(self.home.parent).overlay(result,include_deleted),key=lambda d:d['updated'],reverse=True)

    def validate(self,messages):
        if not isinstance(messages,list) or any(not isinstance(m,dict) or m.get('role') not in ('user','assistant','tool','system') for m in messages):
            raise ValueError('Cronologia sessione danneggiata: nessun file modificato.')

    @serialized
    def activate(self,agent,ident):
        if agent.jobs and any(j.get('thread') and j['thread'].is_alive() for j in agent.jobs.jobs.values()):
            raise ValueError('Ci sono subagenti ancora attivi. Attendi la fine oppure annullali con /jobs cancel ID prima di cambiare sessione.')
        path=self.path(ident)
        metadata=json.loads(self.metadata(ident).read_text(encoding='utf-8'))
        root=Path(metadata['root']).resolve()
        if not root.is_dir(): raise ValueError('Cartella del progetto non disponibile: '+str(root))
        messages=json.loads(path.read_text(encoding='utf-8')); self.validate(messages)
        if getattr(agent,'processes',None) and any(p.poll() is None for p,r in agent.processes.live.values()):
            raise ValueError('Processi shell ancora attivi: usa process_stop o attendi prima di cambiare sessione.')
        old_lease=getattr(agent,'coordinator_lease',None);new_lease=None
        if old_lease and old_lease.session!=path:
            from session_lease import SessionLease
            new_lease=SessionLease(path)
        # Never replay a tool whose outcome was lost when the terminal closed.
        repaired=[]
        for index,msg in enumerate(messages):
            repaired.append(msg)
            if msg.get('tool_calls'):
                following=[]
                for nxt in messages[index+1:]:
                    if nxt.get('role')!='tool': break
                    following.append(nxt.get('tool_call_id'))
                for call in msg['tool_calls']:
                    if call['id'] not in following:
                        repaired.append({'role':'tool','tool_call_id':call['id'],'content':'Sessione interrotta: esito non registrato. Non rieseguire automaticamente; verifica lo stato reale prima di procedere.'})
        agent.browser.close()
        if hasattr(agent,'language_server'): agent.language_server.close()
        if hasattr(agent,'read_versions'): agent.read_versions.clear()
        if hasattr(agent,'debugger'): agent.debugger.close()
        if hasattr(agent,'pending_images'): agent.pending_images.clear()
        from secret_output import redact
        for message in repaired:
            if isinstance(message.get('content'),str):message['content']=redact(message['content'])
        agent.root=root; agent.session=path; agent.messages=repaired; agent.sessions=self
        if new_lease:agent.coordinator_lease=new_lease;old_lease.close()
        from questions import answered_questions
        try:
            answers=json.loads(path.with_suffix('.answers.json').read_text(encoding='utf-8'))
            agent.question_answers=answers if isinstance(answers,dict) else {}
        except (OSError,ValueError): agent.question_answers={}
        agent.question_answers.update(answered_questions(repaired))
        agent.jobs=None; agent.last_context=None; agent.handoff=''; agent.context_needs_inspection=True
        agent.context_recovery_pending=False
        agent._memory_scope=None; agent._memory_scope_for=None; agent._memory_turn=None
        agent.active_skills.clear(); agent.skill_discovery=''
        for name in metadata.get('active_skills',[])[:3]:
            try:agent.tool('skill_load',{'name':name})
            except (OSError,ValueError):pass
        if agent.vault_root:
            from memory_vault import Vault
            agent.memory=Vault(agent.vault_root/'freellmapi-memory',root)
            from semantic_memory import configure
            configure(agent)
        self.update(path,root,repaired,metadata.get('title') if not repaired and metadata.get('custom_title') else None)
        from remote_access import active
        remote=active()
        if remote and remote.local_pairing and remote.snapshot().get('session')==ident:remote.local_pairing.update_session(ident)
        return metadata


def choose(store):
    from questions import ask
    import terminal_ui as ui
    rows=store.list()
    if not rows: ui.write('Nessuna sessione salvata.'); return None
    page=0
    while True:
        batch=rows[page:page+5]
        labels=[f"{r['updated'][:16].replace('T',' ')} UTC · {r['title']} · {Path(r['root']).name} · {r['id'][:8]}" for r in batch]
        answer=ask('Scegli la sessione da riprendere',labels+['Pagina successiva','Annulla']).get('answer')
        if not answer or answer=='Annulla': return None
        if answer=='Pagina successiva': page=page+5 if page+5<len(rows) else 0; continue
        if answer in labels: return batch[labels.index(answer)]['id']
        if any(r['id']==answer for r in rows): return answer
        ui.write('Seleziona una sessione dall’elenco oppure inserisci il suo ID completo.')


def startup(agent,store,resume=False,new=False,ident=None):
    from questions import ask
    store.migrate(agent.root)
    if ident: return store.activate(agent,ident)
    if new: return store.activate(agent,store.create(agent.root))
    recent=store.list(agent.root)
    if resume: return store.activate(agent,recent[0]['id'] if recent else store.create(agent.root))
    if not agent.interactive: return store.activate(agent,store.create(agent.root))
    while True:
        answer=ask('freellmapi · sessioni', ['Riprendi ultima sessione','Nuova sessione','Scegli una sessione']).get('answer')
        if not answer: return None
        if answer=='Nuova sessione': return store.activate(agent,store.create(agent.root))
        if answer=='Riprendi ultima sessione':
            all_rows=store.list()
            return store.activate(agent,(recent or all_rows)[0]['id'] if recent or all_rows else store.create(agent.root))
        if answer=='Scegli una sessione':
            selected=choose(store)
            if selected: return store.activate(agent,selected)
