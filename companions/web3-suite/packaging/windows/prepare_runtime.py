"""Stage official CPython NuGet tools, including venv, and wheel dependencies."""
import argparse,json,shutil,subprocess,sys,zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]

def prune_nonruntime_fixtures(runtime):
    """The wheel's optional public self-tests contain example private keys."""
    runtime=Path(runtime).resolve(strict=True)
    target=(runtime/'Lib/site-packages/Crypto/SelfTest').resolve()
    if not target.is_relative_to(runtime) or target==runtime:raise ValueError('Invalid fixture removal target')
    if target.exists():shutil.rmtree(target)
    return ['Crypto/SelfTest: optional public cryptographic fixtures, not runtime code']

def stage_tcltk(runtime,archive,expected_sha256):
    """Extract the matching official Windows Tcl/Tk subset, never host Python."""
    import hashlib
    runtime=Path(runtime).resolve(strict=True);archive=Path(archive)
    if hashlib.sha256(archive.read_bytes()).hexdigest()!=expected_sha256:raise ValueError('Tcl/Tk archive checksum mismatch')
    count=0
    with zipfile.ZipFile(archive) as bundle:
        for item in bundle.infolist():
            name=item.filename.replace('\\','/')
            if item.is_dir() or '__pycache__' in Path(name).parts:continue
            if not (name.startswith(('Lib/tkinter/','tcl/')) or name in ('DLLs/_tkinter.pyd','DLLs/tcl86t.dll','DLLs/tk86t.dll','DLLs/zlib1.dll')):continue
            target=(runtime/name).resolve()
            if not target.is_relative_to(runtime):raise ValueError('Tcl/Tk archive path escapes runtime')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(bundle.read(item));count+=1
    if not (runtime/'Lib/tkinter/__init__.py').is_file() or not (runtime/'DLLs/_tkinter.pyd').is_file():raise ValueError('Official Tcl/Tk subset incomplete')
    return count

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--payload',type=Path,default=ROOT/'dist/payload')
    parser.add_argument('--cache',type=Path,default=ROOT/'dist/downloads')
    args=parser.parse_args()
    lock=json.loads((HERE/'components.lock.json').read_text())
    runtime=args.payload.resolve()/'runtime/python';runtime.mkdir(parents=True,exist_ok=True)
    source=args.cache.resolve()/lock['components']['python']['file']
    import hashlib
    if hashlib.sha256(source.read_bytes()).hexdigest()!=lock['components']['python']['sha256']:raise ValueError('Python checksum mismatch')
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            if not name.startswith('tools/') or name.endswith('/'):continue
            target=(runtime/name.removeprefix('tools/')).resolve()
            if not target.is_relative_to(runtime.resolve()):raise ValueError('Invalid runtime archive path')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(name))
    tcltk=lock['components']['tcltk']
    print(json.dumps({'official_tcltk_files':stage_tcltk(runtime,args.cache.resolve()/tcltk['file'],tcltk['sha256'])}))
    requirements=HERE/'requirements-suite.lock'
    subprocess.run([sys.executable,'-m','pip','install','--target',str(runtime/'Lib/site-packages'),
                    '--only-binary=:all:','--no-compile','--platform','win_amd64','--python-version','3.13',
                    '--implementation','cp','--abi','cp313','-r',str(requirements)],check=True)
    print(json.dumps({'nonruntime_dependency_fixtures_omitted':prune_nonruntime_fixtures(runtime)}))

if __name__=='__main__':main()
