"""Run the separate instrumentation APK on an emulator, saving native evidence."""
import argparse
import hashlib
import json
import os
import subprocess
from pathlib import Path

ROOT=Path(__file__).resolve().parent
ADB=Path(os.environ['LOCALAPPDATA'])/'Android/Sdk/platform-tools/adb.exe'


def run(serial,output):
    if not serial.startswith('emulator-'):
        raise ValueError('UI QA clears app data and is restricted to an emulator.')
    output.mkdir(parents=True,exist_ok=True)
    def adb(*args,timeout=35):
        result=subprocess.run([str(ADB),'-s',serial,*map(str,args)],capture_output=True,
                              text=True,encoding='utf-8',errors='replace',timeout=timeout)
        if result.returncode:raise RuntimeError(result.stdout+result.stderr)
        return result.stdout
    if adb('shell','getprop','ro.kernel.qemu').strip()!='1':raise ValueError('Target is not an emulator')
    adb('shell','input','keyevent','KEYCODE_WAKEUP')
    adb('shell','wm','dismiss-keyguard')
    adb('shell','settings','put','system','screen_off_timeout','1800000')
    adb('shell','settings','put','secure','show_ime_with_hard_keyboard','1')
    adb('shell','am','force-stop','it.freellmapi.remote')
    adb('install','-r',ROOT/'build/freellmapi-remote.apk')
    adb('install','-r',ROOT/'build/ui-tests/ui-tests.apk')
    adb('shell','pm','clear','it.freellmapi.remote')
    # Bring the test target to the foreground before instrumentation starts.
    # A visible View alone does not imply that its task owns input focus.
    adb('shell','am','start','-W','-n','it.freellmapi.remote/.MainActivity')
    observations={}
    try:
        log=adb('shell','am','instrument','-w','it.freellmapi.remote.tests/it.freellmapi.remote.ChatUiChecks',timeout=90)
        (output/'instrumentation.log').write_text(log,encoding='utf-8')
        prefix='INSTRUMENTATION_RESULT: results='
        row=next((line[len(prefix):] for line in log.splitlines() if line.startswith(prefix)),None)
        if row is None:raise RuntimeError('Instrumentation returned no results')
        observations=json.loads(row)
        return observations.get('passed',False)
    finally:
        evidence={'scope':'Real signed Android APK on emulator; native scroll/rotation/recreation, synthetic chat, no live models or user data',
                  'observations':observations,
                  'apk_sha256':hashlib.sha256((ROOT/'build/freellmapi-remote.apk').read_bytes()).hexdigest()}
        (output/'results.json').write_text(json.dumps(evidence,indent=2),encoding='utf-8')
        print(json.dumps(observations))


if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--serial',default='emulator-5554');parser.add_argument('--output',type=Path,required=True)
    args=parser.parse_args()
    raise SystemExit(0 if run(args.serial,args.output.resolve()) else 1)
