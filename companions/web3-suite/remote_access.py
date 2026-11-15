"""Opt-in HTTPS bridge to the existing CLI. One coordinator, no remote shell endpoint."""
import base64,collections,contextlib,hashlib,hmac,ipaddress,json,os,queue,re,secrets,socket,ssl,tempfile,threading,time,uuid
from datetime import datetime,timedelta,timezone
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs,urlencode,urlsplit
from reliable_runtime import atomic_bytes
from secret_output import redact
from session_store import serialized
from desktop_auth import LoginRequired

_active=None
_local=threading.local()
_RECEIPT_RANK={'queued':0,'running':1,'interrupted_not_replayed':2,'finished':3}

def _prefer_receipt(memory,disk):
    """Keep the furthest durable state. A stale bridge must not requeue a finished receipt."""
    memory=dict(memory);disk=dict(disk)
    if _RECEIPT_RANK.get(disk.get('state'),0)>_RECEIPT_RANK.get(memory.get('state'),0):return disk
    if _RECEIPT_RANK.get(memory.get('state'),0)>_RECEIPT_RANK.get(disk.get('state'),0):return memory
    if memory.get('outcome') is None and disk.get('outcome') is not None:return disk
    return memory

def active():return _active
def publish(kind,text):
    if _active and not getattr(_local,'silent',False):_active.publish(kind,str(text))

@contextlib.contextmanager
def silent():
    old=getattr(_local,'silent',False);_local.silent=True
    try:yield
    finally:_local.silent=old

def vpn_address():
    import shutil,subprocess
    exe=shutil.which('tailscale') or ('C:/Program Files/Tailscale/tailscale.exe' if Path('C:/Program Files/Tailscale/tailscale.exe').is_file() else None)
    if exe:
        try:
            value=subprocess.run([exe,'ip','-4'],capture_output=True,text=True,timeout=4,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0)).stdout.strip().splitlines()[0]
            if ipaddress.ip_address(value) in ipaddress.ip_network('100.64.0.0/10'):return value
        except (OSError,ValueError,IndexError,subprocess.TimeoutExpired):pass
    return None

def tls_context(folder):
    from cryptography import x509
    from cryptography.hazmat.primitives import hashes,serialization
    from cryptography.hazmat.primitives.asymmetric import ec
    from cryptography.x509.oid import NameOID
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    certfile=folder/'server.pem';keyfile=folder/('server-key.dpapi' if os.name=='nt' else 'server-key.pem')
    if not certfile.exists() or not keyfile.exists():
        key=ec.generate_private_key(ec.SECP256R1());name=x509.Name([x509.NameAttribute(NameOID.COMMON_NAME,'freellmapi Remote')]);now=datetime.now(timezone.utc)
        cert=x509.CertificateBuilder().subject_name(name).issuer_name(name).public_key(key.public_key()).serial_number(x509.random_serial_number()).not_valid_before(now-timedelta(minutes=5)).not_valid_after(now+timedelta(days=730)).add_extension(x509.BasicConstraints(ca=False,path_length=None),True).sign(key,hashes.SHA256())
        pem=key.private_bytes(serialization.Encoding.PEM,serialization.PrivateFormat.PKCS8,serialization.NoEncryption())
        if os.name=='nt':
            from hl_credentials import put
            put(keyfile,pem.decode('ascii')) # Dedicated TLS key, never the wallet key.
        else:
            fd=os.open(keyfile,os.O_WRONLY|os.O_CREAT|os.O_TRUNC,0o600)
            with os.fdopen(fd,'wb') as stream:stream.write(pem)
        atomic_bytes(certfile,cert.public_bytes(serialization.Encoding.PEM))
    context=ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER);context.minimum_version=ssl.TLSVersion.TLSv1_2
    if os.name=='nt':
        from hl_credentials import get
        pem=get(keyfile)
        fd,tmp=tempfile.mkstemp(prefix='tls-',suffix='.pem',dir=folder)
        try:
            with os.fdopen(fd,'w',encoding='ascii') as stream:stream.write(pem)
            context.load_cert_chain(certfile,tmp)
        finally:
            pem=None;Path(tmp).unlink(missing_ok=True)
    else:context.load_cert_chain(certfile,keyfile)
    cert=x509.load_pem_x509_certificate(certfile.read_bytes())
    return context,cert.fingerprint(hashes.SHA256()).hex()

