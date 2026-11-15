"""Pinned wheels, isolated venvs, hash lock and pip check. No global pip installs."""
import email,json,os,re,subprocess,sys,uuid,zipfile,time
from pathlib import Path
import repair_lab as lab

def command(argv,log,timeout=300):
    with Path(log).open('w',encoding='utf-8') as stream:
        process=subprocess.Popen(argv,stdout=stream,stderr=subprocess.STDOUT)
        started=time.monotonic()
        try:
            while process.poll() is None:
                remaining=timeout-(time.monotonic()-started)
                if remaining<=0: raise subprocess.TimeoutExpired(argv,timeout)
                try: process.wait(timeout=min(10,remaining))
                except subprocess.TimeoutExpired: print(f'Dipendenze: {time.monotonic()-started:.0f}s · log {log}',flush=True)
        except (KeyboardInterrupt,subprocess.TimeoutExpired) as error:
            if os.name=='nt': subprocess.run(['taskkill','/PID',str(process.pid),'/T','/F'],stdout=subprocess.DEVNULL,stderr=subprocess.DEVNULL)
            else: process.kill()
            process.wait()
            if isinstance(error,KeyboardInterrupt): raise
            raise ValueError('Timeout dipendenze; ambiente precedente intatto. Log: '+str(log)) from None
    if process.returncode: raise ValueError('Comando dipendenze fallito. Log: '+str(log))

def requirements(lines):
    if not isinstance(lines,list) or not lines or len(lines)>40: raise ValueError('Specifica 1..40 pacchetti name==version.')
    result={}
    for line in lines:
        if not isinstance(line,str) or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*==[A-Za-z0-9][A-Za-z0-9_.+!-]*',line): raise ValueError('Solo pacchetti PyPI con versione esatta; niente URL, opzioni, range o codice sorgente.')
        name,version=line.split('==');result[re.sub(r'[-_.]+','-',name).lower()]=version
    return result

def wheel_lock(wheels):
    rows=[];seen=set()
    for wheel in sorted(Path(wheels).glob('*.whl')):
        with zipfile.ZipFile(wheel) as archive:
            files=[n for n in archive.namelist() if n.endswith('.dist-info/METADATA')]
            if len(files)!=1: raise ValueError('Metadata wheel ambigui.')
            metadata=email.message_from_bytes(archive.read(files[0]));name=metadata['Name'];version=metadata['Version']
        requirements([name+'=='+version])
        if name.lower() in seen: raise ValueError('Versioni duplicate nel wheelhouse.')
        seen.add(name.lower());rows.append(name+'=='+version+' --hash=sha256:'+lab.digest(wheel))
    if not rows: raise ValueError('Nessuna wheel scaricata.')
    return '\n'.join(rows)+'\n'

def prepare(home,ident,requested):
    folder,record=lab.load(home,ident)
    if record['state'] not in ('staged','edited','failed','tested'): raise ValueError('Release non modificabile.')
    base=[]
    source=lab.ROOT/'requirements.txt'
    if source.exists(): base=[line.strip() for line in source.read_text().splitlines() if line.strip() and not line.startswith('#')]
    pointer=Path(home)/'runtime-environment.json'
    if pointer.exists():
        active=json.loads(pointer.read_text(encoding='utf-8'));_,previous=lab.load(home,active['repair_id']);dep=previous.get('dependencies',{})
        if dep.get('requested'):
            if lab.digest(dep['requested'])!=dep['requested_hash']: raise ValueError('Specifiche dipendenze attive modificate.')
            base=Path(dep['requested']).read_text(encoding='utf-8').splitlines()
    pins=requirements(base) if base else {};pins.update(requirements(requested))
    env=Path(home)/'dependency-envs'/uuid.uuid4().hex;env.mkdir(parents=True)
    wheels=env/'wheels';wheels.mkdir();req=env/'requested.txt';req.write_text('\n'.join(k+'=='+v for k,v in pins.items()),encoding='utf-8')
    command([sys.executable,'-m','pip','--isolated','--disable-pip-version-check','download','--index-url','https://pypi.org/simple','--only-binary=:all:','--dest',str(wheels),'-r',str(req)],env/'download.log')
    lock=env/'requirements.lock';lock.write_text(wheel_lock(wheels),encoding='utf-8')
    venv=env/'venv';command([sys.executable,'-m','venv',str(venv)],env/'venv.log')
    python=venv/('Scripts/python.exe' if os.name=='nt' else 'bin/python')
    command([str(python),'-m','pip','--isolated','--disable-pip-version-check','install','--no-index','--find-links',str(wheels),'--only-binary=:all:','--require-hashes','-r',str(lock)],env/'install.log')
    command([str(python),'-m','pip','check'],env/'check.log')
    manifest=lab.manifest(venv);lab.store(env/'installed-files.json',manifest)
    report={'python':str(python),'venv':str(venv),'requested':str(req),'requested_hash':lab.digest(req),'lock':str(lock),'lock_hash':lab.digest(lock),'manifest':str(env/'installed-files.json'),'manifest_hash':lab.digest(env/'installed-files.json'),'checked':True}
    record['dependencies']=report;record['state']='edited';record.pop('tested_manifest',None);lab.store(folder/'record.json',record)
    return report

def checked_python(record):
    dep=record.get('dependencies')
    if not dep: return None
    if not dep.get('checked') or lab.digest(dep['lock'])!=dep['lock_hash'] or lab.digest(dep['manifest'])!=dep['manifest_hash']: raise ValueError('Lock dipendenze modificato.')
    if lab.manifest(Path(dep['venv']))!=json.loads(Path(dep['manifest']).read_text(encoding='utf-8')): raise ValueError('Ambiente dipendenze modificato dopo installazione.')
    return dep['python']
