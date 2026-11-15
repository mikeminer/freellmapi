"""Git worktree isolation and reviewable checkpoints; not an OS security sandbox."""
import json
from pathlib import Path
import subprocess
import uuid

def git(root,*args):
    result=subprocess.run(['git','-C',str(root),*args],capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=60)
    if result.returncode: raise RuntimeError(result.stderr.strip() or result.stdout.strip())
    return result.stdout.strip()

def create(root,home):
    root=Path(root).resolve()
    top=Path(git(root,'rev-parse','--show-toplevel')).resolve()
    if top!=root: raise ValueError('La cartella corrente non è la radice Git. Non creo worktree dal repository padre: seleziona la radice del progetto.')
    if git(root,'status','--porcelain'): raise ValueError('Ci sono modifiche non salvate in Git. Crea un checkpoint prima di isolare, per non perderle nel worktree.')
    ident=uuid.uuid4().hex[:12]; target=Path(home)/'worktrees'/ident
    target.parent.mkdir(parents=True,exist_ok=True)
    branch='freellmapi/'+ident
    git(root,'worktree','add','-b',branch,str(target),'HEAD')
    return {'id':ident,'path':str(target),'branch':branch,'base':git(root,'rev-parse','HEAD'),
            'scope':'Worktree Git separato, non sandbox di sicurezza: shell e rete mantengono i permessi Windows.'}

def checkpoint(root,message):
    root=Path(root).resolve()
    if Path(git(root,'rev-parse','--show-toplevel')).resolve()!=root: raise ValueError('Checkpoint consentito solo alla radice Git del progetto.')
    # Tracked changes only: new files and secrets are not staged implicitly.
    git(root,'add','-u')
    if not git(root,'diff','--cached','--name-only'): return {'message':'Nessuna modifica tracciata. I nuovi file richiedono git add esplicito.'}
    git(root,'commit','-m',message)
    return {'commit':git(root,'rev-parse','HEAD'),'scope':'File tracciati; non annulla effetti esterni di shell/API.'}

def revert(root,commit):
    import re
    root=Path(root).resolve()
    if Path(git(root,'rev-parse','--show-toplevel')).resolve()!=root: raise ValueError('Revert consentito solo alla radice Git del progetto.')
    if not re.fullmatch(r'[a-fA-F0-9]{7,40}',commit): raise ValueError('Hash commit non valido.')
    if git(root,'status','--porcelain'): raise ValueError('Worktree non pulito: risolvi le modifiche prima del revert.')
    return git(root,'revert','--no-edit',commit)

def diff(root):
    return {'status':git(root,'status','--short'),'unstaged':git(root,'diff','--no-ext-diff')[:30000],
            'staged':git(root,'diff','--cached','--no-ext-diff')[:30000],
            'note':'Diff troncati a 30000 caratteri ciascuno; include file già staged nel checkpoint.'}