class Remote:
    def __init__(self,home,snapshot,cancel=lambda:None):
        self.folder=Path(home)/'remote';self.folder.mkdir(parents=True,exist_ok=True)
        from trading_view import TradingView
        self.trading=TradingView(home)
        self.snapshot=snapshot;self.cancel_callback=cancel;self.cv=threading.Condition(threading.RLock())
        self.events=collections.deque(maxlen=1200);self.sequence=0;self.inputs=queue.Queue(maxsize=8);self.requests=collections.OrderedDict()
        self.requests_path=self.folder/'requests.json'
        self._persisted=collections.OrderedDict();self._dirty=set();self._requests_unreadable=False
        self._load_requests()
        self.devices_path=self.folder/'devices.json'
        self.devices=json.loads(self.devices_path.read_text(encoding='utf-8')) if self.devices_path.exists() else {}
        self.owner=None;self.busy=False;self.question=None;self.answer=None;self.closed=False;self.server=None
        self.instance=uuid.uuid4().hex
        self.current_input=None
        self.pair_code=None;self.pair_deadline=0;self.pair_attempts=0;self.fingerprint='';self.url='';self.local_pairing=None
        from mobile_account import MobileAccount
        self.account=MobileAccount(home)
    def publish(self,kind,text):
        text=redact(str(text))[:16000]
        with self.cv:
            self.sequence+=1;self.events.append({'id':self.sequence,'kind':kind,'text':text,'time':time.time()});self.cv.notify_all()
    def save_devices(self):atomic_bytes(self.devices_path,json.dumps(self.devices).encode())
    @contextlib.contextmanager
    def _requests_file_lock(self):
        path=self.folder/'requests.json.lock';self.folder.mkdir(parents=True,exist_ok=True)
        fd=os.open(path,os.O_CREAT|os.O_RDWR)
        try:
            if os.fstat(fd).st_size==0:os.write(fd,b'\0')
            os.lseek(fd,0,os.SEEK_SET)
            deadline=time.monotonic()+10
            if os.name=='nt':
                import msvcrt
                while True:
                    try:msvcrt.locking(fd,msvcrt.LK_NBLCK,1);break
                    except OSError:
                        if time.monotonic()>=deadline:raise TimeoutError('Registro richieste occupato.')
                        time.sleep(0.01)
            else:
                import fcntl
                fcntl.flock(fd,fcntl.LOCK_EX)
            try:yield
            finally:
                if os.name=='nt':
                    import msvcrt
                    os.lseek(fd,0,os.SEEK_SET);msvcrt.locking(fd,msvcrt.LK_UNLCK,1)
                else:
                    import fcntl
                    fcntl.flock(fd,fcntl.LOCK_UN)
        finally:os.close(fd)
    def _read_request_rows(self):
        if not self.requests_path.exists():return []
        try:
            raw=self.requests_path.read_text(encoding='utf-8')
            rows=json.loads(raw) if raw.strip() else []
        except (OSError,json.JSONDecodeError):return None
        if not isinstance(rows,list):return None
        return [row for row in rows if isinstance(row,dict) and isinstance(row.get('device'),str) and isinstance(row.get('value'),dict) and isinstance(row['value'].get('request_id'),str)]
    def _load_requests(self):
        if not self.requests_path.exists():return
        with self._requests_file_lock():rows=self._read_request_rows()
        if rows is None:self._requests_unreadable=True;return
        for row in rows:
            value=dict(row['value']);key=(row['device'],value['request_id'])
            self._persisted[key]=dict(value)
            if value.get('state') in ('queued','running'):value['state']='interrupted_not_replayed'
            self.requests[key]=value
    def _refresh_persisted(self):
        rows=self._read_request_rows()
        if rows is None:self._requests_unreadable=True;return False
        self._requests_unreadable=False
        persisted=collections.OrderedDict()
        for row in rows:
            value=dict(row['value']);persisted[(row['device'],value['request_id'])]=value
        self._persisted=persisted
        for key,value in persisted.items():
            current=self.requests.get(key)
            if key in self._dirty and current is not None:
                self.requests[key]=_prefer_receipt(current,value);continue
            if current is None:
                shown=dict(value)
                if shown.get('state') in ('queued','running'):shown['state']='interrupted_not_replayed'
                self.requests[key]=shown
            elif value.get('state')=='finished':self.requests[key]=dict(value)
            elif current.get('state')=='interrupted_not_replayed' and value.get('state') in ('queued','running'):continue
            else:self.requests[key]=_prefer_receipt(current,value)
        return True
    def _write_requests(self):
        output=collections.OrderedDict((key,dict(value)) for key,value in self._persisted.items())
        written=set(self._dirty)
        for key in written:
            if key not in self.requests:output.pop(key,None);continue
            current=dict(self.requests[key]);previous=output.get(key)
            output[key]=current if previous is None else _prefer_receipt(current,previous)
        while len(output)>5000:output.popitem(last=False)
        atomic_bytes(self.requests_path,json.dumps([{'device':key[0],'value':value} for key,value in output.items()]).encode())
        self._persisted=collections.OrderedDict((key,dict(value)) for key,value in output.items())
        self._dirty.clear()
        for key in list(self.requests):
            if key not in output:del self.requests[key]
        for key,value in output.items():
            if key in written:self.requests[key]=dict(value)
            elif key not in self.requests:
                shown=dict(value)
                if shown.get('state') in ('queued','running'):shown['state']='interrupted_not_replayed'
                self.requests[key]=shown
            elif value.get('state')=='finished':self.requests[key]=dict(value)
    def save_requests(self):
        with self._requests_file_lock():
            if not self._dirty:return
            if self._refresh_persisted() is False:return
            self._write_requests()
    def _disk_receipt(self,device,request_id):
        rows=self._read_request_rows()
        if not rows:return None
        for row in rows:
            if row['device']==device and row['value']['request_id']==request_id:return row['value']
        return None
    def _visible_receipt(self,device,request_id):
        memory=self.requests.get((device,request_id));disk=self._disk_receipt(device,request_id)
        if memory is None:return disk
        if disk is None:return memory
        chosen=_prefer_receipt(memory,disk)
        if chosen.get('state')=='finished' and memory.get('state')!='finished':self.requests[(device,request_id)]=dict(chosen)
        return chosen
    def pair(self,url=None,ttl=120):
        from remote_pairing import pairing_seconds
        ttl=pairing_seconds(ttl)
        target=url or self.url;parsed=urlsplit(target)
        if parsed.scheme not in ('https','http') or not parsed.hostname or parsed.username or parsed.password or parsed.path not in ('','/') or parsed.query or parsed.fragment:raise ValueError('URL del PC richiesta, senza credenziali o percorso.')
        if parsed.scheme=='http' and target.rstrip('/')!=self.url:raise ValueError('HTTP consentito solo sul tunnel VPN/USB locale già verificato all’avvio.')
        with self.cv:
            self.pair_code=f'{secrets.randbelow(100000000):08d}';self.pair_deadline=time.monotonic()+ttl;self.pair_attempts=0
            code=self.pair_code
            uri='freellmapi://pair?'+urlencode({'url':target.rstrip('/'),'fp':self.fingerprint,'code':self.pair_code})
        import qrcode
        qrcode.make(uri).save(self.folder/'pair.png')
        return {'url':target.rstrip('/'),'fingerprint':self.fingerprint,'code':code,'expires_seconds':ttl,'link':uri,'qr':str(self.folder/'pair.png')}
    def pair_device(self,body):
        with self.cv:
            self.pair_attempts+=1
            if not self.pair_code or self.pair_attempts>8 or time.monotonic()>self.pair_deadline or not hmac.compare_digest(str(body.get('code','')),self.pair_code):raise PermissionError('Abbinamento scaduto o codice errato; genera /remote pair dal PC.')
            if len(self.devices)>=8:raise ValueError('Limite dispositivi: usa /remote revoke dal PC.')
            token=secrets.token_urlsafe(32);device=uuid.uuid4().hex
            self.devices[hashlib.sha256(token.encode()).hexdigest()]={'id':device,'name':redact(str(body.get('name','Android')))[:80],'expires':time.time()+30*86400}
            self.pair_code=None;self.save_devices()
            return {'token':token,'device':device,'expires_days':30}
    def authorize(self,header):
        if not isinstance(header,str) or not header.startswith('Bearer '):raise PermissionError('Dispositivo non abbinato.')
        digest=hashlib.sha256(header[7:].encode()).hexdigest()
        with self.cv:
            # Multiple bridges share pairing/revocation; do not use a stale copy.
            self.devices=json.loads(self.devices_path.read_text(encoding='utf-8')) if self.devices_path.exists() else {}
            device=self.devices.get(digest)
            if not device or device['expires']<=time.time():raise PermissionError('Abbinamento scaduto/revocato.')
            return device['id']
    def revoke(self):
        with self.cv:
            self.devices.clear();self.save_devices();self.pair_code=None;self.cv.notify_all()
            while not self.inputs.empty():
                try:self.inputs.get_nowait()
                except queue.Empty:break
            if self.question:self.answer={'question':self.question['question'],'cancelled':True}
    def enqueue(self,device,body,local=False):
        ident=body.get('request_id');text=body.get('text')
        if not isinstance(ident,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',ident) or not isinstance(text,str) or not text.strip() or len(text)>12000:raise ValueError('Messaggio/ID non valido (massimo 12000 caratteri).')
        text=text.strip()
        allowed={'/status','/timings','/session','/new','/ask','/plan','/auto on','/auto off','/retry','/compact','/models','/reload','/trading status','/trading strategy','/trading monitor','/trading stop'}
        if not local and text.startswith('/') and text not in allowed|{'/hyperliquid','/hyperliquid menu'} and not re.fullmatch(r'/model [^\s]{1,200}|/resume [a-f0-9]{32}',text):raise ValueError('Comando remoto non disponibile: usa messaggi normali, modelli o sessioni.')
        selected=body.get('session')
        if selected is not None and selected!=self.snapshot().get('session'):raise ValueError('La sessione è cambiata: aggiorna prima di inviare. Nessuna azione accodata.')
        session=self.snapshot().get('session');attachments=body.get('attachments',[])
        if re.fullmatch(r'/resume [a-f0-9]{32}',text):
            from session_routing import owner,public
            existing=owner(self.folder.parent,text[8:],self.instance)
            if existing:
                if body.get('session_handoff') is not True:raise ValueError('Aggiorna il client per collegarti al coordinatore della sessione; nessuna seconda esecuzione accodata.')
                return {'request_id':ident,'state':'handoff','coordinator':public(existing),'session':text[8:],'not_enqueued':True}
        if attachments:
            from attachment_store import Attachments
            if text.startswith('/'):raise ValueError('Gli allegati richiedono un messaggio, non un comando.')
            Attachments(self.folder.parent).validate(device,session,attachments)
        with self.cv:
            with self._requests_file_lock():
                if self._requests_unreadable or self._refresh_persisted() is False:raise ValueError('Registro richieste non leggibile: nessun nuovo accodamento.')
                key=(device,ident)
                if key in self.requests:return dict(self.requests[key],reused=True)
                if self.inputs.full():raise ValueError('Coda piena: attendi il completamento dei messaggi inviati.')
                journal=self.folder/'input-journal';journal.mkdir(exist_ok=True)
                name=device+'-'+ident+'.json'
                atomic_bytes(journal/name,json.dumps({'text':redact(text),'session':session,'attachments':attachments},ensure_ascii=False).encode())
                result={'request_id':ident,'state':'queued','session':session,'attachments':attachments};self.requests[key]=result;self._dirty.add(key)
                while len(self.requests)>5000:
                    dropped,_=self.requests.popitem(last=False);self._dirty.discard(dropped);self._persisted.pop(dropped,None)
                self._write_requests()
            self.inputs.put((device,ident,text));self.publish('user',text);return result
    def pop(self):
        if self.busy:return None
        try:device,ident,text=self.inputs.get_nowait()
        except queue.Empty:return None
        with self.cv:
            if not any(d['id']==device and d['expires']>time.time() for d in self.devices.values()):return None
            self.owner=device;self.busy=True;self.requests[(device,ident)]['state']='running';self._dirty.add((device,ident))
            self.current_input=(device,ident)
            self.save_requests()
        return text
    def prepare_input(self,agent):
        if not self.current_input:return
        record=self.requests[self.current_input]
        if record.get('session') and record['session']!=agent.session.stem:raise ValueError('Sessione cambiata dopo accodamento: nessuna azione eseguita.')
        ids=record.get('attachments') or []
        if ids:
            from attachment_store import Attachments
            Attachments(self.folder.parent).bind(agent.session,ids);agent.pending_attachments=ids
        self.current_input=None
    def complete(self,outcome=None):
        with self.cv:
            self.current_input=None
            for key,value in list(self.requests.items()):
                if key[0]==self.owner and value['state']=='running':
                    value['state']='finished'
                    if outcome is not None:value['outcome']=outcome
                    self._dirty.add(key)
            self.owner=None;self.busy=False;self.question=None;self.answer=None;self.cv.notify_all()
            self.save_requests()
    def ask(self,question,options):
        ident=uuid.uuid4().hex
        with self.cv:
            self.question={'id':ident,'question':redact(question),'options':[redact(x) for x in options],'owner':self.owner};self.answer=None;self.publish('question',question)
            deadline=time.monotonic()+600
            while self.answer is None and not self.closed and time.monotonic()<deadline:self.cv.wait(min(2,max(0,deadline-time.monotonic())))
            result=self.answer or {'question':question,'cancelled':True}
            self.question=None;self.answer=None;return result
    def answer_question(self,device,body):
        with self.cv:
            q=self.question
            paired=any(d['id']==device and d['expires']>time.time() for d in self.devices.values())
            if not q or q['id']!=body.get('id') or not (q['owner']==device or paired) or self.answer is not None:raise ValueError('Domanda scaduta o già risolta.')
            answer=body.get('answer')
            if body.get('cancelled'):self.answer={'question':q['question'],'cancelled':True}
            elif isinstance(answer,str) and answer.strip() and len(answer)<=4000:self.answer={'question':q['question'],'answer':answer.strip(),'cancelled':False}
            else:raise ValueError('Risposta non valida.')
            self.cv.notify_all();return {'accepted':True}
    def state(self,device,request_id=None):
        if request_id is not None and (not isinstance(request_id,str) or not re.fullmatch(r'[a-zA-Z0-9_-]{8,80}',request_id)):raise ValueError('ID richiesta non valido.')
        state=self.snapshot()
        if self.local_pairing and state.get('session')!=getattr(self,'registered_session',None):
            self.local_pairing.update_session(state.get('session'));self.registered_session=state.get('session')
        # Measured phase timings are supplied by snapshot(); no prompts or keys.
        state['trading']=self.trading.snapshot()
        import trading_service
        state['trading_service']=trading_service.status(self.folder.parent)
        with self.cv:
            receipt_state=None
            if request_id is not None:
                # Lookup is read-only: a receipt saved by another bridge is visible
                # without queueing, and a missing ID stays unknown.
                receipt=self._visible_receipt(device,request_id)
                receipt_state={'request_id':request_id,'found':receipt is not None,'receipt':dict(receipt) if receipt is not None else None}
            state.update(instance=self.instance,busy=self.busy,cursor=self.sequence,requests=[dict(v) for (d,_),v in self.requests.items() if d==device][-15:])
            if receipt_state is not None:state['request_receipt']=receipt_state
            state['question']={k:v for k,v in self.question.items() if k!='owner'} if self.question else None
            state['capabilities']=['shared_clients','local_commands','actions_menu','session_binding','attachments','session_handoff','tailscale_account']
        return state
    def poll(self,after,timeout=2):
        with self.cv:
            if self.sequence<=after and not self.closed:self.cv.wait(timeout)
            gap=after>self.sequence or bool(self.events and after and after<self.events[0]['id']-1)
            rows=[row for row in self.events if row['id']>after][:150]
            return {'events':rows,'cursor':rows[-1]['id'] if rows else self.sequence,'gap':gap}
    def start(self,bind='127.0.0.1',port=31416,private_vpn=False):
        global _active
        ipaddress.ip_address(bind)
        if _active:raise ValueError('Un solo bridge remoto per processo.')
        if private_vpn and bind!='127.0.0.1' and bind!=vpn_address():raise ValueError('Trasporto VPN consentito solo sull’IP Tailscale verificato oppure sul loopback USB.')
        context=None
        if not private_vpn:context,self.fingerprint=tls_context(self.folder)
        remote=self
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def reply(self,status,data):
                blob=json.dumps(data,ensure_ascii=False).encode();self.send_response(status);self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(blob)));self.send_header('Cache-Control','no-store');self.send_header('X-Content-Type-Options','nosniff');self.send_header('X-Freellmapi-Instance',remote.instance);self.end_headers();self.wfile.write(blob)
            def handle_request(self):
                try:
                    self.connection.settimeout(8)
                    parsed=urlsplit(self.path);body={}
                    if self.command=='POST':
                        if self.headers.get('Content-Type','').split(';')[0]!='application/json':raise ValueError('Content-Type application/json richiesto.')
                        length=int(self.headers.get('Content-Length','0'))
                        maximum=28_000_000 if parsed.path=='/v1/attachment' else 20000
                        if not 0<length<=maximum:raise ValueError('Corpo richiesta non valido.')
                        body=json.loads(self.rfile.read(length))
                        if not isinstance(body,dict):raise ValueError('Oggetto JSON richiesto.')
                    # Consume a bounded valid body before rejecting it. Closing a
                    # Windows socket with unread POST data can discard the 401 via RST.
                    if self.headers.get('Origin'):raise PermissionError('Origine browser non consentita.')
                    if self.command=='POST' and parsed.path=='/v1/pair':return self.reply(200,remote.pair_device(body))
                    device=remote.authorize(self.headers.get('Authorization',''))
                    address=self.client_address[0]
                    if parsed.path.startswith('/v1/account/'):
                        if self.command=='GET' and parsed.path=='/v1/account/status':return self.reply(200,remote.account.status(device,address,fresh=True))
                        if self.command=='POST' and parsed.path=='/v1/account/login':return self.reply(200,remote.account.login(device,address))
                        if self.command=='POST' and parsed.path=='/v1/account/logout':return self.reply(200,remote.account.logout(device))
                        return self.reply(404,{'error':'Endpoint account non disponibile.'})
                    if self.headers.get('X-Freellmapi-Account')=='required' or remote.account.enrolled(device):remote.account.require(device,address)
                    if self.command=='GET' and parsed.path=='/v1/sessions/resolve':
                        from session_routing import owner,public
                        wanted=parse_qs(parsed.query).get('session',[''])[0];existing=owner(remote.folder.parent,wanted,remote.instance)
                        return self.reply(200,{'session':wanted,'coordinator':public(existing) if existing else None})
                    if self.command=='POST' and parsed.path=='/v1/attachment':
                        from attachment_store import Attachments
                        if body.get('session')!=remote.snapshot().get('session'):raise ValueError('Sessione allegato cambiata.')
                        raw=base64.b64decode(body.get('data',''),validate=True)
                        return self.reply(200,Attachments(remote.folder.parent).put(device,body['session'],body['name'],raw))
                    if self.command=='GET' and parsed.path=='/v1/state':return self.reply(200,remote.state(device,parse_qs(parsed.query).get('request_id',[None])[0]))
                    if self.command=='POST' and parsed.path=='/v1/trading/refresh':return self.reply(200,remote.trading.snapshot(refresh=True))
                    if self.command=='POST' and parsed.path=='/v1/trading/service-stop':
                        import trading_service
                        return self.reply(200,trading_service.stop(remote.folder.parent))
                    if self.command=='GET' and parsed.path=='/v1/events':
                        after=max(0,int(parse_qs(parsed.query).get('after',['0'])[0]));data=remote.poll(after)
                        remote.authorize(self.headers.get('Authorization',''));return self.reply(200,data)
                    if self.command=='POST' and parsed.path=='/v1/input':return self.reply(200,remote.enqueue(device,body))
                    if self.command=='POST' and parsed.path=='/v1/answer':return self.reply(200,remote.answer_question(device,body))
                    if self.command=='POST' and parsed.path=='/v1/cancel':
                        with remote.cv:
                            busy=remote.busy
                            remote.cancel_requested=busy
                            if remote.question:remote.answer={'question':remote.question['question'],'cancelled':True};remote.cv.notify_all()
                        self.reply(200,{'requested':busy})
                        if busy:remote.cancel_callback()
                        return
                    self.reply(404,{'error':'Endpoint non disponibile.'})
                except PermissionError as exc:
                    from mobile_account import AccountRequired
                    self.reply(403 if isinstance(exc,AccountRequired) else 401,{'error':str(exc)})
                except LoginRequired as exc:self.reply(403,{'error':str(exc)})
                except (ValueError,TypeError,KeyError,json.JSONDecodeError):self.reply(400,{'error':'Richiesta non valida, scaduta o non disponibile.'})
                except (OSError,TimeoutError):pass
                except Exception:self.reply(500,{'error':'Errore remoto; controlla il CMD locale.'})
            do_GET=handle_request;do_POST=handle_request
        class Server(ThreadingHTTPServer):
            daemon_threads=True
            allow_reuse_address=False
            allow_reuse_port=False
            def server_bind(self):
                # HTTPServer's SO_REUSEADDR default permits multiple simultaneous
                # listeners on Windows, routing pair requests to the wrong process.
                if os.name=='nt':self.socket.setsockopt(socket.SOL_SOCKET,socket.SO_EXCLUSIVEADDRUSE,1)
                super().server_bind()
            def get_request(self):
                sock,address=super().get_request();sock.settimeout(5)
                if context is None:return sock,address
                try:return context.wrap_socket(sock,server_side=True),address
                except BaseException:sock.close();raise
        try:self.server=Server((bind,port),Handler)
        except OSError as exc:
            if getattr(exc,'winerror',None) in (10013,10048) or exc.errno in (48,98):raise ValueError('Porta Remote già occupata: '+bind+':'+str(port)+'. Usa freellmapi --remote-pair per il bridge esistente oppure avvia con --remote-port PORTA_LIBERA.') from exc
            raise
        self.url=('http://' if private_vpn else 'https://')+('127.0.0.1' if bind=='0.0.0.0' else bind)+':'+str(self.server.server_port)
        threading.Thread(target=self.server.serve_forever,daemon=True,name='freellmapi-remote').start();_active=self
        try:
            from remote_pairing import LocalPairing
            self.local_pairing=LocalPairing(self).start()
        except Exception:
            self.close();raise
        return self
    def close(self):
        global _active
        self.trading.close()
        with self.cv:self.closed=True;self.cv.notify_all()
        if self.local_pairing:self.local_pairing.close();self.local_pairing=None
        if self.server:self.server.shutdown();self.server.server_close()
        if _active is self:_active=None
        (self.folder/'pair.png').unlink(missing_ok=True)
        (self.folder/'pair.html').unlink(missing_ok=True)

