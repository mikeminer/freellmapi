"""Allowlisted payload assembly. Run after fetch_components and pip staging."""
import argparse,hashlib,json,os,shutil,subprocess,sys,zipfile
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
    if (root/'requirements.txt').is_file():yield root/'requirements.txt'
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
    global OUT,CACHE
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output',type=Path,default=OUT)
    parser.add_argument('--cache',type=Path,default=CACHE)
    parser.add_argument('--skill-checkout',type=Path)
    parser.add_argument('--source-commit',required=True,help='Commit exported into this clean source tree.')
    args=parser.parse_args();OUT=args.output.resolve();CACHE=args.cache.resolve()
    if (OUT/'agent').exists():raise ValueError('Use fresh agent staging; existing files are not merged into a release.')
    if not __import__('re').fullmatch('[0-9a-f]{40}',args.source_commit):raise ValueError('Full source commit required')
    version=(HERE/'release-version.txt').read_text().strip()
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
    source_manifest={}
    for source in source_files():
        target=OUT/'agent'/source.relative_to(ROOT);target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        source_manifest[target.relative_to(OUT).as_posix()]=hashlib.sha256(source.read_bytes()).hexdigest()
    for source,target in ((HERE/'launcher.py',OUT/'launcher/suite.py'),(HERE/'suite_setup.py',OUT/'agent/suite_setup.py'),
                          (ROOT/'desktop/freellmapi-official.ico',OUT/'assets/freellmapi.ico'),
                          (ROOT/'mobile/build/freellmapi-remote.apk',OUT/'mobile/freellmapi-remote.apk'),
                          (HERE/'USER_GUIDE.md',OUT/'docs/USER_GUIDE.md')):
        target.parent.mkdir(parents=True,exist_ok=True);shutil.copyfile(source,target)
        source_manifest[target.relative_to(OUT).as_posix()]=hashlib.sha256(target.read_bytes()).hexdigest()
    from stage_skills import stage
    bundled=stage(checkout=args.skill_checkout,payload=OUT)
    # Console command is available in every CMD launched with the suite's bin
    # on PATH. The installer registers this directory for the current user.
    (OUT/'bin').mkdir(exist_ok=True)
    (OUT/'bin/freellmapi.cmd').write_text('@echo off\n"%~dp0..\\runtime\\python\\python.exe" -I -X utf8 "%~dp0..\\launcher\\suite.py" cmd %*\nexit /b %errorlevel%\n')
    subprocess.run([str(OUT/'runtime/python/python.exe'),'-I','-c',
                    "import sys,pathlib;from markdown_it import MarkdownIt;p=pathlib.Path(sys.argv[1]);p.with_suffix('.html').write_text('<!doctype html><meta charset=utf-8><title>FREELLMAPI Web3 Suite</title><style>body{max-width:900px;margin:40px auto;padding:20px;font:17px/1.6 system-ui;color:#202630}pre{white-space:pre-wrap;background:#eee;padding:15px}a{color:#2864ce}</style>'+MarkdownIt('commonmark',{'html':False}).render(p.read_text(encoding='utf-8')),encoding='utf-8')",
                    str(OUT/'docs/USER_GUIDE.md')],check=True)
    for path in (OUT/'docs/USER_GUIDE.html',OUT/'bin/freellmapi.cmd'):
        source_manifest[path.relative_to(OUT).as_posix()]=hashlib.sha256(path.read_bytes()).hexdigest()
    (OUT/'source-files.json').write_text(json.dumps({'schema_version':1,'source_commit':args.source_commit,'files':source_manifest},indent=2),encoding='utf-8')
    (OUT/'suite-manifest.json').write_text(json.dumps({'version':version,'source_commit':args.source_commit,
        'source_files_sha256':hashlib.sha256((OUT/'source-files.json').read_bytes()).hexdigest(),
        'specialization':'Web3: Hyperliquid, Solana, Helius, Pump.fun, DevFridge Game Builder',
        'bundled_skills':[{'id':p['id'],'version':p['version'],'files':len(p['files']),'source_commit':p['source_commit']} for p in bundled['packages']],
        'components':lock['components'],'apk_sha256':hashlib.sha256((OUT/'mobile/freellmapi-remote.apk').read_bytes()).hexdigest()},indent=2),encoding='utf-8')
    print('Payload assembled:',OUT,flush=True)

if __name__=='__main__':main()
