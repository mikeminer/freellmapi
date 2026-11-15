"""Optional Docker process boundary. Worktrees alone are not a security sandbox."""
from pathlib import Path
import re
import subprocess


def run(root,image,command,timeout=120):
    root=Path(root).resolve()
    if root==Path(root.anchor) or root==Path.home().resolve(): raise ValueError('Seleziona una cartella progetto specifica, non home/disco.')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_./:@-]{0,200}',image): raise ValueError('Nome immagine non valido.')
    # No automatic pulls; a chosen local image must exist before running.
    check=subprocess.run(['docker','image','inspect',image],capture_output=True,timeout=15)
    if check.returncode: raise ValueError('Immagine Docker non disponibile localmente. Installa/avvia Docker e prepara l’immagine scelta.')
    import uuid
    name='freellmapi-'+uuid.uuid4().hex[:12]
    args=['docker','run','--rm','--name',name,'--network','none','--cap-drop','ALL','--security-opt','no-new-privileges',
          '--pids-limit','128','--memory','1g','--cpus','1','--read-only','--tmpfs','/tmp:rw,noexec,nosuid,size=128m',
          '--mount',f'type=bind,source={root},target=/workspace','--workdir','/workspace',image,'sh','-lc',command]
    try:
        # Redirect output to file to avoid unbounded communicate buffers.
        import tempfile
        with tempfile.TemporaryFile() as log:
            process=subprocess.Popen(args,stdout=log,stderr=subprocess.STDOUT)
            try: code=process.wait(timeout=max(1,min(int(timeout),600)))
            except BaseException:
                subprocess.run(['docker','kill',name],capture_output=True,timeout=15)
                process.kill(); process.wait(); raise
            log.seek(0); raw=log.read(100001)
            return {'exit_code':code,'output':raw[:100000].decode('utf-8',errors='replace'),'truncated':len(raw)>100000,
                    'scope':'Rete disabilitata; solo la cartella progetto montata in scrittura. I suoi file restano modificabili.'}
    except FileNotFoundError: raise ValueError('Docker non installato.') from None