@serialized
def snapshot(agent,catalog):
    if callable(catalog):catalog=catalog()
    progress=getattr(agent,'_request_progress',None);budget=getattr(agent,'budget',None)
    measured=getattr(agent,'phase_metrics',None)
    messages=[{'role':m['role'],'content':redact(m['content'])[-12000:]} for m in list(agent.messages)[-100:] if m.get('role') in ('user','assistant') and isinstance(m.get('content'),str) and m['content']]
    from model_health import chat_model
    return {'memory':{'connected':bool(getattr(agent,'memory',None)),'vault':str(getattr(agent,'vault_root','') or '')},'phase_metrics':measured.snapshot() if measured else None,'name':'freellmapi','session':agent.session.stem,'project':redact(str(agent.root)),'model':agent.model,'actual_model':getattr(agent,'last_successful_model',None),'mode':agent.mode,'api':getattr(agent,'api_state','non verificata'),'progress':progress.render() if progress else None,'request_progress':progress.snapshot() if progress and callable(getattr(progress,'snapshot',None)) else None,'tokens':getattr(budget,'tokens',0),'requests_used':getattr(budget,'steps',0),'models':list(dict.fromkeys(['auto','auto:reliable','auto:smart','auto:fast']+[m['id'] for m in list(catalog) if chat_model(m)]))[:1000],'sessions':[{k:redact(row.get(k)) if isinstance(row.get(k),str) else row.get(k) for k in ('id','title','root','updated','messages')} for row in agent.sessions.list()[:80]] if getattr(agent,'sessions',None) else [],'messages':messages[-60:]}

