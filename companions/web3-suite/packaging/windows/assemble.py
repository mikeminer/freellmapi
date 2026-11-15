"""Allowlisted payload assembly. Run after fetch_components and pip staging."""
import hashlib,json,os,shutil,subprocess,sys,zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1];OUT=ROOT/'dist/payload';CACHE=ROOT/'dist/downloads'

def extract(archive,destination,strip=False):
    destination=Path(destination);destination.mkdir(parents=True,exist_ok=True)
    with zipfile.ZipFile(archive) as z:
        for item in z.infolist():
            parts=Path(item.filename.replace('\\','/')).parts
            if strip:parts=parts[1:]
            if not parts:continue
            target=destination.joinpath(*parts).resolve()
            if not target.is_relative_to(destination.resolve()):raise ValueError('Archive path escapes destination')
            if item.is_dir():target.mkdir(parents=True,exist_ok=True)
            else:
                target.parent.mkdir(parents=True,exist_ok=True)
                with z.open(item) as src,target.open('wb') as dst:shutil.copyfileobj(src,dst)

def source_files(root=ROOT):
    for p in root.glob('*.py'):
        if not p.name.startswith('verify_'):yield p
    ignored={'node_modules','__pycache__','vendor','models','build','.venv'}
    for folder in ('desktop','integrations','examples','mobile','packaging'):
        for current,dirs,files in os.walk(root/folder):
            dirs[:]=[d for d in dirs if d not in ignored and not (Path(current)/d).is_symlink()]
            for name in files:
                p=Path(current)/name
                if p.is_symlink():continue
                if p.suffix not in ('.py','.mjs','.js','.css','.html','.java','.xml','.png','.ico','.md','.txt','.json','.iss','.lock'):continue
                if p.name in ('runtime-policy.json','dependency-audit.json') or p.suffix=='.pem':continue
                if p.suffix=='.json' and p.name not in ('package.json','package-lock.json','manifest.json','curve.json','amm.json') and folder not in ('desktop','packaging'):continue
                yield p

def main():
    lock=json.loads((HERE/'components.lock.json').read_text())
    for name,row in lock['components'].items():
        source=CACHE/row['file']
        if hashlib.sha256(source.read_bytes()).hexdigest()!=row['sha256']:raise ValueError('Asset checksum mismatch: '+name)
    destinations={'router':('router',False),'node':('runtime/node',True),'powershell':('runtime/powershell',False),
                  'git':('runtime/git',False),'adb':('runtime/adb',False)}
    for name,(target,strip) in destinations.items():
        extract(CACHE/lock['components'][name]['file'],OUT/target,strip)
    (OUT/'prerequisites').mkdir(exist_ok=True)
    shutil.copyfile(CACHE/lock['components']['tailscale']['file'],OUT/'prerequisites/tailscale.msi')
    shutil.copyfile(CACHE/lock['components']['vcredist']['file'],OUT/'prerequisites/vc_redist.x64.exe')
    for source in source_files():
        target=OUT/'agent'/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    for source,target in ((HERE/'launcher.py',OUT/'launcher/suite.py'),(HERE/'suite_setup.py',OUT/'agent/suite_setup.py'),
                          (ROOT/'desktop/freellmapi-official.ico',OUT/'assets/freellmapi.ico'),
                          (ROOT/'mobile/build/freellmapi-remote.apk',OUT/'mobile/freellmapi-remote.apk'),
                          (HERE/'USER_GUIDE.md',OUT/'docs/USER_GUIDE.md')):
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
    from stage_skills import stage
    bundled=stage(payload=OUT)
    # Console command is available in every CMD launched with the suite's bin
    # on PATH. The installer registers this directory for the current user.
    (OUT/'bin').mkdir(exist_ok=True)
    (OUT/'bin/freellmapi.cmd').write_text('@echo off\n"%~dp0..\\runtime\\python\\python.exe" -I -X utf8 "%~dp0..\\launcher\\suite.py" cmd %*\nexit /b %errorlevel%\n')
    (OUT/'suite-manifest.json').write_text(json.dumps({'version':'0.1.1','specialization':'Web3: Hyperliquid, Solana, Helius, Pump.fun, DevFridge Game Builder',
                                                    'bundled_skills':[{'id':p['id'],'version':p['version'],'files':len(p['files']),'source_commit':p['source_commit']} for p in bundled['packages']],
                                                    'components':lock['components'],'apk_sha256':hashlib.sha256((OUT/'mobile/freellmapi-remote.apk').read_bytes()).hexdigest()},indent=2))
    subprocess.run([str(OUT/'runtime/python/python.exe'),'-I','-c',
                    "import sys,pathlib;from markdown_it import MarkdownIt;p=pathlib.Path(sys.argv[1]);p.with_suffix('.html').write_text('<!doctype html><meta charset=utf-8><title>FREELLMAPI Web3 Suite</title><style>body{max-width:900px;margin:40px auto;padding:20px;font:17px/1.6 system-ui;color:#202630}pre{white-space:pre-wrap;background:#eee;padding:15px}a{color:#2864ce}</style>'+MarkdownIt('commonmark',{'html':False}).render(p.read_text(encoding='utf-8')),encoding='utf-8')",
                    str(OUT/'docs/USER_GUIDE.md')],check=True)
    print('Payload assembled:',OUT,flush=True)

if __name__=='__main__':main()
