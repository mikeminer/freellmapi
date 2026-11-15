"""Persistent embedding index. The Markdown notes stay authoritative; the index can be rebuilt.

Timeouts are deliberate. FreeLLMAPI 0.16.14 aborts each embeddings provider at 30s and
does not cancel that work when the client disconnects. The default family
gemini-embedding-001 has one provider, so a client socket timeout below 30s abandons a
request the server still finishes. The socket timeout is 35s to receive that single
provider's outcome. A chat turn waits at most 4s and does not close the socket.
"""
import hashlib,inspect,json,math,sqlite3,threading,time,urllib.error,urllib.request,os,shutil,subprocess,tempfile
from concurrent.futures import Future, TimeoutError as FutureTimeout
from contextlib import closing
from pathlib import Path
from urllib.parse import urlsplit

DOCUMENT_CHAR_LIMIT=12000
CHUNK_CHARS=1800
CHUNK_STRIDE=1600
MAX_NEW_CHUNKS=8
MAX_VECTORS=10000
TURN_WAIT_SECONDS=4
SOCKET_TIMEOUT_SECONDS=35
MAX_ATTEMPTS=2
MAX_HTTP_INPUTS=8
from memory_relevance import MIN_SEMANTIC_SCORE as SEMANTIC_MIN_SCORE
CONFIG_ID='chunks:1800/1600/doc:12000/v2'
BACKEND='freellmapi-http'

class EmbeddingUnavailable(RuntimeError):
    pass

class EmbeddingPending(RuntimeError):
    pass

class EmbeddingBusy(RuntimeError):
    pass

class ProviderStatus(RuntimeError):
    def __init__(self, status, retry_after=None, recoverable=False):
        super().__init__(f'HTTP {status}')
        self.status=status
        self.retry_after=retry_after
        self.recoverable=recoverable

def _retry_after(headers):
    if not headers: return None
    raw=None
    for key,value in headers.items():
        if str(key).lower()=='retry-after': raw=value; break
    if raw is None: return None
    try: return max(0,float(raw))
    except (TypeError,ValueError): return None

def _finite_vector(value):
    return isinstance(value,list) and value and all(isinstance(n,(int,float)) and not isinstance(n,bool) and math.isfinite(n) for n in value)

def _parse_embedding_body(body, count):
    if not isinstance(body,dict): raise ValueError('Malformed embedding response')
    model=body.get('model')
    if not isinstance(model,str) or not model or model.startswith(('auto','fusion')): raise ValueError('Invalid or ambiguous embedding model response')
    rows=body.get('data')
    if not isinstance(rows,list): raise ValueError('Malformed embedding response')
    try: ordered=sorted(rows, key=lambda row: row['index'])
    except (TypeError,KeyError) as error: raise ValueError('Embedding response indices mismatch') from error
    if [row.get('index') for row in ordered]!=list(range(count)): raise ValueError('Embedding response indices mismatch')
    vectors=[]
    for row in ordered:
        vector=row.get('embedding') if isinstance(row,dict) else None
        if not _finite_vector(vector): raise ValueError('Invalid vectors')
        vectors.append(vector)
    if any(len(vector)!=len(vectors[0]) for vector in vectors): raise ValueError('Invalid vectors')
    usage=body.get('usage') if isinstance(body.get('usage'),dict) else None
    if usage is not None:
        usage={k:usage.get(k) for k in ('prompt_tokens','total_tokens') if k in usage}
    return {'model':model,'provider':body.get('provider') if isinstance(body.get('provider'),str) else None,
            'vectors':vectors,'dimensions':len(vectors[0]),'usage':usage}