def show_pair(remote,url=None,open_browser=False):
    pair=remote.pair(url)
    print_pair(pair,open_browser=open_browser)
    return pair


def print_pair(pair,open_browser=False):
    import terminal_ui as ui
    with silent():
        ui.write('freellmapi Remote · abbinamento valido '+str(pair['expires_seconds'])+' secondi (solo sul PC)')
        ui.write('PC: '+pair['url']+'\nCodice monouso: '+pair['code']+'\nTrasporto: '+('HTTPS · fingerprint '+pair['fingerprint'] if pair['fingerprint'] else 'tunnel privato VPN/USB · fingerprint non richiesto'))
        ui.write('Il codice serve solo al primo abbinamento. Poi usa il collegamento salvato nell’app.')
        ui.write('QR: '+pair['qr']+'\nLink per l’app: '+pair['link'])
        if open_browser:
            try:
                from remote_pair_browser import open_pair
                result=open_pair(pair)
                ui.write('QR aperto nel browser predefinito.' if result['opened'] else 'Pagina QR pronta: '+result['page']+' · il browser non ha confermato l’apertura.')
            except (OSError,ValueError,KeyError) as error:ui.write('QR salvato; apertura browser non riuscita: '+type(error).__name__+'. Usa /remote qr per riprovare.')

