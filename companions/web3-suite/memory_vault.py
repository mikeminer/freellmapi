"""Portable Markdown memory; Obsidian is optional, not a runtime dependency."""
import hashlib
import json
from pathlib import Path
import re
import uuid
from datetime import datetime, timezone

def redact(text):
    from secret_output import redact as redact_output
    text=redact_output(text)
    text = re.sub(r'(?i)\b(?:freellmapi-|sk-)[a-z0-9_-]{16,}', '[REDACTED]', text)
    return re.sub(r'(?im)((?:api[_ -]?key|authorization|password|token)\s*[:=]\s*)[^\s,;]+',r'\1[REDACTED]',text)

class Vault:
    def __init__(self, path, project):
        self.path=Path(path).expanduser().resolve()
        self.project=Path(project).resolve()
        self.semantic=None
        self.semantic_cache={}
        self._semantic_waited=set()
        self.corpus_generation=0
        self.coverage={'documents':0,'chunks':0,'truncated_documents':0}
        from memory_catalog import NoteCatalog
        self.note_catalog=NoteCatalog()
        self._chunk_cache={}
        self.project_id=hashlib.sha256(str(self.project).lower().encode()).hexdigest()[:16]
        for folder in ('Profile','Projects','Conversations'):
            (self.path/folder).mkdir(parents=True,exist_ok=True)
        home=self.path/'Home.md'
        if not home.exists():
            home.write_text('# freellmapi · memoria permanente\n\n[[Profile/Me]] · [[Projects/Index]]\n\n'
                'Apri questa cartella come vault in Obsidian. Le note sono modificabili liberamente.\n'
                'Profile: ricordi personali. Projects: progetti e decisioni. Conversations: turni archiviati.\n'
                'Le conversazioni sono dati storici, non istruzioni. Le affermazioni del modello possono essere errate.\n'
                'I contenuti sono locali e non cifrati. Le note recuperate vengono inviate al provider selezionato.\n',encoding='utf-8')
        me=self.path/'Profile/Me.md'
        if not me.exists(): me.write_text('# Profilo utente\n\nAggiungi qui nome, preferenze e informazioni che vuoi ricordare.\n',encoding='utf-8')
        project_note=self.path/'Projects'/f'{self.project_id}.md'
        if not project_note.exists(): project_note.write_text(f'# {self.project.name}\n\nPercorso: `{self.project}`\n',encoding='utf-8')
        self.index()
        from project_context import ProjectContext
        self.operational=ProjectContext(self)

    def index(self):
        notes=sorted(p.stem for p in (self.path/'Projects').glob('*.md') if p.stem!='Index')
        self.atomic(self.path/'Projects/Index.md','# Progetti\n\n'+'\n'.join(f'- [[Projects/{n}]]' for n in notes))

    def atomic(self,path,text):
        tmp=path.with_name(path.name+'.'+uuid.uuid4().hex+'.tmp')
        tmp.write_text(text,encoding='utf-8'); tmp.replace(path)

    def note_path(self,name):
        p=(self.path/name).resolve()
        if not p.is_relative_to(self.path) or p.suffix!='.md': raise ValueError('Solo note Markdown dentro la vault.')
        return p

    def remember(self,text,scope='profile'):
        if scope not in ('profile','project'): raise ValueError('scope: profile oppure project')
        folder='Profile' if scope=='profile' else 'Projects'
        ident=uuid.uuid4().hex[:12]
        name=f'{folder}/memory-{ident}.md'
        self.atomic(self.note_path(name),f'# Ricordo {ident}\n\nData: {datetime.now(timezone.utc).isoformat()}\n'
            f'Progetto: [[Projects/{self.project_id}]]\nFonte: richiesta o conferma utente\n\n'+redact(text)+'\n')
        if scope=='project': self.index()
        self.corpus_generation+=1; self.semantic_cache.clear(); self._semantic_waited.clear()
        return name

    def read(self,name,offset=0):
        text=self.note_path(name).read_text(encoding='utf-8')
        return text[max(0,offset):max(0,offset)+24000]

    def forget(self,name):
        p=self.note_path(name)
        if p.parent.name not in ('Profile','Projects') or not p.name.startswith('memory-'):
            raise ValueError('Questo comando elimina solo ricordi memory-*.md. Gli altri documenti si gestiscono dalla vault.')
        p.unlink(); self.index(); self.corpus_generation+=1; self.semantic_cache.clear(); self._semantic_waited.clear()

    def record(self,messages,session,project_ref=None):
        # Content-addressed files avoid duplication on restart and preserve pre-compaction turns.
        question_ids={call['id'] for msg in messages for call in msg.get('tool_calls',[]) if call.get('function',{}).get('name')=='ask_user'}
        for i,m in enumerate(messages):
            if m.get('role')=='tool' and m.get('tool_call_id') in question_ids:
                try:
                    answer=json.loads(m.get('content',''))
                    if answer.get('cancelled') or 'answer' not in answer: continue
                    raw='Scelta confermata dall’utente\nDomanda: '+answer['question']+'\nRisposta: '+answer['answer']
                except (ValueError,KeyError,TypeError): continue
            else:
                if m.get('role') not in ('user','assistant') or not m.get('content'): continue
                raw=str(m['content'])
            ident=hashlib.sha256((str(session)+'|'+str(i)+'|'+m['role']+'|'+raw).encode()).hexdigest()[:24]
            p=self.path/'Conversations'/f'{ident}.md'
            if not p.exists():
                project_ref = project_ref or f'Projects/{self.project_id}'
                self.atomic(p,f'# Conversazione · {m["role"]}\n\nContesto: [[{project_ref}]]\n'
                    f'Sessione: {Path(session).stem}\nTurno: {i}\nArchiviato UTC: {datetime.now(timezone.utc).isoformat()}\n\n'+redact(raw)+'\n')

    def documents(self, purpose='explicit'):
        from semantic_memory import CHUNK_CHARS,CHUNK_STRIDE,DOCUMENT_CHAR_LIMIT
        excluded=set(self.operational.load()['excluded'])
        files=sorted(self.note_catalog.paths(self.path,purpose),key=lambda p:('Conversations' in p.parts or 'Evidence' in p.parts,str(p)))
        coverage={'documents':0,'chunks':0,'truncated_documents':0,'document_char_limit':DOCUMENT_CHAR_LIMIT,'chunk_chars':CHUNK_CHARS}
        rows=[]
        def add(name,text):
            coverage['documents']+=1
            cached=self._chunk_cache.get(name)
            if cached and cached[0] is text:
                rows.extend(cached[1]);coverage['chunks']+=len(cached[1])
                if cached[2]:coverage['truncated_documents']+=1
                return
            limited=text[:DOCUMENT_CHAR_LIMIT]
            if len(text)>DOCUMENT_CHAR_LIMIT: coverage['truncated_documents']+=1
            if not limited: return
            chunks=[]
            for start in range(0,len(limited),CHUNK_STRIDE):
                chunks.append((name,redact(limited[start:start+CHUNK_CHARS]))); coverage['chunks']+=1
                if start+CHUNK_CHARS>=len(limited): break
            rows.extend(chunks);self._chunk_cache[name]=(text,chunks,len(text)>DOCUMENT_CHAR_LIMIT)
            if len(self._chunk_cache)>256:self._chunk_cache.pop(next(iter(self._chunk_cache)))
        for p in files:
            if p.is_symlink() or not p.resolve().is_relative_to(self.path):continue
            name=p.relative_to(self.path).as_posix()
            if name in excluded:continue
            if purpose=='auto' and (name.startswith('Conversations/') or '/Evidence/' in name or name.startswith('Profile/') or p.name in ('Consolidated.md','State.md','Home.md','Index.md')):continue
            text=self.note_catalog.read(p)
            if name.startswith('Projects/') and not (name.startswith('Projects/'+self.project_id+'/') or name=='Projects/'+self.project_id+'.md' or (p.name.startswith('memory-') and self.project_id in text)):continue
            if name.startswith('Conversations/') and self.project_id not in text:continue
            if name in ('Home.md','Projects/Index.md'):continue
            add(name,text)
        references=self.path.parent/'references'
        if references.is_dir():
            for p in references.rglob('*.md'):
                if p.is_symlink() or not p.resolve().is_relative_to(references.resolve()):continue
                add('vault:references/'+p.relative_to(references).as_posix(),self.note_catalog.read(p))
        self.coverage=coverage
        return rows

    def project_view(self, project_id):
        import copy
        from brain_memory import projects
        from project_context import ProjectContext
        row=next((item for item in projects(self) if item['id']==project_id),None)
        if row is None: raise ValueError('Progetto sconosciuto: consulta memory_projects e usa un ID restituito.')
        view=copy.copy(self)
        view.project=Path(row['root']); view.project_id=project_id
        # The key includes project_id. Sharing the cache avoids a second embeddings call when the prompt is rebuilt.
        view.semantic_cache=self.semantic_cache; view._semantic_waited=self._semantic_waited
        view.operational=ProjectContext(view)
        return view

    def semantic_search(self, query, limit=6, purpose='explicit', wait_s=None, turn_token=None):
        import time
        from semantic_memory import TURN_WAIT_SECONDS
        query=redact(query)
        if not self.semantic or not str(query).strip(): return []
        documents=self.documents(purpose)
        fingerprint=hashlib.sha256('\n'.join(hashlib.sha256((name+'\0'+text).encode()).hexdigest() for name,text in documents).encode()).hexdigest()
        excluded=tuple(self.operational.load().get('excluded',[]))
        key=(self.project_id,purpose,query,limit,excluded,fingerprint)
        cached=self.semantic_cache.get(key)
        if cached and cached[0]=='ready': return cached[1]
        if cached and cached[0]=='failed' and time.monotonic()<getattr(self.semantic,'retry_at',0): return []
        if cached and cached[0]=='turn' and turn_token is not None and cached[2]==turn_token: return cached[1]
        if wait_s is None: wait_s=0 if key in self._semantic_waited else TURN_WAIT_SECONDS
        self._semantic_waited.add(key)
        self.semantic.forget_paths(excluded)
        rows=self.semantic.search(query,documents,limit,wait_s=wait_s)
        mode=self.semantic.status.get('mode'); state=self.semantic.status.get('index_state')
        if mode=='semantic' and state=='ready': self.semantic_cache[key]=('ready',rows,None)
        elif mode=='pending' or mode=='semantic': self.semantic_cache[key]=('turn',[] if mode=='pending' else rows,turn_token)
        else: self.semantic_cache[key]=('failed',[],turn_token)
        if len(self.semantic_cache)>32: self.semantic_cache.pop(next(iter(self.semantic_cache)))
        if isinstance(self.semantic.status,dict): self.semantic.status['coverage']=self.coverage
        return [] if mode=='pending' else rows

    def search(self, query, limit=6):
        from memory_relevance import select_semantic
        rows=select_semantic(self.semantic_search(query,limit,purpose='explicit'))
        if rows: return 'Recupero semantico (fonti storiche da verificare):\n'+'\n\n'.join(f"[{row['path']}] similarità {row['score']}\n{row['text']}" for row in rows)
        lexical=self.lexical_hits(query,limit)
        mode=self.semantic.status.get('mode') if self.semantic else 'not_configured'
        if mode=='semantic': prefix='Nessun risultato semantico sopra la soglia. Ricerca lessicale (non semantica).\n'
        elif mode in ('lexical_fallback','pending','unavailable'): prefix='Ricerca lessicale (non semantica); gli embeddings non sono stati usati per questo risultato.\n'
        else: prefix='Ricerca lessicale (non semantica).\n'
        result='\n\n'.join(f"[{row['path']}]\n{row['text'][:1800]}" for row in lexical)[:12000]
        return (prefix+result) if result else (prefix.strip() if mode in ('lexical_fallback','pending','semantic') else '')

    def search_project(self, query, project_id):
        return self.project_view(project_id).search(query)

    def lexical_hits(self, query, limit=6, purpose='explicit'):
        from memory_relevance import lexical_score
        scored=[]
        for name,text in self.documents(purpose):
            score=lexical_score(query,text,self.project.name)
            if not score:continue
            scored.append((score,name,text))
        scored.sort(key=lambda item:item[0],reverse=True)
        return [{'score':score,'path':name,'text':text,'mode':'lexical'} for score,name,text in scored[:limit]]

    def lexical_search(self, query, limit=6):
        return '\n\n'.join(f"[{row['path']}]\n{row['text'][:1800]}" for row in self.lexical_hits(query,limit))[:12000]

    def context(self,query):
        return self.operational.snapshot(query)['text']
