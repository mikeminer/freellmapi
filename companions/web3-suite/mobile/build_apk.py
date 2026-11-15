"""Build the dependency-free native Android client with installed official SDK tools."""
import hashlib,json,os,shutil,subprocess,sys,zipfile
from pathlib import Path
ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
from command_environment import environment

def build():
    env=environment();sdk=Path(env['ANDROID_HOME']);java=Path(env['JAVA_HOME'])/'bin';tools=sdk/'build-tools'/'35.0.0';platform=sdk/'platforms'/'android-35'/'android.jar'
    out=ROOT/'build';out.mkdir(exist_ok=True);classes=out/'classes';classes.mkdir(exist_ok=True);dex=out/'dex';dex.mkdir(exist_ok=True)
    log=[]
    def run(args):
        p=subprocess.run([str(x) for x in args],cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
        log.append({'command':[str(x) for x in args],'exit_code':p.returncode,'stdout':p.stdout,'stderr':p.stderr})
        (out/'build.log').write_text('\n'.join(json.dumps(x,ensure_ascii=False) for x in log),encoding='utf-8')
        if p.returncode:raise RuntimeError(p.stderr or p.stdout)
        return p.stdout
    for entry in [tools/'aapt2.exe',tools/'d8.bat',tools/'zipalign.exe',tools/'apksigner.bat',platform,java/'javac.exe']:
        if not entry.is_file():raise RuntimeError('Strumento Android mancante: '+str(entry))
    compiled=out/'resources.zip'
    run([tools/'aapt2.exe','compile','--dir',ROOT/'res','-o',compiled])
    unsigned=out/'unsigned.apk';aligned=out/'aligned.apk';apk=out/'freellmapi-remote.apk'
    run([tools/'aapt2.exe','link','-o',unsigned,'--manifest',ROOT/'AndroidManifest.xml','-I',platform,'-A',ROOT/'assets',compiled])
    sources=list((ROOT/'src').rglob('*.java'))
    run([java/'javac.exe','-encoding','UTF-8','-source','8','-target','8','-classpath',platform,'-d',classes,*sources])
    jar=out/'classes.jar'
    with zipfile.ZipFile(jar,'w',zipfile.ZIP_DEFLATED) as z:
        for item in classes.rglob('*.class'):z.write(item,item.relative_to(classes).as_posix())
    run([tools/'d8.bat','--min-api','26','--lib',platform,'--output',dex,jar])
    with zipfile.ZipFile(unsigned,'a',zipfile.ZIP_DEFLATED) as z:
        for item in dex.glob('*.dex'):z.write(item,item.name)
    run([tools/'zipalign.exe','-f','4',unsigned,aligned])
    store=Path(os.environ.get('APPDATA',str(Path.home())))/'freellmapi-cli'/'android'/'debug.keystore';store.parent.mkdir(parents=True,exist_ok=True)
    if not store.exists():run([java/'keytool.exe','-genkeypair','-keystore',store,'-storepass','android','-alias','androiddebugkey','-keypass','android','-dname','CN=freellmapi Remote Debug','-keyalg','RSA','-keysize','2048','-validity','10000'])
    run([tools/'apksigner.bat','sign','--ks',store,'--ks-key-alias','androiddebugkey','--ks-pass','pass:android','--key-pass','pass:android','--out',apk,aligned])
    verified=run([tools/'apksigner.bat','verify','--verbose','--print-certs',apk]);badging=run([tools/'aapt.exe','dump','badging',apk])
    result={'apk':str(apk),'sha256':hashlib.sha256(apk.read_bytes()).hexdigest(),'bytes':apk.stat().st_size,'signature_verified':True,'package':'it.freellmapi.remote','min_sdk':26,'target_sdk':35,'variant':'debug_signed','badging':badging,'signature':verified}
    (out/'build-result.json').write_text(json.dumps(result,indent=2),encoding='utf-8');print(json.dumps({k:v for k,v in result.items() if k not in ('badging','signature')}));return result

if __name__=='__main__':build()
