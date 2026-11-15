"""Explicit natural-language self-test requests use the installed CLI's local suite."""
from pathlib import Path
import re
import subprocess
import sys
import time
import uuid
from tool_routing import normalize


def requested(prompt):
    text=normalize(prompt).strip().rstrip('.!')
    return bool(re.fullmatch(r'(?:per favore\s+)?(?:fai|esegui|avvia)\s+(?:(?:dei|i|tutti i|alcuni)\s+)?test\s+(?:su|di|per)\s+freellmapi',text)
                or re.fullmatch(r'(?:testa|collauda)\s+freellmapi',text)
                or re.fullmatch(r'(?:run|execute)\s+(?:the\s+)?tests\s+(?:on|for)\s+freellmapi',text))


def run(home,output,timeout=180):
    root=Path(__file__).resolve().parent
    folder=Path(home)/'self-tests'; folder.mkdir(parents=True,exist_ok=True)
    log=folder/(uuid.uuid4().hex+'.log')
    command=[sys.executable,'-X','utf8','-m','unittest','discover','-s',str(root),'-p','test_*.py']
    output('Test locali di freellmapi: '+str(root)+'\nLog: '+str(log))
    started=time.monotonic(); process=None
    try:
        with log.open('w',encoding='utf-8') as stream:
            process=subprocess.Popen(command,cwd=root,stdout=stream,stderr=subprocess.STDOUT)
            while True:
                remaining=timeout-(time.monotonic()-started)
                if remaining<=0: raise subprocess.TimeoutExpired(command,timeout)
                try: process.wait(timeout=min(5,remaining)); break
                except subprocess.TimeoutExpired:
                    output(f'Test locali in esecuzione · {time.monotonic()-started:.0f}s · log {log.stat().st_size} byte')
    except (KeyboardInterrupt,subprocess.TimeoutExpired):
        if process and process.poll() is None:
            process.terminate()
            try: process.wait(timeout=5)
            except subprocess.TimeoutExpired: process.kill(); process.wait()
        return {'ok':False,'summary':'Test interrotti o limite di tempo raggiunto. Nessun esito positivo dichiarato.','log':str(log)}
    with log.open('rb') as stream:
        stream.seek(max(0,log.stat().st_size-8000)); tail=stream.read().decode('utf-8',errors='replace')
    summary=re.search(r'Ran \d+ tests? in [^\r\n]+',tail)
    ok=process.returncode==0 and bool(summary) and bool(re.search(r'^OK(?:\s|$)',tail,re.M))
    return {'ok':ok,'exit_code':process.returncode,'summary':(summary[0] if summary else 'Riepilogo test non disponibile')+' — '+('SUPERATI' if ok else 'NON SUPERATI'),
            'log':str(log),'scope':'Suite locale; non certifica i provider, tutti i modelli o i dispositivi esterni.'}
