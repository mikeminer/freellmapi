"""Local Node Inspector debugger. No dependency on Claude or Codex."""
import json
from pathlib import Path
import queue
import re
import subprocess
import threading
import time


class Debugger:
    def __init__(self):
        self.process=None; self.socket=None; self.serial=0; self.pending={}
        self.events=[]; self.paused=None; self.lock=threading.RLock(); self.stderr_thread=None

    def start(self,script,args=()):
        import websocket
        self.close()
        script=Path(script).resolve()
        if not script.is_file(): raise ValueError('Script inesistente.')
        self.process=subprocess.Popen(['node','--inspect-brk=127.0.0.1:0',str(script),*args],
            cwd=script.parent,stdout=subprocess.DEVNULL,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='replace')
        urls=queue.Queue()
        process=self.process
        def stderr():
            for line in process.stderr:
                match=re.search(r'ws://127\.0\.0\.1:\d+/[\w-]+',line)
                if match: urls.put(match.group())
            urls.put(None)
        self.stderr_thread=threading.Thread(target=stderr,daemon=True); self.stderr_thread.start()
        try:
            url=urls.get(timeout=10)
            if not url: raise RuntimeError('Node Inspector non avviato.')
            self.socket=websocket.create_connection(url,timeout=10,suppress_origin=True,http_no_proxy=['127.0.0.1'])
            self.socket.settimeout(None)
            threading.Thread(target=self.reader,args=(self.socket,),daemon=True).start()
            self.call('Runtime.enable'); self.call('Debugger.enable')
            self.call('Runtime.runIfWaitingForDebugger')
            return self.state(wait=2)
        except BaseException:
            self.close(); raise

    def reader(self,sock):
        try:
            while True:
                raw=sock.recv()
                if not raw: break
                data=json.loads(raw)
                with self.lock:
                    if 'id' in data:
                        target=self.pending.get(data['id'])
                        if target: target.put(data)
                    else:
                        if data.get('method')=='Debugger.paused': self.paused=data.get('params')
                        elif data.get('method')=='Debugger.resumed': self.paused=None
                        if data.get('method') in ('Debugger.paused','Debugger.resumed','Runtime.exceptionThrown','Runtime.consoleAPICalled'):
                            self.events.append(data); self.events=self.events[-30:]
        except Exception: pass
        finally:
            with self.lock:
                for target in self.pending.values(): target.put({'error':{'message':'Inspector disconnesso.'}})

    def call(self,method,params=None):
        if not self.socket: raise ValueError('Avvia prima debug_start.')
        with self.lock:
            self.serial+=1; ident=self.serial; target=queue.Queue(); self.pending[ident]=target
            self.socket.send(json.dumps({'id':ident,'method':method,'params':params or {}}))
        try:
            response=target.get(timeout=10)
            if 'error' in response: raise RuntimeError(str(response['error']))
            return response.get('result',{})
        finally:
            with self.lock: self.pending.pop(ident,None)

    def state(self,wait=0):
        deadline=time.monotonic()+wait
        while wait and not self.paused and self.process and self.process.poll() is None and time.monotonic()<deadline: time.sleep(.025)
        return {'running':bool(self.process and self.process.poll() is None),'paused':self.paused,'events':self.events[-5:]}

    def breakpoint(self,path,line):
        if int(line)<1: raise ValueError('Le righe iniziano da 1.')
        return self.call('Debugger.setBreakpointByUrl',{'url':Path(path).resolve().as_uri(),'lineNumber':int(line)-1})

    def move(self,action):
        methods={'continue':'Debugger.resume','step_over':'Debugger.stepOver','step_into':'Debugger.stepInto','step_out':'Debugger.stepOut','pause':'Debugger.pause'}
        if action not in methods: raise ValueError('Azione: continue, step_over, step_into, step_out, pause.')
        if action!='pause' and not self.paused: raise ValueError('Il processo non è in pausa. Leggi debug_state.')
        self.paused=None; self.call(methods[action]); return self.state(wait=.5)

    def variables(self,frame=0):
        if not self.paused: raise ValueError('Nessuna pausa attiva: usa debug_state.')
        frames=self.paused['callFrames']
        if not 0<=frame<len(frames): raise ValueError('Indice frame non valido.')
        result=[]
        for scope in frames[frame]['scopeChain']:
            if scope['type']=='global': continue
            obj=scope.get('object',{}).get('objectId')
            if obj: result.append({'scope':scope['type'],'properties':self.call('Runtime.getProperties',{'objectId':obj,'ownProperties':True,'generatePreview':True}).get('result',[])[:100]})
        return result

    def close(self):
        if self.socket:
            self.socket.close(); self.socket=None
        if self.process and self.process.poll() is None:
            self.process.terminate()
            try: self.process.wait(timeout=3)
            except subprocess.TimeoutExpired: self.process.kill(); self.process.wait()
        if self.stderr_thread: self.stderr_thread.join(timeout=2)
        if self.process and self.process.stderr: self.process.stderr.close()
        self.process=None; self.paused=None; self.events=[]
