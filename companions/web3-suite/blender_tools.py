"""Local Blender authoring, explicit runtime identity and bounded sanitized execution."""
import hashlib,json,os,shutil
from pathlib import Path

def status(home):
    config=Path(home)/'blender-runtime.json'
    if config.is_file():
        data=json.loads(config.read_text(encoding='utf-8-sig'))
        compatibility=data.get('compatibility',{})
        executable=Path(compatibility.get('copy') or data['executable'])
        expected=compatibility.get('compat_exe_sha256')
        if not executable.is_file():return {'available':False,'reason':'Configured Blender missing','config':str(config)}
        digest=hashlib.sha256(executable.read_bytes()).hexdigest()
        return {'available':not expected or digest==expected,'executable':str(executable),'sha256':digest,'compatibility_copy':bool(compatibility),'source':data.get('source'),'config':str(config)}
    found=shutil.which('blender')
    return {'available':bool(found),'executable':found,'reason':None if found else 'Blender not configured; no model assets fabricated.'}

def run(agent,script_path,home,timeout=300):
    runtime=status(home)
    if not runtime['available']:raise ValueError('Blender runtime unavailable or hash changed: '+json.dumps(runtime))
    script=agent.path(script_path)
    if not script.is_file() or script.suffix.lower()!='.py':raise ValueError('Use an existing Blender Python script in the project.')
    if not agent.approve('Eseguire Blender locale in background con '+str(script)+' per creare/esportare asset?'):return {'denied':True}
    from android_build import quote
    from shell_runner import run as command
    exe=Path(runtime['executable']);directories=[exe.parent/'blender.crt',exe.parent/'blender.shared',exe.parent]
    prefix=os.pathsep.join(str(p) for p in directories if p.is_dir())
    text=command('$env:PATH = '+quote(prefix)+' + [IO.Path]::PathSeparator + $env:PATH; & '+quote(exe)+' --background --factory-startup --python '+quote(script)+'; exit $LASTEXITCODE',agent.root,home,timeout)
    return {'ok':text.startswith('Exit code: 0'),'runtime':runtime,'script':str(script),'execution':text,'verification':'Exit code alone is not visual validation. Inspect exported GLB, render and actual game performance.'}
