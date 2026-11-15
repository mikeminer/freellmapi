"""LSP stdio client with framed JSON-RPC, deadlines and no server-applied edits."""
import json
import os
from pathlib import Path
import queue
import subprocess
import threading


class LanguageServer:
    def __init__(self):
        self.process=None; self.pending={}; self.serial=0; self.lock=threading.RLock(); self.documents={}; self.diagnostics={}; self.logs=[]

    def send(self,data):
        raw=json.dumps(data).encode('utf-8')
        with self.lock:
            if not self.process or self.process.poll() is not None: raise RuntimeError('Language server non attivo.')
            self.process.stdin.write(f'Content-Length: {len(raw)}\r\n\r\n'.encode()+raw); self.process.stdin.flush()

    def notify(self,method,params): self.send({'jsonrpc':'2.0','method':method,'params':params})

    def reader(self,process):
        try:
            while True:
                headers={}
                while True:
                    line=process.stdout.readline(8192)
                    if not line: return
                    if line in (b'\r\n',b'\n'): break
                    key,value=line.decode('ascii').split(':',1); headers[key.lower()]=value.strip()
                size=int(headers['content-length'])
                if not 0<size<=8_000_000: raise ValueError('Frame LSP fuori limite.')
                raw=process.stdout.read(size)
                if len(raw)!=size: return
                data=json.loads(raw)
                if 'method' in data and 'id' in data:
                    if data['method']=='workspace/configuration': reply={'result':[None for _ in data.get('params',{}).get('items',[])]}
                    else: reply={'error':{'code':-32601,'message':'Client method not supported; no edits applied'}}
                    self.send({'jsonrpc':'2.0','id':data['id'],**reply})
                elif 'id' in data:
                    with self.lock: target=self.pending.get(data['id'])
                    if target: target.put(data)
                elif data.get('method')=='textDocument/publishDiagnostics':
                    params=data['params']; self.diagnostics[params['uri']]=params
                elif data.get('method') in ('window/logMessage','window/showMessage'):
                    self.logs.append(data.get('params',{})); self.logs=self.logs[-10:]
        except (OSError,ValueError,KeyError): pass
        finally:
            with self.lock:
                for target in self.pending.values(): target.put({'error':{'message':'Language server disconnesso.'}})

    def request(self,method,params):
        with self.lock:
            self.serial+=1; ident=self.serial; target=queue.Queue(); self.pending[ident]=target
        try:
            self.send({'jsonrpc':'2.0','id':ident,'method':method,'params':params})
            try: data=target.get(timeout=15)
            except queue.Empty:
                self.notify('$/cancelRequest',{'id':ident}); raise TimeoutError('LSP timeout: '+method) from None
            if 'error' in data: raise RuntimeError(str(data['error']))
            return data.get('result')
        finally:
            with self.lock: self.pending.pop(ident,None)

    def start(self,command,args,root):
        import shutil
        if not shutil.which(command) and not Path(command).is_file(): raise ValueError('Eseguibile language server non trovato; eventuale server attivo conservato.')
        self.close(); self.root=Path(root).resolve()
        self.process=subprocess.Popen([command,*args],cwd=root,stdin=subprocess.PIPE,stdout=subprocess.PIPE,stderr=subprocess.DEVNULL,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
        self.thread=threading.Thread(target=self.reader,args=(self.process,),daemon=True); self.thread.start()
        try:
            result=self.request('initialize',{'processId':os.getpid(),'rootUri':self.root.as_uri(),'capabilities':{'workspace':{'applyEdit':False},'textDocument':{'publishDiagnostics':{'versionSupport':True}}},'workspaceFolders':[{'uri':self.root.as_uri(),'name':self.root.name}]})
            self.notify('initialized',{}); return result
        except BaseException: self.close(); raise

    def query(self,path,action,line=1,character=0,language=None):
        if not self.process or self.process.poll() is not None: raise ValueError('Language server non avviato: usa lsp_start.')
        path=Path(path).resolve()
        if not path.is_relative_to(self.root): raise ValueError('File fuori dal workspace LSP.')
        if path.stat().st_size>1_000_000: raise ValueError('Documento LSP oltre 1 MB.')
        text=path.read_text(encoding='utf-8-sig'); uri=path.as_uri()
        known=self.documents.get(uri)
        if known is None:
            version=1
            inferred={'.py':'python','.js':'javascript','.ts':'typescript','.tsx':'typescriptreact','.jsx':'javascriptreact','.json':'json','.cs':'csharp','.go':'go','.rs':'rust'}.get(path.suffix,'plaintext')
            self.notify('textDocument/didOpen',{'textDocument':{'uri':uri,'languageId':language or inferred,'version':version,'text':text}})
        else:
            version=known[0]
            if text!=known[1]:
                version+=1; self.notify('textDocument/didChange',{'textDocument':{'uri':uri,'version':version},'contentChanges':[{'text':text}]})
        self.documents[uri]=(version,text)
        if action=='diagnostics':
            from urllib.parse import unquote,urlsplit
            import time
            # LSP servers may percent-encode/normalize Windows drive letter case.
            canonical=lambda value: unquote(urlsplit(value).path).casefold() if os.name=='nt' else unquote(urlsplit(value).path)
            deadline=time.monotonic()+3
            while True:
                latest=next((d for key,d in self.diagnostics.items() if canonical(key)==canonical(uri)),None)
                ready=latest is not None and latest.get('version')==version
                if ready or time.monotonic()>=deadline: break
                time.sleep(.05)
            return {'status':'ready' if ready else 'pending','document_version':version,'latest_published':latest,
                    'diagnostics_count':len(latest.get('diagnostics',[])) if ready else None,
                    'server_logs':self.logs,'note':'pending significa verifica INCOMPLETA: non dichiarare assenza di errori. Ripeti la richiesta dopo attesa; ready vale solo per la versione indicata.'}
        methods={'definition':'textDocument/definition','references':'textDocument/references','hover':'textDocument/hover','symbols':'textDocument/documentSymbol'}
        if action not in methods or line<1 or character<0: raise ValueError('Azione/posizione LSP non valida.')
        params={'textDocument':{'uri':uri}}
        if action!='symbols': params['position']={'line':line-1,'character':character}
        if action=='references': params['context']={'includeDeclaration':True}
        return self.request(methods[action],params)

    def close(self):
        process=self.process
        if not process: return
        if process.poll() is None:
            process.terminate()
            try: process.wait(timeout=3)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        if hasattr(self,'thread'): self.thread.join(2)
        for stream in (process.stdin,process.stdout):
            if stream: stream.close()
        self.process=None; self.documents.clear(); self.diagnostics.clear()
