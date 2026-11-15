"""Explicitly reviewed hooks and deny rules. No automatic execution of project hooks."""
import fnmatch
import json
from pathlib import Path
import subprocess
import tempfile


class Policy:
    def __init__(self): self.config={'deny':[],'hooks':[]}

    def load(self,path,approve):
        path=Path(path).resolve()
        if path.stat().st_size>100000: raise ValueError('Configurazione oltre 100 KB.')
        config=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(config,dict) or set(config)-{'deny','hooks'}: raise ValueError('Campi ammessi: deny, hooks.')
        if not isinstance(config.get('deny',[]),list) or not all(isinstance(s,str) for s in config.get('deny',[])): raise ValueError('deny: elenco pattern nomi strumenti.')
        for hook in config.get('hooks',[]):
            if hook.get('event') not in ('pre_tool','post_tool'): raise ValueError('Evento hook: pre_tool o post_tool.')
            argv=hook.get('argv')
            if not isinstance(argv,list) or not argv or not all(isinstance(s,str) for s in argv): raise ValueError('argv: elenco non vuoto di stringhe.')
            if not isinstance(hook.get('tool','*'),str): raise ValueError('tool: pattern stringa.')
            if not isinstance(hook.get('timeout',10),int) or not 1<=hook.get('timeout',10)<=30: raise ValueError('Timeout hook: 1–30 secondi.')
        if not approve('Caricare regole/hook nativi (gli hook eseguono questi programmi locali):\n'+json.dumps(config,ensure_ascii=False,indent=2)): return {'loaded':False}
        self.config=config; return {'loaded':True,'snapshot':str(path),'note':'Modifiche successive richiedono un nuovo caricamento esplicito.'}

    def check(self,name):
        if any(fnmatch.fnmatchcase(name,p) for p in self.config.get('deny',[])): raise PermissionError('policy_denied: '+name+' è vietato anche in modalità auto.')

    def run(self,event,name,args,root,result=None):
        outputs=[]
        for hook in self.config.get('hooks',[]):
            if hook['event']!=event or not fnmatch.fnmatchcase(name,hook.get('tool','*')): continue
            payload=json.dumps({'event':event,'tool':name,'arguments':args,'result':result},ensure_ascii=False).encode()
            with tempfile.TemporaryFile() as log:
                try: process=subprocess.run(hook['argv'],input=payload,stdout=log,stderr=subprocess.STDOUT,cwd=root,timeout=hook.get('timeout',10))
                except subprocess.TimeoutExpired: raise RuntimeError('hook_timeout: '+event+'. Verifica il processo hook; nessuna ripetizione automatica.') from None
                log.seek(0); text=log.read(24000).decode('utf-8',errors='replace')
            if process.returncode: raise RuntimeError(f'hook_failed: {event} exit {process.returncode}: '+text)
            if text: outputs.append(text)
        return outputs
