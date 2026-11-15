"""Session-local scheduled prompts; no execution while the CLI is closed."""
import json
from pathlib import Path
import time
import uuid
from reliable_runtime import atomic_bytes,LOCK


class Schedule:
    def __init__(self,session): self.path=Path(session).with_suffix('.schedule.json')
    def read(self): return json.loads(self.path.read_text()) if self.path.exists() else {}
    def save(self,data): atomic_bytes(self.path,json.dumps(data,ensure_ascii=False).encode())
    def create(self,prompt,delay=60,interval=0):
        if not prompt.strip() or len(prompt)>10000: raise ValueError('Prompt vuoto o troppo lungo.')
        if not 1<=delay<=86400 or interval and not 60<=interval<=86400: raise ValueError('delay 1–86400; interval 0 oppure 60–86400 secondi.')
        with LOCK:
            data=self.read()
            if sum(r['enabled'] for r in data.values())>=10: raise ValueError('Massimo 10 pianificazioni attive.')
            ident=uuid.uuid4().hex[:12]; now=time.time()
            row={'id':ident,'prompt':prompt,'next_due':now+delay,'interval':interval,'expires':now+86400,'enabled':True,'state':'scheduled'}
            data[ident]=row; self.save(data); return row
    def delete(self,ident):
        with LOCK:
            data=self.read()
            if ident not in data: raise ValueError('ID sconosciuto: consulta schedule_list.')
            data[ident]['enabled']=False; data[ident]['state']='cancelled'; self.save(data)
        return {'cancelled':ident}
    def poll(self):
        with LOCK:
            data=self.read(); now=time.time()
            for row in data.values():
                if row['enabled'] and row['expires']>now and row['next_due']<=now:
                    row['state']='dispatched'; row['last_dispatched']=now
                    if row['interval']: row['next_due']=now+row['interval']
                    else: row['enabled']=False
                    self.save(data); return '/_scheduled '+row['id']
        return None
    def finish(self,ident,success):
        with LOCK:
            data=self.read(); data[ident]['state']='finished' if success else 'stopped'
            if not success: data[ident]['enabled']=False
            self.save(data)
