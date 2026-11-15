"""Build a separate, locally signed Android instrumentation APK; no SDK downloads."""
import json
import os
import subprocess
import sys
import zipfile
from pathlib import Path

ROOT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT.parent))
from command_environment import environment


def build():
    env=environment();sdk=Path(env['ANDROID_HOME']);java=Path(env['JAVA_HOME'])/'bin'
    tools=sdk/'build-tools/35.0.0';platform=sdk/'platforms/android-35/android.jar'
    out=ROOT/'build/ui-tests';out.mkdir(parents=True,exist_ok=True)
    classes=out/'classes';classes.mkdir(exist_ok=True)
    dex=out/'dex';dex.mkdir(exist_ok=True)
    def run(args):
        result=subprocess.run(list(map(str,args)),cwd=ROOT,env=env,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=120)
        if result.returncode:raise RuntimeError(result.stderr or result.stdout)
    manifest=out/'AndroidManifest.xml'
    manifest.write_text('''<manifest xmlns:android="http://schemas.android.com/apk/res/android" package="it.freellmapi.remote.tests">
    <uses-sdk android:minSdkVersion="26" android:targetSdkVersion="35" />
    <application android:label="FREELLMAPI QA" />
    <instrumentation android:name="it.freellmapi.remote.ChatUiChecks" android:targetPackage="it.freellmapi.remote" />
    </manifest>''',encoding='utf-8')
    run([tools/'aapt2.exe','link','-o',out/'unsigned.apk','--manifest',manifest,'-I',platform])
    run([java/'javac.exe','-encoding','UTF-8','-source','8','-target','8','-classpath',str(platform)+os.pathsep+str(ROOT/'build/classes.jar'),'-d',classes,*ROOT.glob('testsrc/**/*.java')])
    jar=out/'classes.jar'
    with zipfile.ZipFile(jar,'w',zipfile.ZIP_DEFLATED) as target:
        for path in classes.rglob('*.class'):target.write(path,path.relative_to(classes).as_posix())
    run([tools/'d8.bat','--min-api','26','--lib',platform,'--classpath',ROOT/'build/classes.jar','--output',dex,jar])
    with zipfile.ZipFile(out/'unsigned.apk','a',zipfile.ZIP_DEFLATED) as target:
        for path in dex.glob('*.dex'):target.write(path,path.name)
    run([tools/'zipalign.exe','-f','4',out/'unsigned.apk',out/'aligned.apk'])
    store=Path(os.environ['APPDATA'])/'freellmapi-cli/android/debug.keystore'
    apk=out/'ui-tests.apk'
    run([tools/'apksigner.bat','sign','--ks',store,'--ks-key-alias','androiddebugkey','--ks-pass','pass:android','--key-pass','pass:android','--out',apk,out/'aligned.apk'])
    run([tools/'apksigner.bat','verify',apk])
    print(json.dumps({'test_apk':str(apk),'signature_verified':True}))


if __name__=='__main__':build()
