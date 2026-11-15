"""Stage a project without build caches, credentials, symlinks or overwrites."""
import hashlib
import os
from pathlib import Path
import shutil
import tempfile

SKIP_DIRS={'.git','.gradle','.cache','.ssh','.aws','.azure','.idea'}
SKIP_FILES={'.npmrc','.pypirc','id_rsa','id_ed25519','credentials.json'}

def is_link(path):
    return path.is_symlink() or getattr(path,'is_junction',lambda:False)()


def existing_stage(agent,source):
    from work_ledger import load
    goal=load(agent.session).get('android_delivery',{})
    if not goal.get('required') or goal.get('status')=='completed' or not goal.get('base_project'):
        return None
    if Path(source).resolve()!=Path(goal['base_project']).resolve():return None
    candidates=[]
    if goal.get('apk_path'):
        apk=Path(goal['apk_path'])
        if len(apk.parents)>6:candidates.append(apk.parents[6])
    if goal.get('staged_project'):candidates.append(Path(goal['staged_project']))
    calls={c['id']:c['function']['name'] for m in agent.messages for c in m.get('tool_calls',[])}
    import json
    for message in agent.messages:
        if message.get('role')!='tool' or calls.get(message.get('tool_call_id'))!='project_copy':continue
        try:
            result=json.loads(message.get('content',''))
            if Path(result.get('source','')).resolve()==Path(source).resolve():candidates.append(Path(result['destination']))
        except (ValueError,KeyError,TypeError):continue
    for target in candidates:
        target=target.resolve()
        if target.is_relative_to(agent.root) and (target/'package.json').is_file() and (target/'android/app/build.gradle').is_file():
            return {'source':str(Path(source).resolve()),'destination':str(target),'copied':0,'changed':False,'reused':True,
                    'instruction':'Copia già presente per questa richiesta: nessuna nuova cartella o sovrascrittura. Continua qui conservando modifiche, build e contesto.'}
    return None


def ignored(relative):
    parts=relative.parts
    name=parts[-1].lower()
    return (any(p.lower() in SKIP_DIRS for p in parts) or
            name.startswith('.env') or name in SKIP_FILES or
            relative.suffix.lower() in ('.jks','.keystore','.p12','.pfx','.pem','.key') or
            (parts[0].lower() in ('dist','build','coverage')) or
            (parts[0].lower()=='android' and any(p.lower()=='build' for p in parts[1:])) or
            relative.as_posix().startswith('android/app/src/main/assets/public/'))


def stage(source, destination):
    source=Path(source).expanduser().resolve(strict=True)
    destination=Path(destination).resolve()
    if not source.is_dir() or source==destination or source.is_relative_to(destination) or destination.is_relative_to(source):
        raise ValueError('project_copy: cartelle sorgente/destinazione sovrapposte o non valide.')
    if not any((source/name).exists() for name in ('package.json','pyproject.toml','build.gradle','settings.gradle','Cargo.toml','go.mod','.git')):
        raise ValueError('project_copy: scegli la radice di un progetto, non una cartella personale o di sistema.')
    destination.mkdir(parents=True,exist_ok=True)
    copied=[]; conflicts=[]; reused=0; skipped=0; total=0
    for folder,dirs,files in os.walk(source,followlinks=False):
        base=Path(folder)
        dirs[:]=[d for d in dirs if not is_link(base/d) and not ignored((base/d).relative_to(source))]
        for name in files:
            original=base/name;relative=original.relative_to(source)
            if is_link(original) or ignored(relative): skipped+=1;continue
            target=destination/relative
            if any(is_link(p) for p in [target,*target.parents] if p.is_relative_to(destination)):
                raise ValueError('project_copy: link nella destinazione, nessuna sovrascrittura consentita.')
            total+=original.stat().st_size
            if len(copied)+reused+len(conflicts)>20000 or total>1_000_000_000:
                raise ValueError('project_copy: limite 20000 file / 1 GB raggiunto; copia parziale conservata. Riduci la sorgente.')
            if target.exists():
                if target.is_file() and original.stat().st_size==target.stat().st_size and hashlib.sha256(original.read_bytes()).digest()==hashlib.sha256(target.read_bytes()).digest():
                    reused+=1
                else:conflicts.append(str(relative))
                continue
            target.parent.mkdir(parents=True,exist_ok=True)
            handle,temporary=tempfile.mkstemp(prefix='.project-copy-',dir=target.parent)
            try:
                with os.fdopen(handle,'wb') as output,original.open('rb') as input_file:
                    shutil.copyfileobj(input_file,output)
                try:os.link(temporary,target) # atomic create; never overwrites a concurrent edit
                except FileExistsError:conflicts.append(str(relative));continue
                shutil.copystat(original,target)
                copied.append(str(relative))
            finally:Path(temporary).unlink(missing_ok=True)
    return {'source':str(source),'destination':str(destination),'copied':len(copied),'reused':reused,'skipped':skipped,
            'conflicts_preserved':conflicts[:100],'changed':bool(copied),'sample':copied[:30],
            'instruction':'Sorgenti preparati senza cache Gradle/build né credenziali comuni. Node_modules disponibili sono riusati tramite copia. File modificati esistenti conservati, non sovrascritti. Continua nella destinazione; non ricopiare tutto dopo un errore.'}
