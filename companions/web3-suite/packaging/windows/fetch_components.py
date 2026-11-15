"""Resolve official Windows distributions once, then verify cached downloads."""
import concurrent.futures,hashlib,json,sys,time,urllib.request,urllib.error
from pathlib import Path

HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
CACHE=ROOT/'dist/downloads';LOCK=HERE/'components.lock.json'

def read(url):
    request=urllib.request.Request(url,headers={'User-Agent':'FREELLMAPI-Companion-Suite-Builder/0.1'})
    with urllib.request.urlopen(request,timeout=120) as response:return response.read()
def data(url):return json.loads(read(url))
def release(repo):return data('https://api.github.com/repos/'+repo+'/releases/latest')
def asset(repo,predicate):
    r=release(repo);a=next(a for a in r['assets'] if predicate(a['name']))
    return {'version':r['tag_name'],'url':a['browser_download_url'],'sha256':(a.get('digest') or '').removeprefix('sha256:')}

def resolve():
    upstream=asset('tashfeenahmed/freellmapi',lambda n:n.endswith('-win.zip'))
    tailscale=data('https://pkgs.tailscale.com/stable/?mode=json');msi=tailscale['MSIs']['amd64']
    node=next(r for r in data('https://nodejs.org/dist/index.json') if r['version'].startswith('v22.') and 'win-x64-zip' in r['files'])
    nodefile='node-'+node['version']+'-win-x64.zip';nodeurl='https://nodejs.org/dist/'+node['version']+'/'
    nodesha=next(line.split()[0] for line in read(nodeurl+'SHASUMS256.txt').decode().splitlines() if line.endswith('  '+nodefile))
    return {'schema_version':1,'components':{
        'python':{'version':'3.13.17','url':'https://www.nuget.org/api/v2/package/python/3.13.17','file':'python.3.13.17.nupkg','sha256':''},
        'router':upstream,
        'tailscale':{'version':tailscale['MSIsVersion'],'url':'https://pkgs.tailscale.com/stable/'+msi,'sha256':read('https://pkgs.tailscale.com/stable/'+msi+'.sha256').decode().split()[0]},
        'inno':asset('jrsoftware/issrc',lambda n:n.startswith('innosetup-') and n.endswith('-x64.exe')),
        'node':{'version':node['version'],'url':nodeurl+nodefile,'sha256':nodesha},
        'powershell':asset('PowerShell/PowerShell',lambda n:n.endswith('win-x64.zip')),
        'git':asset('git-for-windows/git',lambda n:n.startswith('MinGit-') and n.endswith('-64-bit.zip') and 'busybox' not in n),
        'adb':{'version':'platform-tools-latest','url':'https://dl.google.com/android/repository/platform-tools-latest-windows.zip','sha256':''}
        ,'vcredist':{'version':'2015-2022','url':'https://aka.ms/vs/17/release/vc_redist.x64.exe','sha256':''}
    }}

def fetch(name,row):
    path=CACHE/row.get('file',row['url'].rsplit('/',1)[-1])
    if not path.exists():
        temporary=path.with_suffix(path.suffix+'.part')
        for attempt in range(3):
            offset=temporary.stat().st_size if temporary.exists() else 0
            headers={'User-Agent':'FREELLMAPI-Companion-Suite-Builder/0.1'}
            if offset:headers['Range']='bytes='+str(offset)+'-'
            request=urllib.request.Request(row['url'],headers=headers)
            try:
                with urllib.request.urlopen(request,timeout=35) as response,temporary.open('ab' if response.status==206 else 'wb') as out:
                    while block:=response.read(1024*1024):out.write(block)
                break
            except (OSError,urllib.error.URLError):
                if attempt==2:raise
                time.sleep(2*(attempt+1))
        temporary.replace(path)
    actual=hashlib.sha256(path.read_bytes()).hexdigest()
    if row['sha256'] and row['sha256']!=actual:raise ValueError('Checksum mismatch: '+name)
    return name,{**row,'sha256':actual,'file':path.name,'size':path.stat().st_size}

def main():
    CACHE.mkdir(parents=True,exist_ok=True)
    lock=json.loads(LOCK.read_text()) if LOCK.exists() else resolve()
    LOCK.write_text(json.dumps(lock,indent=2)+'\n',encoding='utf-8')
    with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
        futures=[pool.submit(fetch,name,row) for name,row in lock['components'].items()]
        for future in concurrent.futures.as_completed(futures):
            name,row=future.result();lock['components'][name]=row
            LOCK.write_text(json.dumps(lock,indent=2)+'\n',encoding='utf-8')
            print(name,row['version'],row['size'],row['sha256'],flush=True)
    LOCK.write_text(json.dumps(lock,indent=2)+'\n',encoding='utf-8')

if __name__=='__main__':main()
