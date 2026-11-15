"""Isolate optional background storage faults from foreground remote messages."""
import json
import re
import sqlite3
import threading
import time
import traceback
from pathlib import Path
from secret_output import redact

_lock=threading.Lock()


def diagnostic(home,source,error):
    text=re.sub(r'(?i)\b0x[a-f0-9]{64}\b','[REDACTED]',redact(str(error)))[:1000]
    row={'time':time.time(),'source':source,'type':type(error).__name__,'message':text,
         'sqlite_code':getattr(error,'sqlite_errorname',None),
         'notes':[redact(note) for note in getattr(error,'__notes__',[])],
         'frames':[{'file':f.filename,'line':f.lineno,'function':f.name} for f in traceback.extract_tb(error.__traceback__)[-8:]]}
    try:
        path=Path(home)/'diagnostics/runtime-errors.jsonl';path.parent.mkdir(parents=True,exist_ok=True)
        with _lock,path.open('a',encoding='utf-8') as stream:stream.write(json.dumps(row,ensure_ascii=False)+'\n')
    except OSError:pass
    return row


class BackgroundPoll:
    def __init__(self,home,output):self.home=home;self.output=output;self.retry={}

    def run(self,name,scope,callback,critical=False):
        key=(name,str(scope))
        if time.monotonic()<self.retry.get(key,0):return None
        try:
            result=callback()
            self.retry[key]=time.monotonic()+1
            return result
        except (sqlite3.Error,OSError) as error:
            diagnostic(self.home,'background:'+name,error)
            self.retry[key]=float('inf') if critical else time.monotonic()+60
            self.output('Servizio '+name+' sospeso per errore di archivio ('+type(error).__name__+'). '+
                        ('Nessun nuovo ciclo automatico finanziario; verifica e riavvia per ripristinarlo.' if critical else 'Nuova verifica tra 60s.')+
                        ' La chat rimane disponibile; dettagli in diagnostics/runtime-errors.jsonl.')
            return None
