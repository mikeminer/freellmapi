"""Stream from a regular file so daemon-inherited stdout cannot hold a turn open."""
import codecs
import subprocess
import time
import uuid
import threading
import queue
from secret_output import StreamFilter
from pathlib import Path
import terminal_ui as ui


def run(command,root,home,timeout=60):
    folder=Path(home)/'shell-logs'; folder.mkdir(parents=True,exist_ok=True)
    log=folder/(uuid.uuid4().hex+'.log')
    limit=min(600,max(1,int(timeout)))
    result=''
    events=queue.Queue()
    stop=threading.Event()
    from command_environment import environment
    proc=subprocess.Popen(['pwsh','-NoProfile','-Command',command],cwd=root,
                          stdin=subprocess.DEVNULL,stdout=subprocess.PIPE,stderr=subprocess.STDOUT,
                          env=environment(),
                          creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
    def collect():
        decoder=codecs.getincrementaldecoder('utf-8')('replace');sanitize=StreamFilter()
        try:
            while not stop.is_set():
                raw=proc.stdout.read1(4096)
                if not raw:break
                value=sanitize.feed(decoder.decode(raw))
                if value:events.put(value)
            value=sanitize.feed(decoder.decode(b'',final=True),final=True)
            if value:events.put(value)
        finally:
            events.put(None)
            proc.stdout.close()
    worker=threading.Thread(target=collect,daemon=True,name='shell-output-filter');worker.start()
    deadline=time.monotonic()+limit
    ui.write('Esecuzione in corso...')
    heartbeat=time.monotonic()+10
    with log.open('w',encoding='utf-8') as writer:
        def drain(final=False):
            nonlocal result
            while True:
                try:text=events.get_nowait()
                except queue.Empty:break
                if text is None:continue
                writer.write(text);writer.flush();ui.command_output(text);result=(result+text)[-24000:]
        try:
            while proc.poll() is None:
                drain()
                if time.monotonic()>=heartbeat:
                    ui.write(f'Processo {proc.pid} attivo · limite {limit}s · log {log}')
                    heartbeat=time.monotonic()+10
                if time.monotonic()>=deadline: raise subprocess.TimeoutExpired(command,limit)
                time.sleep(.1)
            # Read only the bytes already present after parent completion; no EOF wait on daemons.
            worker.join(timeout=.2)
            drain(final=True)
            stop.set()
        except (subprocess.TimeoutExpired,KeyboardInterrupt) as exc:
            subprocess.run(['taskkill','/PID',str(proc.pid),'/T','/F'],capture_output=True,timeout=10)
            try: proc.wait(timeout=5)
            except subprocess.TimeoutExpired: proc.kill()
            stop.set();worker.join(timeout=.2);drain(final=True)
            if isinstance(exc,KeyboardInterrupt): raise
            return f'Errore: Comando interrotto o timeout ({limit}s). Non rilanciare invariato: consulta il log e aumenta il timeout o usa process_start per lavori lunghi.\n{result}\nLog: {log}'
    return ('Errore: ' if proc.returncode else '')+f'Exit code: {proc.returncode}\n{result}\nLog: {log}'
