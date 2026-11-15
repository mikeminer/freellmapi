"""Reuse a verified Android debug build only while sources and APK remain identical."""
import hashlib,json
from pathlib import Path
from work_ledger import load,save

def fingerprint(root,command):
    root=Path(root)
    if 'build-debug.ps1' not in command or not (root/'build-debug.ps1').is_file():return None
    files=[]
    for folder in ('src','public','android/app/src/main/java','android/app/src/main/res'):
        base=root/folder
        if base.exists():files.extend(p for p in base.rglob('*') if p.is_file() and not p.is_symlink())
    for name in ('build-debug.ps1','index.html','package.json','package-lock.json','vite.config.mjs','android/build.gradle','android/settings.gradle','android/app/build.gradle','android/app/src/main/AndroidManifest.xml'):
        if (root/name).is_file():files.append(root/name)
    return hashlib.sha256(b''.join(str(p.relative_to(root)).encode()+b'\0'+p.read_bytes() for p in sorted(set(files)))).hexdigest()

def cached(session,root,command):
    source=fingerprint(root,command)
    if source is None:return None
    entry=load(session).get('build_checkpoint',{})
    apk=Path(root)/'android/app/build/outputs/apk/debug/app-debug.apk'
    if entry.get('source')==source and apk.exists() and hashlib.sha256(apk.read_bytes()).hexdigest()==entry.get('apk_sha256'):
        return 'Exit code: 0\nBuild già verificata e invariata: nessun comando rieseguito. Continua con le verifiche mancanti.\nAPK: '+str(apk)+'\nSHA256: '+entry['apk_sha256']
    return None

def record(session,root,command,result):
    source=fingerprint(root,command);apk=Path(root)/'android/app/build/outputs/apk/debug/app-debug.apk'
    if source and str(result).startswith('Exit code: 0') and apk.is_file():
        data=load(session);data['build_checkpoint']={'source':source,'apk_sha256':hashlib.sha256(apk.read_bytes()).hexdigest()};save(session,data)