class EmbeddingClient:
    """One embeddings request at a time. Equivalent payloads share one future."""
    def __init__(self, base, token, status_path, transport=None, socket_timeout=SOCKET_TIMEOUT_SECONDS, max_attempts=MAX_ATTEMPTS, retry_backoff=0.5):
        self.base=base.rstrip('/')
        self.token=token
        self.status_path=Path(status_path)
        self.transport=transport or self._http
        self.socket_timeout=socket_timeout
        self.max_attempts=max_attempts
        self.retry_backoff=retry_backoff
        self.request_model='auto'
        self.lock=threading.Lock()
        self.active=None
        self.cooldown_until=0.0
        self.observation={'requested_model':'auto','mode':'not_requested'}
        self.calls=0
        self.http_calls=0
        self.curl_path=shutil.which('curl.exe') if os.name=='nt' else None

    def embed(self, texts, wait_s=None):
        if time.monotonic()<self.cooldown_until: raise EmbeddingUnavailable('cooldown')
        key=hashlib.sha256(json.dumps([self.request_model,list(texts)],ensure_ascii=False).encode()).hexdigest()
        with self.lock:
            active=self.active
            if active and active[1].done() and active[1].exception() is not None and time.monotonic()>=self.cooldown_until:
                active=None
            if active and not active[1].done() and active[0]!=key:
                raise EmbeddingBusy('another embeddings request is in flight')
            if active and active[0]==key:
                future=active[1]
            else:
                future=Future(); self.active=(key,future)
                threading.Thread(target=self._worker,args=(list(texts),future),name='freellmapi-embeddings',daemon=True).start()
        try: result=future.result(timeout=None if wait_s is None else wait_s)
        except FutureTimeout as error:
            # concurrent.futures.TimeoutError is builtins.TimeoutError on Python 3.10+.
            # A finished provider timeout must stay a timeout; only an unfinished wait is pending.
            if future.done():
                inner=future.exception()
                if inner is not None: raise inner
            raise EmbeddingPending('embeddings still running') from error
        return result['model'],result['vectors']

    def _worker(self, texts, future):
        delay=self.retry_backoff
        last=None
        for attempt in range(1,self.max_attempts+1):
            started=time.perf_counter()
            try:
                self.calls+=1
                response=self.transport(texts,self.socket_timeout)
                parsed=_parse_embedding_body(response.get('body'),len(texts))
                parsed['elapsed_s']=round(response.get('elapsed') or time.perf_counter()-started,3)
                parsed['http_status']=response.get('status',200)
                parsed['attempts']=attempt
                parsed['requested_model']=self.request_model
                self._observe(parsed)
                future.set_result(parsed); return
            except Exception as error:
                last=error
                retry_after=getattr(error,'retry_after',None)
                recoverable=bool(getattr(error,'recoverable',False)) and attempt<self.max_attempts and not (retry_after and retry_after>1)
                if not recoverable: self._cooldown(60 if not retry_after else retry_after)
                self._observe_error(error,attempt,retry_after)
                if not recoverable:
                    future.set_exception(error); return
                time.sleep(delay); delay=min(2,delay*2)
        future.set_exception(last or EmbeddingUnavailable('embeddings failed'))

    def _cooldown(self, seconds):
        seconds=max(1,min(float(seconds),86400*32))
        self.cooldown_until=time.monotonic()+seconds
        self.observation['retry_after_seconds']=round(seconds,3)
        self.observation['cooldown_until_epoch']=time.time()+seconds
        self.observation['mode']='lexical_fallback'

    def _observe(self, parsed):
        self.cooldown_until=0
        self.observation={'mode':'semantic','requested_model':self.request_model,'model':parsed['model'],'provider':parsed['provider'],
            'dimensions':parsed['dimensions'],'http_status':parsed['http_status'],'usage':parsed['usage'],'attempts':parsed['attempts'],
            'last_latency_s':parsed['elapsed_s'],'last_error':None,'retry_after_seconds':None,'cooldown_until_epoch':None}
        self._write_status()

    def _observe_error(self, error, attempt, retry_after):
        self.observation={'mode':'lexical_fallback','requested_model':self.request_model,'last_error':type(error).__name__,
            'http_status':getattr(error,'status',None),'attempts':attempt,'retry_after_seconds':retry_after,
            'cooldown_until_epoch':self.observation.get('cooldown_until_epoch')}
        self._write_status()

    def _write_status(self):
        try:
            self.status_path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.status_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.observation,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.status_path)
        except OSError: pass

    def _http(self, texts, timeout):
        if len(texts)>MAX_HTTP_INPUTS:
            started=time.perf_counter();combined=[];identity=None;provider=None;usage_total=0;usage_known=True
            for start in range(0,len(texts),MAX_HTTP_INPUTS):
                batch=texts[start:start+MAX_HTTP_INPUTS]
                remaining=timeout-(time.perf_counter()-started)
                if remaining<=0:raise TimeoutError('Embedding HTTP batch budget exhausted')
                response=self._http(batch,remaining)
                parsed=_parse_embedding_body(response['body'],len(batch))
                current=(parsed['model'],parsed['dimensions'])
                if identity is not None and current!=identity:raise ValueError('Embedding identity changed between HTTP batches')
                identity=current;provider=parsed['provider']
                combined.extend({'index':start+i,'embedding':v} for i,v in enumerate(parsed['vectors']))
                tokens=(parsed['usage'] or {}).get('prompt_tokens')
                if not isinstance(tokens,int):usage_known=False
                else:usage_total+=tokens
            return {'status':200,'headers':{},'elapsed':time.perf_counter()-started,'body':{'model':identity[0],'provider':provider,'data':combined,'usage':{'prompt_tokens':usage_total} if usage_known else None}}
        self.http_calls+=1
        if self.curl_path:return self._curl_http(texts,timeout)
        payload=json.dumps({'model':self.request_model,'input':texts}).encode()
        request=urllib.request.Request(self.base+'/v1/embeddings',data=payload,headers={'Authorization':'Bearer '+self.token,'Content-Type':'application/json'})
        host=urlsplit(self.base).hostname
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({})) if host in ('127.0.0.1','localhost','::1') else urllib.request.build_opener()
        started=time.perf_counter()
        try:
            with opener.open(request,timeout=timeout) as response:
                body=json.loads(response.read().decode('utf-8'))
                return {'status':response.status,'headers':dict(response.headers),'body':body,'elapsed':time.perf_counter()-started}
        except urllib.error.HTTPError as error:
            retry=_retry_after(error.headers)
            recoverable=error.code in (429,502,503,504) and not (retry and retry>1)
            raise ProviderStatus(error.code,retry,recoverable) from None

    def _curl_http(self,texts,timeout):
        """Pass credentials/data through stdin, never argv or request files."""
        started=time.perf_counter()
        payload=json.dumps({'model':self.request_model,'input':texts},ensure_ascii=False)
        with tempfile.TemporaryDirectory(prefix='freellmapi-embedding-') as folder:
            headers_path=Path(folder)/'headers.txt'
            config=['url = '+json.dumps(self.base+'/v1/embeddings'),'request = "POST"',
                'max-time = '+str(max(.05,float(timeout))),'max-filesize = 16777216','silent','show-error',
                'header = '+json.dumps('Authorization: Bearer '+self.token),
                'header = "Content-Type: application/json"',
                'data-binary = '+json.dumps(payload,ensure_ascii=False),
                'dump-header = '+json.dumps(headers_path.as_posix()),'write-out = "\\n%{http_code}"']
            if urlsplit(self.base).hostname in ('127.0.0.1','localhost','::1'):config.append('noproxy = "*"')
            try:
                result=subprocess.run([self.curl_path,'--config','-'],input=('\n'.join(config)+'\n').encode('utf-8'),capture_output=True,
                    timeout=float(timeout)+2,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
            except subprocess.TimeoutExpired:raise TimeoutError('Embedding transport deadline') from None
            if result.returncode:
                if result.returncode==28:raise TimeoutError('Embedding HTTP timeout')
                if result.returncode in (18,52,56):raise ConnectionResetError('Incomplete embedding HTTP response')
                raise urllib.error.URLError('Embedding HTTP transport failed (curl '+str(result.returncode)+')')
            body,separator,status=result.stdout.rpartition(b'\n')
            if not separator or not status.isdigit():raise ValueError('Invalid embedding HTTP status')
            status=int(status)
            headers={}
            if headers_path.exists():
                for line in headers_path.read_text(encoding='iso-8859-1').splitlines():
                    key,sep,value=line.partition(':')
                    if sep:headers[key.strip()]=value.strip()
            if status>=400:
                retry=_retry_after(headers)
                raise ProviderStatus(status,retry,status in (429,502,503,504) and not (retry and retry>1))
            return {'status':status,'headers':headers,'body':json.loads(body.decode('utf-8')),'elapsed':time.perf_counter()-started}

class SemanticIndex:
    def __init__(self, path, embed, backend=BACKEND):
        self.path=Path(path); self.embed=embed; self.backend=backend
        self.status_path=self.path.with_name('semantic-status.json')
        self.retry_at=0; self.lock=threading.Lock(); self._followup=False
        self.status={'mode':'not_requested','index_state':'unavailable','backend':backend,'config_id':CONFIG_ID}
        self._load_cooldown()

    def _load_cooldown(self):
        try: saved=json.loads(self.status_path.read_text(encoding='utf-8'))
        except (OSError,ValueError): return
        until=saved.get('cooldown_until_epoch') or 0
        if isinstance(until,(int,float)) and until>time.time():
            self.retry_at=time.monotonic()+(until-time.time())
            self.status.update({'mode':'lexical_fallback','index_state':'unavailable','reason':saved.get('last_error') or 'cooldown',
                'retry_after_seconds':round(until-time.time(),3),'cooldown_until_epoch':until})

    def _invoke(self, texts, wait_s):
        parameters=inspect.signature(self.embed).parameters
        if 'wait_s' in parameters or any(item.kind is inspect.Parameter.VAR_KEYWORD for item in parameters.values()):
            return self.embed(texts,wait_s=wait_s)
        return self.embed(texts)

    def forget_paths(self, paths):
        paths=[path for path in paths if path]
        if not paths or not self.path.exists(): return
        with self.lock, closing(sqlite3.connect(self.path,timeout=5)) as db, db:
            self._ensure(db)
            db.executemany('DELETE FROM vectors WHERE path=?',[(path,) for path in paths])

    def _backup_sqlite(self, connection=None):
        """Consistent copy before a format change or a vector wipe. Markdown notes are not opened."""
        backup=self.path.with_name(self.path.name+'.bak-'+time.strftime('%Y%m%d-%H%M%S'))
        if connection is not None:
            with closing(sqlite3.connect(backup)) as target: connection.backup(target)
        else:
            with closing(sqlite3.connect(self.path)) as source, closing(sqlite3.connect(backup)) as target:
                source.backup(target)
        return str(backup)

    def rebuild(self):
        """Drop cached vectors only. Markdown notes are not opened for writing."""
        with self.lock:
            if self.path.exists():
                backup=self._backup_sqlite()
                with closing(sqlite3.connect(self.path,timeout=5)) as db, db:
                    self._ensure(db); db.execute('DELETE FROM vectors')
                return backup
            return None

    def search(self, query, documents, limit=6, wait_s=TURN_WAIT_SECONDS):
        if time.monotonic()<self.retry_at:
            self.status.update({'mode':'lexical_fallback','index_state':'unavailable','reason':self.status.get('reason') or 'cooldown'})
            self._publish(); return []
        chunks={}
        order=[]
        for name,text in documents:
            ident=hashlib.sha256((name+'\0'+text).encode()).hexdigest()
            chunks[ident]=(name,text); order.append(ident)
        try:
            with self.lock:
                self.path.parent.mkdir(parents=True,exist_ok=True)
                with closing(sqlite3.connect(self.path,timeout=5)) as db, db:
                    self._ensure(db)
                    self._drop_stale(db,chunks)
                    stored={row[0]:row for row in db.execute('SELECT id,model,dimensions,backend,vector,path FROM vectors') if row[0] in chunks}
            missing=[ident for ident in order if ident not in stored][:MAX_NEW_CHUNKS]
            model,vectors=self._embed_query(query,missing,chunks,wait_s)
            with self.lock, closing(sqlite3.connect(self.path,timeout=5)) as db, db:
                self._ensure(db)
                dimensions=len(vectors[0])
                for ident,vector in zip(missing,vectors[1:]):
                    name,text=chunks[ident]
                    db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?,?)',(ident,name,'',model,dimensions,self.backend,json.dumps(vector),time.time()))
                    stored[ident]=(ident,model,dimensions,self.backend,json.dumps(vector),name)
                mismatched=[ident for ident in order if ident in stored and not self._compatible(stored[ident],model,dimensions)]
                self._publish_progress(model,dimensions,len(order),stored)
            if mismatched:
                extra=mismatched[:MAX_NEW_CHUNKS]
                extra_model,extra_vectors=self._invoke([chunks[ident][1] for ident in extra],wait_s)
                if extra_model!=model or len(extra_vectors)!=len(extra) or any(len(vector)!=len(vectors[0]) for vector in extra_vectors):
                    raise ValueError('Invalid vectors')
                with self.lock, closing(sqlite3.connect(self.path,timeout=5)) as db, db:
                    for ident,vector in zip(extra,extra_vectors):
                        name,_text=chunks[ident]
                        db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?,?)',(ident,name,'',model,dimensions,self.backend,json.dumps(vector),time.time()))
                        stored[ident]=(ident,model,dimensions,self.backend,json.dumps(vector),name)
            ranked=self._rank(query_vector=vectors[0],model=model,dimensions=len(vectors[0]),chunks=chunks,stored=stored)
            indexed=sum(1 for ident in order if ident in stored and self._compatible(stored[ident],model,len(vectors[0])))
            stale=any(ident in stored and not self._compatible(stored[ident],model,len(vectors[0])) for ident in order)
            state='stale' if stale else 'partial' if indexed<len(order) else 'ready'
            self.status={'mode':'semantic','index_state':state,'model':model,'dimensions':len(vectors[0]),'backend':self.backend,'config_id':CONFIG_ID,
                'indexed_in_scope':indexed,'chunks_in_scope':len(order),'partial':indexed<len(order),'document_char_limit':DOCUMENT_CHAR_LIMIT,'chunk_chars':CHUNK_CHARS}
            self._publish()
            self._continue(order,chunks,stored,model)
            return [{'score':round(score,4),'path':name,'text':text} for score,name,text in ranked[:limit] if score>0]
        except (EmbeddingPending,EmbeddingBusy):
            self.status.update({'mode':'pending','index_state':'partial','reason':'in_flight','backend':self.backend,'config_id':CONFIG_ID})
            self._publish(); return []
        except Exception as error:
            delay=float(getattr(error,'retry_after',None) or 60)
            self.retry_at=time.monotonic()+delay
            self.status={'mode':'lexical_fallback','index_state':'unavailable','reason':type(error).__name__,'http_status':getattr(error,'status',None),
                'retry_after_seconds':delay,'cooldown_until_epoch':time.time()+delay,'backend':self.backend,'config_id':CONFIG_ID,'chunks_in_scope':len(order)}
            self._publish(); return []

    def _embed_query(self, query, missing, chunks, wait_s):
        texts=[query]+[chunks[ident][1] for ident in missing]
        model,vectors=self._invoke(texts,wait_s)
        if not isinstance(model,str) or not model or model.startswith(('auto','fusion')) or len(vectors)!=len(texts):
            raise ValueError('Invalid or ambiguous embedding model response')
        if not vectors or any(not _finite_vector(vector) or len(vector)!=len(vectors[0]) for vector in vectors):
            raise ValueError('Invalid vectors')
        return model,vectors

    def _compatible(self, row, model, dimensions):
        return row[1]==model and row[2]==dimensions and row[3]==self.backend

    def _rank(self, query_vector, model, dimensions, chunks, stored):
        norm=math.sqrt(sum(value*value for value in query_vector))
        ranked=[]
        for ident,(name,text) in chunks.items():
            row=stored.get(ident)
            if not row or not self._compatible(row,model,dimensions): continue
            vector=json.loads(row[4])
            if not _finite_vector(vector) or len(vector)!=dimensions: continue
            other=math.sqrt(sum(value*value for value in vector))
            if norm and other: ranked.append((sum(a*b for a,b in zip(query_vector,vector))/(norm*other),name,text))
        ranked.sort(key=lambda item:item[0],reverse=True)
        return ranked

    def _continue(self, order, chunks, stored, model):
        if self._followup: return
        pending=[ident for ident in order if ident not in stored or stored[ident][1]!=model]
        if not pending: return
        batch=pending[:MAX_NEW_CHUNKS]
        self._followup=True
        def run():
            try:
                _model,vectors=self._invoke([chunks[ident][1] for ident in batch],None)
                if _model!=model or len(vectors)!=len(batch): return
                with self.lock, closing(sqlite3.connect(self.path,timeout=5)) as db, db:
                    self._ensure(db)
                    for ident,vector in zip(batch,vectors):
                        if not _finite_vector(vector) or len(vector)!=len(vectors[0]): return
                        db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?,?)',(ident,chunks[ident][0],'',model,len(vector),self.backend,json.dumps(vector),time.time()))
            except Exception:
                return
            finally:
                self._followup=False
        threading.Thread(target=run,name='freellmapi-index-followup',daemon=True).start()

    def _drop_stale(self, db, chunks):
        by_path={}
        for ident,(path,_text) in chunks.items(): by_path.setdefault(path,set()).add(ident)
        for path,idents in by_path.items():
            marks=','.join('?' for _ in idents)
            db.execute(f'DELETE FROM vectors WHERE path=? AND id NOT IN ({marks})',(path,*idents))
        db.execute('DELETE FROM vectors WHERE rowid NOT IN (SELECT rowid FROM vectors ORDER BY updated_at DESC LIMIT ?)',(MAX_VECTORS,))

    def _publish_progress(self, model, dimensions, total, stored):
        self.status.update({'model':model,'dimensions':dimensions,'chunks_in_scope':total})

    def _publish(self):
        client=getattr(self.embed,'__self__',None)
        if isinstance(client,EmbeddingClient):
            for key in ('provider','usage','http_status','attempts','last_latency_s','requested_model'):
                if client.observation.get(key) is not None: self.status[key]=client.observation[key]
        try:
            self.status_path.parent.mkdir(parents=True,exist_ok=True)
            temporary=self.status_path.with_suffix('.tmp')
            temporary.write_text(json.dumps(self.status,ensure_ascii=False,indent=2),encoding='utf-8')
            temporary.replace(self.status_path)
        except OSError: pass

    def _ensure(self, db):
        existing=db.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='vectors'").fetchone()
        if existing and 'dimensions' not in existing[0]:
            self._migrate(db)
            return
        db.execute('''CREATE TABLE IF NOT EXISTS vectors(
            id TEXT PRIMARY KEY, path TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL, dimensions INTEGER NOT NULL, backend TEXT NOT NULL, vector TEXT NOT NULL, updated_at REAL NOT NULL)''')
        db.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)')
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('config_id',CONFIG_ID))
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('backend',self.backend))

    def _migrate(self, db):
        if self.path.exists():
            self.status['migration_backup']=self._backup_sqlite(db)
        db.execute('ALTER TABLE vectors RENAME TO vectors_legacy')
        db.execute('''CREATE TABLE vectors(
            id TEXT PRIMARY KEY, path TEXT NOT NULL DEFAULT '', project_id TEXT NOT NULL DEFAULT '',
            model TEXT NOT NULL, dimensions INTEGER NOT NULL, backend TEXT NOT NULL, vector TEXT NOT NULL, updated_at REAL NOT NULL)''')
        for ident,model,raw in db.execute('SELECT id,model,vector FROM vectors_legacy'):
            try: vector=json.loads(raw)
            except (TypeError,ValueError): continue
            if not _finite_vector(vector) or not isinstance(model,str): continue
            db.execute('INSERT OR REPLACE INTO vectors VALUES(?,?,?,?,?,?,?,?)',(ident,'','',model,len(vector),'legacy',json.dumps(vector),time.time()))
        db.execute('DROP TABLE vectors_legacy')
        db.execute('CREATE TABLE IF NOT EXISTS meta(key TEXT PRIMARY KEY, value TEXT)')
        db.execute('INSERT OR REPLACE INTO meta VALUES(?,?)',('config_id',CONFIG_ID))

def configure(agent):
    if not agent.memory: return
    client=EmbeddingClient(agent.base,agent.token,agent.memory.path/'semantic-status.json')
    agent.memory.embed_client=client
    agent.memory.semantic=SemanticIndex(agent.memory.path/'semantic-index.sqlite',client.embed,backend=BACKEND)
