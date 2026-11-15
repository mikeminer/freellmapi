"""Paired phone SSO: peer identity comes from Tailscale WhoIs, never phone JSON."""
import ipaddress,threading,time,os,contextlib
from datetime import datetime,timezone
from desktop_auth import DesktopAuth,LoginRequired,_run

_lock=threading.RLock()
class AccountRequired(PermissionError):pass

@contextlib.contextmanager
def registry_lock(path):
    path.parent.mkdir(parents=True,exist_ok=True);fd=os.open(path,os.O_CREAT|os.O_RDWR)
    try:
        if os.fstat(fd).st_size==0:os.write(fd,b'0')
        os.lseek(fd,0,os.SEEK_SET)
        if os.name=='nt':
            import msvcrt
            deadline=time.monotonic()+5
            while True:
                try:msvcrt.locking(fd,msvcrt.LK_NBLCK,1);break
                except OSError:
                    if time.monotonic()>=deadline:raise AccountRequired('Registro account occupato; nessun accesso modificato.')
                    time.sleep(.02)
        else:
            import fcntl
            fcntl.flock(fd,fcntl.LOCK_EX)
        yield
    finally:os.close(fd)

def peer_identity(home,address):
    ip=ipaddress.ip_address(address)
    if ip.version!=4 or ip not in ipaddress.ip_network('100.64.0.0/10'):raise LoginRequired('Accedi dal Seeker tramite la rete Tailscale del tuo account Google.')
    owner=DesktopAuth(home).verified_owner();proof=_run(['whois','--json',str(ip)])
    profile=proof.get('UserProfile') or {};node=proof.get('Node') or {}
    addresses=[ipaddress.ip_interface(value).ip for value in node.get('Addresses',[])]
    if (str(profile.get('ID'))!=owner['user_id'] or str(node.get('User'))!=owner['user_id'] or
        not node.get('StableID') or node.get('Tags') or node.get('Expired') or ip not in addresses):
        raise LoginRequired('Il telefono non appartiene all’account Tailscale proprietario del PC.')
    expiry=node.get('KeyExpiry')
    if expiry and datetime.fromisoformat(expiry.replace('Z','+00:00'))<=datetime.now(timezone.utc):raise LoginRequired('Accesso Tailscale del telefono scaduto.')
    return {**owner,'node_id':node['StableID']}

class MobileAccount:
    def __init__(self,home,reader=None):
        self.home=home;self.storage=DesktopAuth(home);self.path=self.storage.folder.parent/'remote/accounts.dpapi'
        self.reader=reader or (lambda address:peer_identity(home,address));self.cache={}
    def _rows(self):
        rows=self.storage._get(self.path)
        if self.path.exists() and rows is None:raise AccountRequired('Registro accessi non leggibile; nessun abbinamento cancellato.')
        return rows or {}
    def enrolled(self,device):
        with _lock:return device in self._rows()
    def status(self,device,address,fresh=False):
        with _lock:
            row=self._rows().get(device) or {};out={'authenticated':False,'state':'signed_out','provider':'tailscale-peer-sso'}
            if row.get('authenticated') is not True:return out
            try:
                cached=self.cache.get((device,address))
                current=cached[1] if not fresh and cached and time.monotonic()-cached[0]<3 else self.reader(address)
                if DesktopAuth.principal(current)!=DesktopAuth.principal(row) or current.get('node_id')!=row.get('node_id'):raise LoginRequired('Account o telefono Tailscale cambiato: accedi di nuovo con il proprietario.')
                self.cache[(device,address)]=(time.monotonic(),current)
                out.update(authenticated=True,state='signed_in',identity={k:current[k] for k in ('name','login')})
            except (LoginRequired,OSError,ValueError):out.update(state='tailscale_unavailable',message='Account Tailscale non verificato. Controlla la VPN e premi Accedi.')
            return out
    def login(self,device,address):
        with _lock,registry_lock(self.path.with_suffix('.lock')):
            current=self.reader(address);rows=self._rows();rows[device]={**current,'authenticated':True}
            self.storage._put(self.path,rows);self.cache.clear();return self.status(device,address,fresh=True)
    def logout(self,device):
        with _lock,registry_lock(self.path.with_suffix('.lock')):
            rows=self._rows();rows[device]={'authenticated':False};self.storage._put(self.path,rows);self.cache.clear()
            return {'authenticated':False,'state':'signed_out','provider':'tailscale-peer-sso'}
    def require(self,device,address):
        if not self.status(device,address)['authenticated']:raise AccountRequired('account_login_required')
