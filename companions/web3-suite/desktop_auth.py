"""Local SSO through the installed Tailscale daemon's authenticated identity.

No Google password/token, wallet key, global VPN logout, or remote OIDC server.
The Windows-user-protected app session is bound to a verified tailnet principal.
"""
import ipaddress,json,os,subprocess,threading,time
from datetime import datetime,timezone
from pathlib import Path

class LoginRequired(RuntimeError):pass

def executable(name='tailscale.exe'):
    roots=[Path(os.environ.get('ProgramFiles',r'C:\Program Files'))/'Tailscale']
    for root in roots:
        path=root/name
        if path.is_file():return path
    raise LoginRequired('Tailscale non è installato sul PC.')

def windows_user():
    if os.name!='nt':return 'uid:'+str(os.getuid())
    import win32api,win32con,win32security
    token=win32security.OpenProcessToken(win32api.GetCurrentProcess(),win32con.TOKEN_QUERY)
    try:return win32security.ConvertSidToStringSid(win32security.GetTokenInformation(token,win32security.TokenUser)[0])
    finally:token.Close()

def _run(arguments):
    result=subprocess.run([str(executable()),*arguments],capture_output=True,text=True,encoding='utf-8',timeout=5,
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    if result.returncode:raise LoginRequired('Tailscale non disponibile. Apri Tailscale e completa l’accesso.')
    try:return json.loads(result.stdout)
    except ValueError:raise LoginRequired('Identità Tailscale non verificabile.') from None

def identity(run=_run):
    status=run(['status','--json']);node=status.get('Self') or {};tailnet=status.get('CurrentTailnet') or {}
    if status.get('BackendState')!='Running' or node.get('Online') is not True:
        raise LoginRequired('Collega Tailscale con il tuo account Google, poi premi Accedi.')
    if node.get('Tags') or node.get('Expired') or node.get('KeyExpired') or not tailnet.get('StableID') or not node.get('ID') or not node.get('UserID'):
        raise LoginRequired('Serve un dispositivo Tailscale personale con identità utente valida.')
    ips=[str(ipaddress.ip_address(ip)) for ip in node.get('TailscaleIPs',[]) if ipaddress.ip_address(ip).version==4]
    if not ips or ipaddress.ip_address(ips[0]) not in ipaddress.ip_network('100.64.0.0/10'):
        raise LoginRequired('Indirizzo del dispositivo Tailscale non verificato.')
    proof=run(['whois','--json',ips[0]]);profile=proof.get('UserProfile') or {};verified=proof.get('Node') or {}
    local=(status.get('User') or {}).get(str(node.get('UserID'))) or {}
    if (not profile.get('ID') or profile['ID']!=node.get('UserID') or verified.get('User')!=profile['ID'] or
        verified.get('StableID')!=node.get('ID') or verified.get('Tags') or verified.get('Expired') or
        not profile.get('LoginName') or profile['LoginName'].casefold()!=str(local.get('LoginName','')).casefold()):
        raise LoginRequired('La verifica dell’account Tailscale non coincide con il dispositivo attuale.')
    expiry=verified.get('KeyExpiry') or node.get('KeyExpiry')
    if expiry and datetime.fromisoformat(expiry.replace('Z','+00:00'))<=datetime.now(timezone.utc):
        raise LoginRequired('Accesso Tailscale scaduto: ricollega il dispositivo.')
    return {'user_id':str(profile['ID']),'tailnet_id':str(tailnet['StableID']),'node_id':str(verified['StableID']),
            'login':profile['LoginName'],'name':profile.get('DisplayName') or profile['LoginName'],
            'windows_user':windows_user(),'provider':'tailscale-local-sso'}

class DesktopAuth:
    def __init__(self,home,reader=identity):
        self.folder=Path(home)/'desktop';self.folder.mkdir(parents=True,exist_ok=True)
        self.owner=self.folder/'account-owner.dpapi';self.session=self.folder/'account-session.dpapi'
        self.reader=reader;self.lock=threading.RLock();self.generation=0;self.cached=None;self.checked=0
    def _get(self,path):
        from hl_credentials import get
        try:
            value=json.loads(get(path))
            return value if isinstance(value,dict) else None
        except (OSError,ValueError,RuntimeError):return None
    def _put(self,path,data):
        from hl_credentials import transform
        from reliable_runtime import atomic_bytes
        # Existing DPAPI helper decodes ASCII: JSON escapes preserve Unicode names.
        atomic_bytes(path,transform(json.dumps(data,ensure_ascii=True).encode('ascii'),True))
    @staticmethod
    def principal(value):return tuple(value.get(k) for k in ('user_id','tailnet_id','windows_user'))
    @classmethod
    def valid(cls,value):return isinstance(value,dict) and all(isinstance(v,str) and v for v in cls.principal(value))
    def status(self,fresh=False):
        with self.lock:
            if not fresh and self.cached is not None and time.monotonic()-self.checked<2:return dict(self.cached)
            saved=self._get(self.session);owner=self._get(self.owner)
            result={'authenticated':False,'state':'signed_out','provider':'tailscale-local-sso'}
            if saved and saved.get('authenticated') is True:
                try:
                    current=self.reader()
                    if not self.valid(owner) or not self.valid(saved) or self.principal(current)!=self.principal(owner) or self.principal(saved)!=self.principal(owner):
                        result.update(state='account_changed',message='L’account Tailscale è cambiato. Ricollega l’account proprietario.')
                    elif current['node_id']!=saved.get('node_id'):
                        result.update(state='device_changed',message='Il dispositivo Tailscale è cambiato: premi Accedi per verificarlo di nuovo.')
                    else:result.update(authenticated=True,state='signed_in',identity={k:current[k] for k in ('name','login','provider')})
                except (LoginRequired,OSError,ValueError,subprocess.TimeoutExpired) as error:
                    result.update(state='tailscale_unavailable',message=str(error) if isinstance(error,LoginRequired) else 'Tailscale non disponibile; accesso desktop sospeso.')
            self.cached=result;self.checked=time.monotonic();return dict(result)
    def require(self):
        result=self.status(fresh=True)
        if not result['authenticated']:raise LoginRequired(result.get('message') or 'Accedi con Tailscale / Google per usare FREELLMAPI Desktop.')
        return result
    def login(self):
        with self.lock:
            current=self.verified_owner()
            self._put(self.session,{**current,'authenticated':True});self.generation+=1;self.cached=None
            return self.status(fresh=True)
    def verified_owner(self):
        with self.lock:
            current=self.reader();owner=self._get(self.owner)
            if self.owner.exists() and not self.valid(owner):raise LoginRequired('Associazione account non leggibile. Nessun account sostituito.')
            if owner and self.principal(owner)!=self.principal(current):
                raise LoginRequired('Questi progetti appartengono a un altro account Tailscale. Torna all’account proprietario.')
            if not owner:self._put(self.owner,current)
            return current
    def logout(self):
        with self.lock:
            self._put(self.session,{'authenticated':False});self.generation+=1;self.cached=None
            return self.status()
    def open_tailscale(self):
        subprocess.Popen([str(executable('tailscale-ipn.exe'))],stdin=subprocess.DEVNULL,stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL,
                         creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        return {'opened':True,'authentication':'Complete any Google sign-in only in Tailscale/system browser.'}
