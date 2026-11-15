"""Local desktop/CMD clients attach to the existing, single Remote coordinator."""
import hashlib,http.client,json,os,ssl,subprocess,sys,time,uuid,threading
from pathlib import Path
from urllib.parse import urlsplit,quote
from remote_pairing import check_instance,read_small,request_pair

class RemoteRejected(RuntimeError):
    def __init__(self,status,message):super().__init__(message);self.status=status

class RemoteClient:
    def __init__(self,home,kind='desktop'):
        self.home=Path(home);self.kind=kind;self.metadata=None;self.token=None;self.last_state={}
        self.folder=self.home/'clients';self.folder.mkdir(parents=True,exist_ok=True)
        self.send_lock=threading.Lock()
        self.credential=self.folder/(kind+'-remote.dpapi');self.pending=self.folder/(kind+'-pending.json')

    def descriptors(self,session=None):
        result=[]
        paths=list((self.home/'remote/endpoints').glob('*.json'))+[self.home/'remote/local-pairing.json']
        for path in paths:
            try:
                meta=read_small(path)
                if session and meta.get('session')!=session:continue
                if any(x['url']==meta['url'] for x in result):continue
                check_instance(meta);result.append(meta)
            except (OSError,ValueError,TimeoutError):continue
        return sorted(result,key=lambda x:0 if urlsplit(x['url']).port==31416 else 1)

    def request(self,path,body=None,auth=True,timeout=25):
        if not self.metadata:raise RuntimeError('Coordinatore non collegato.')
        target=urlsplit(self.metadata['url']);fingerprint=self.metadata.get('fingerprint','')
        if target.scheme=='https':
            context=ssl.create_default_context() if not fingerprint else ssl._create_unverified_context()
            connection=http.client.HTTPSConnection(target.hostname,target.port,context=context,timeout=timeout)
        else:connection=http.client.HTTPConnection(target.hostname,target.port,timeout=timeout)
        try:
            connection.connect()
            if target.scheme=='https' and fingerprint:
                actual=hashlib.sha256(connection.sock.getpeercert(binary_form=True)).hexdigest()
                if actual!=fingerprint:raise RuntimeError('Certificato del coordinatore cambiato: nessun invio.')
            headers={'Content-Type':'application/json'}
            if auth:headers['Authorization']='Bearer '+self.token
            data=json.dumps(body).encode() if body is not None else None
            connection.request('POST' if body is not None else 'GET',path,data,headers)
            response=connection.getresponse();raw=response.read(4_000_001)
            if len(raw)>4_000_000:raise RuntimeError('Risposta del coordinatore oltre il limite.')
            value=json.loads(raw)
            if response.status!=200:raise RemoteRejected(response.status,'Remote HTTP '+str(response.status)+': '+str(value.get('error','richiesta non accettata')))
            return value
        finally:connection.close()

    def connect(self,session=None,auto_start=False):
        candidates=self.descriptors(session)
        if not candidates and auto_start:
            if self.descriptors():candidates=self.descriptors()
            else:
                from session_store import Sessions
                rows=Sessions(self.home).list();sid=session or (rows[0]['id'] if rows else None)
                python=str(Path(sys.executable).with_name('python.exe')) if os.name=='nt' else sys.executable
                command=[python,'-X','utf8',str(Path(__file__).with_name('runtime_bootstrap.py')),'--remote-only']
                if sid:command+=['--session',sid]
                else:command+=['--new']
                log=(self.folder/'coordinator.log').open('a',encoding='utf-8')
                try:subprocess.Popen(command,stdin=subprocess.DEVNULL,stdout=log,stderr=log,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
                finally:log.close()
                until=time.monotonic()+20
                while time.monotonic()<until:
                    candidates=self.descriptors()
                    if candidates:break
                    time.sleep(.2)
        if not candidates:raise RuntimeError('Nessun coordinatore Remote disponibile per questa sessione.')
        preferred=self.folder/(self.kind+'-coordinator.json')
        if not session and preferred.exists():
            try:
                selected=read_small(preferred);candidates.sort(key=lambda m:m['instance']!=selected['instance'])
            except (OSError,ValueError,KeyError):pass
        self.metadata=candidates[0]
        from hl_credentials import get,put
        pair_needed=False
        try:
            saved=json.loads(get(self.credential));self.token=saved['token'];self.device=saved['device']
        except (OSError,ValueError,RuntimeError,KeyError):pair_needed=True
        if not pair_needed:
            try:state=self.state()
            except RemoteRejected as error:
                if error.status not in (401,403):raise
                pair_needed=True
        if pair_needed:
            pair=request_pair(self.home,metadata=self.metadata)
            login=self.request('/v1/pair',{'code':pair['code'],'name':'FREELLMAPI '+self.kind},auth=False)
            self.token=login['token'];self.device=login['device'];put(self.credential,json.dumps({'token':self.token,'device':self.device}));state=self.state()
        if session and state['session']!=session:raise RuntimeError('La sessione del coordinatore è cambiata. Aggiorna prima di collegarti.')
        return state

    def state(self):
        result=self.request('/v1/state');self.last_state=result;return result

    def send(self,text,attachments=()):
        with self.send_lock:return self._send(text,attachments)
    def _send(self,text,attachments=()):
        if self.pending.exists():
            result=self.check_pending()
            if result.get('state')=='unconfirmed':return result
            raise RuntimeError('Invio precedente riconciliato. Il nuovo messaggio è ancora nella bozza: rileggilo e premi Invia. Nessun reinvio automatico.')
        import re
        if not attachments and re.fullmatch(r'/resume [a-f0-9]{32}',text):
            from session_routing import owner
            meta=owner(self.home,text[8:],(self.metadata or {}).get('instance'))
            if meta:return self.attach(meta,text[8:])
        record={'request_id':uuid.uuid4().hex,'text':text,'session':self.last_state.get('session'),'attachments':list(attachments),'session_handoff':True}
        from reliable_runtime import atomic_bytes
        atomic_bytes(self.pending,json.dumps(record,ensure_ascii=False).encode())
        try:
            if 'local_commands' in self.last_state.get('capabilities',[]):result=self.local_command(record)
            else:result=self.request('/v1/input',record,timeout=12)
        except (OSError,TimeoutError,http.client.HTTPException):return {'state':'unconfirmed','request_id':record['request_id'],'not_replayed':True}
        except RemoteRejected as error:
            if error.status in (400,401,404):
                atomic_bytes(self.folder/(self.kind+'-rejected.json'),json.dumps(record).encode());self.pending.unlink(missing_ok=True)
            raise
        if result.get('state')=='handoff' and result.get('not_enqueued'):
            self.pending.unlink(missing_ok=True)
            return self.attach(result['coordinator'],result['session'])
        if result.get('request_id')==record['request_id']:
            atomic_bytes(self.folder/(self.kind+'-last-input.json'),json.dumps(record,ensure_ascii=False).encode());self.pending.unlink(missing_ok=True)
        return result

    def attach(self,metadata,session):
        from session_routing import same_host
        if not self.metadata or not same_host(self.metadata['url'],metadata['url']):raise RuntimeError('Coordinatore su un altro PC o trasporto: collegamento conservato.')
        check_instance(metadata);previous=self.metadata
        try:
            self.metadata=dict(metadata);state=self.state()
            if state.get('session')!=session:raise RuntimeError('Il coordinatore ha cambiato sessione. Nessuna azione inviata.')
        except Exception:self.metadata=previous;raise
        from reliable_runtime import atomic_bytes
        atomic_bytes(self.folder/(self.kind+'-coordinator.json'),json.dumps(metadata).encode())
        return {'state':'attached','session':session,'not_enqueued':True,'not_replayed':True}

    def local_command(self,record):
        from reliable_runtime import atomic_bytes
        meta=self.metadata;check_instance(meta)
        folder=self.home/'remote/local-pairing'/meta['instance'];ident=uuid.uuid4().hex
        source=folder/(ident+'.request');reply=folder/(ident+'.reply')
        atomic_bytes(source,json.dumps({'operation':'command','device':self.device,'input':record}).encode())
        try:
            end=time.monotonic()+8
            while time.monotonic()<end:
                if reply.exists():
                    try:value=read_small(reply)
                    except (FileNotFoundError,PermissionError):time.sleep(.05);continue
                    if value.get('error'):raise RemoteRejected(400 if value.get('not_enqueued') else 500,value['error'])
                    return value['receipt']
                time.sleep(.05)
            return {'state':'unconfirmed','request_id':record['request_id'],'not_replayed':True}
        finally:source.unlink(missing_ok=True);reply.unlink(missing_ok=True)

    def check_pending(self):
        if not self.pending.exists():return {'state':'none','message':'Nessun invio non confermato.'}
        value=json.loads(self.pending.read_text(encoding='utf-8'))
        state=self.request('/v1/state?request_id='+quote(value['request_id']))
        receipt=state.get('request_receipt') or {}
        if receipt.get('found'):self.pending.unlink(missing_ok=True);return receipt['receipt']
        return {'state':'unconfirmed','request_id':value['request_id'],'not_replayed':True}

    def answer(self,question,answer='',cancelled=False):return self.request('/v1/answer',{'id':question,'answer':answer,'cancelled':cancelled})
