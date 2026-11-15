"""Copy every Git blob of the pinned skill, including vendor/hidden files."""
import hashlib,json,re,subprocess,sys
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]

def git(repo,*args):return subprocess.check_output(['git','-C',str(repo),*args])

def stage(checkout=None,payload=None):
    lock=json.loads((HERE/'bundled-skills.lock.json').read_text());repo=Path(checkout or ROOT/'dist/work/devfridge-bundle')
    payload=Path(payload or ROOT/'dist/payload').resolve();payload.mkdir(parents=True,exist_ok=True)
    if not repo.exists():
        subprocess.run(['git','clone','--filter=blob:none','--no-checkout',lock['repository'],str(repo)],check=True)
        git(repo,'sparse-checkout','init','--cone');git(repo,'sparse-checkout','set',lock['path'])
        git(repo,'checkout','--detach',lock['commit'])
    tree=git(repo,'rev-parse',lock['commit']+':'+lock['path']).decode().strip()
    if tree!=lock['tree_sha']:raise ValueError('Pinned skill tree mismatch')
    entries=[]
    for row in git(repo,'ls-tree','-rz','--long',lock['commit'],'--',lock['path']).split(b'\0'):
        if not row:continue
        meta,path=row.split(b'\t',1);mode,kind,sha,size=meta.decode().split()
        if kind!='blob' or mode not in ('100644','100755'):raise ValueError('Unsupported skill link/submodule')
        entries.append((path.decode().removeprefix(lock['path']+'/'),sha,int(size)))
    if len(entries)!=lock['expected_files']:raise ValueError('Incomplete skill tree')
    package_path='skills/dgb-'+tree[:8];folder=payload/package_path
    folder.mkdir(parents=True,exist_ok=True);files={}
    process=subprocess.Popen(['git','-C',str(repo),'cat-file','--batch'],stdin=subprocess.PIPE,stdout=subprocess.PIPE)
    try:
        for name,sha,size in entries:
            path=(folder/name).resolve()
            if not path.is_relative_to(folder.resolve()):raise ValueError('Invalid skill path')
            process.stdin.write((sha+'\n').encode());process.stdin.flush()
            header=process.stdout.readline().decode().split()
            if len(header)!=3 or header[1]!='blob' or int(header[2])!=size:raise ValueError('Invalid Git blob header')
            raw=process.stdout.read(size)
            if process.stdout.read(1)!=b'\n' or hashlib.sha1(b'blob '+str(size).encode()+b'\0'+raw).hexdigest()!=sha:raise ValueError('Git blob hash mismatch')
            path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
            files[name]={'size':size,'sha256':hashlib.sha256(raw).hexdigest(),'git_blob':sha}
    finally:
        process.stdin.close();process.wait(timeout=20)
    if process.returncode:raise ValueError('Git blob export failed')
    body=(folder/'SKILL.md').read_text(encoding='utf-8')
    description=re.search(r'^description:\s*(.+)',body,re.M).group(1)
    version=re.search(r'^\s+version:\s*"?([^"\n]+)',body,re.M).group(1).strip()
    if version!=lock['version']:raise ValueError('Pinned skill version mismatch')
    license_data=git(repo,'show',lock['commit']+':LICENSE');notice=payload/'licenses/DevFridge-BUSL-1.1.txt'
    notice.parent.mkdir(parents=True,exist_ok=True);notice.write_bytes(license_data)
    manifest={'schema_version':1,'packages':[{'id':'devfridge-game-builder','description':description,'version':version,
              'path':package_path,'source_repository':lock['repository'],'source_commit':lock['commit'],'tree_sha':tree,
              'files':files,'notices':[{'path':notice.relative_to(payload).as_posix(),'sha256':hashlib.sha256(license_data).hexdigest()}]}]}
    (payload/'bundled-skills.json').write_text(json.dumps(manifest,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    sys.path.insert(0,str(ROOT));from bundled_skills import validate
    validate(payload)
    print(json.dumps({'version':version,'files':len(files),'bytes':sum(f['size'] for f in files.values()),'source_commit':lock['commit'],'tree_sha':tree}))
    return manifest

if __name__=='__main__':stage()
