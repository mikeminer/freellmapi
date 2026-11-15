"""Stage official CPython NuGet tools, including venv, and wheel dependencies."""
import json,subprocess,sys,zipfile
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]

def main():
    lock=json.loads((HERE/'components.lock.json').read_text())
    runtime=ROOT/'dist/payload/runtime/python';runtime.mkdir(parents=True,exist_ok=True)
    source=ROOT/'dist/downloads'/lock['components']['python']['file']
    import hashlib
    if hashlib.sha256(source.read_bytes()).hexdigest()!=lock['components']['python']['sha256']:raise ValueError('Python checksum mismatch')
    with zipfile.ZipFile(source) as archive:
        for name in archive.namelist():
            if not name.startswith('tools/') or name.endswith('/'):continue
            target=(runtime/name.removeprefix('tools/')).resolve()
            if not target.is_relative_to(runtime.resolve()):raise ValueError('Invalid runtime archive path')
            target.parent.mkdir(parents=True,exist_ok=True);target.write_bytes(archive.read(name))
    requirements=HERE/'requirements-suite.lock'
    subprocess.run([sys.executable,'-m','pip','install','--target',str(runtime/'Lib/site-packages'),
                    '--only-binary=:all:','--platform','win_amd64','--python-version','3.13',
                    '--implementation','cp','--abi','cp313','-r',str(requirements)],check=True)

if __name__=='__main__':main()
