"""One coordinator may write a session; other clients attach or fail closed."""
from pathlib import Path
import threading
_runtime=threading.local()

def close_current():
    lease=getattr(_runtime,'lease',None)
    if lease:lease.close()

class SessionLease:
    def __init__(self,session):
        self.session=Path(session);self.stream=None
        folder=self.session.parent.parent/'live-locks';folder.mkdir(exist_ok=True)
        stream=(folder/(self.session.stem+'.lock')).open('a+b')
        try:
            import os
            if os.fstat(stream.fileno()).st_size==0:stream.write(b'0');stream.flush()
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream,fcntl.LOCK_EX|fcntl.LOCK_NB)
        except OSError:
            stream.close();raise ValueError('Sessione già attiva in un altro coordinatore. Collegati al Remote esistente; nessun secondo agente avviato.') from None
        self.stream=stream
        _runtime.lease=self
    def close(self):
        if self.stream:self.stream.close();self.stream=None
        if getattr(_runtime,'lease',None) is self:del _runtime.lease
    def __del__(self):self.close()