def command(agent,prompt,home,catalog,port=31416,open_browser=True):
    words=prompt.strip().split()
    if len(words)>1 and words[0].casefold() in ('freellmapi','freellmapi.cmd','freellmapi.exe') and words[1]=='--remote-pair':
        from remote_pairing import request_pair,pairing_seconds
        import terminal_ui as ui
        seconds=120
        if len(words)>2:
            if len(words)!=4 or words[2]!='--pair-seconds' or not words[3].isdigit():
                ui.write('Comando locale: freellmapi --remote-pair [--pair-seconds 120..600]. Nessuna richiesta inviata al modello.');return True
            try:seconds=pairing_seconds(int(words[3]))
            except ValueError as error:ui.write('Remote locale: '+str(error));return True
        try:
            local=active()
            print_pair(local.pair(ttl=seconds) if local else request_pair(home,seconds),open_browser=open_browser)
        except (ValueError,OSError,TimeoutError) as error:ui.write('Remote locale: '+str(error)+' Nessuna richiesta API eseguita.')
        return True
    if not prompt.startswith('/remote'):return False
    import terminal_ui as ui
    parts=prompt.split(maxsplit=2);action=parts[1] if len(parts)>1 else 'status'
    remote=active()
    if action=='start':
        if remote:
            ui.write('Remote già attivo: '+remote.url);show_pair(remote,open_browser=open_browser)
        else:
            vpn=vpn_address();bind=parts[2] if len(parts)>2 else vpn or '127.0.0.1'
            usb=bind=='usb'
            if usb:bind='127.0.0.1'
            import _thread
            try:remote=Remote(home,lambda:snapshot(agent,catalog),_thread.interrupt_main).start(bind,port=port,private_vpn=usb or bool(vpn and bind==vpn))
            except ValueError as error:
                if 'Porta Remote già occupata' not in str(error):raise
                from remote_pairing import request_pair
                try:pair=request_pair(home)
                except (ValueError,OSError,TimeoutError):
                    ui.write(str(error)+'\nÈ un conflitto locale di porta. Nessuna richiesta al modello; scegli un’altra porta o usa il bridge già aperto.');return True
                ui.write('Remote già in esecuzione in un altro processo: '+pair['url']+'. Questa chat CMD resta locale; il telefono si collega alla sessione del bridge esistente.')
                print_pair(pair,open_browser=open_browser);return True
            show_pair(remote,open_browser=open_browser)
            if bind=='127.0.0.1':ui.write('Solo accesso USB/locale: per fuori casa connetti la VPN privata sul PC e sul telefono, poi /remote stop e /remote start.')
    elif action=='pair':
        if not remote:
            from remote_pairing import request_pair
            try:print_pair(request_pair(home),open_browser=open_browser)
            except (ValueError,OSError,TimeoutError) as error:ui.write('Remote locale: '+str(error)+' Nessuna richiesta API eseguita.')
            return True
        show_pair(remote,parts[2] if len(parts)>2 else None,open_browser=open_browser)
    elif action=='qr':
        if remote:show_pair(remote,open_browser=True)
        else:
            from remote_pairing import request_pair
            print_pair(request_pair(home),open_browser=True)
    elif action=='revoke':
        if remote:remote.revoke();ui.write('Dispositivi remoti revocati; coda svuotata. Il lavoro già iniziato non viene riavvolto.')
    elif action=='stop':
        if remote:remote.close();ui.write('Collegamento remoto chiuso; sessione CMD conservata.')
    elif action=='status':ui.write('Remote: '+(remote.url+' · '+str(len(remote.devices))+' dispositivi abbinati' if remote else 'spento'))
    else:ui.write('/remote start [IP|usb] · /remote pair [URL HTTPS] · /remote qr · /remote status · /remote revoke · /remote stop')
    return True
