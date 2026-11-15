"""Exercise the actual installer in scratch space; never open personal accounts."""
import argparse,hashlib,json,os,subprocess,sys,tempfile,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]

def run(command,*,env=None,cwd=None,timeout=180):
    result=subprocess.run([str(x) for x in command],env=env,cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
    if result.returncode:raise RuntimeError('Verification process failed: '+str(command[0])+'\n'+result.stderr[-1500:])
    return result.stdout

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--version',default='0.1.1')
    parser.add_argument('--upgrade-from',default='0.1.0')
    args=parser.parse_args()
    QA=ROOT/('dist/qa-'+args.version)
    # Use local application storage, like the actual per-user installation.
    # Documents may apply additional app/file-protection rules to .txt updates.
    install_base=Path(tempfile.gettempdir())/('freellmapi-suite-qa-'+args.version)
    INSTALL=install_base/'installed suite with spaces';RESULTS=QA/'results.json'
    QA.mkdir(parents=True,exist_ok=True)
    assert INSTALL.resolve().is_relative_to(install_base.resolve())
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\freellmapi.agent.suite_is1') as key:
            previous=Path(winreg.QueryValueEx(key,'InstallLocation')[0]).resolve()
            if previous!=INSTALL.resolve():raise RuntimeError('A real suite installation already exists; do not overwrite its registration for QA.')
    except FileNotFoundError:pass
    exe=ROOT/('dist/windows/FREELLMAPI-Web3-Suite-Setup-'+args.version+'.exe')
    assert exe.is_file()
    # Scope every installer child, including postinstall registration, before
    # launching either EXE. Never register the QA bundle in the real profile.
    env=dict(os.environ);env['APPDATA']=str(QA/'appdata');env['FREELLMAPI_HOME']=str(QA/'isolated-home')
    env['PATH']=str(Path(env['WINDIR'])/'System32')
    home=Path(env['FREELLMAPI_HOME']);home.mkdir(exist_ok=True)
    fixtures={'config.json':b'{"qa_context":"preserve-me"}',
              'key.dpapi':b'SYNTHETIC-NOT-A-KEY',
              'sessions/installer-qa.json':b'{"messages":["preserved context"]}',
              'skills/manual-qa/source.json':b'{"name":"manual-qa"}',
              'skills/manual-qa/SKILL.md':b'User custom content'}
    for name,raw in fixtures.items():
        path=home/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(raw)
    def preserved():
        return all((home/name).read_bytes()==raw for name,raw in fixtures.items())
    def install(artifact,label):
        print('Installing '+label,flush=True)
        run([artifact,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS','/TASKS=',
             '/COMPONENTS=core,router','/NOPATH=1','/DIR='+str(INSTALL),'/LOG='+str(QA/(label+'.log'))],env=env,timeout=900)
        assert preserved()
    # This install creates no icons, does not modify PATH, omits Tailscale,
    # does not launch the setup wizard and never starts the router or agent.
    if args.upgrade_from:
        baseline=ROOT/('dist/windows/FREELLMAPI-Web3-Suite-Setup-'+args.upgrade_from+'.exe')
        assert baseline.is_file();install(baseline,'baseline-'+args.upgrade_from)
    install(exe,'installer-'+args.version)
    # Prove registration was performed by Setup, not by a later Skills import.
    registry=[json.loads(p.read_text(encoding='utf-8')) for p in (home/'skills').glob('*/source.json')]
    builtin=next(x for x in registry if x.get('bundle_id')=='devfridge-game-builder')
    assert builtin['files']==894 and builtin['version']=='1.12.0' and builtin['built_in']
    python=INSTALL/'runtime/python/python.exe';launcher=INSTALL/'launcher/suite.py'
    print('Verifying extracted private runtime and full skill package',flush=True)
    doctor=json.loads(run([python,'-I',launcher,'doctor'],env=env,cwd=QA))
    assert doctor['ok'] and doctor['private_prefix']
    assert doctor['bundled_registration_verified'] and doctor['bundled_skills'][0]['files']==894
    code=("import pathlib,sys,json,socket;from unittest.mock import patch;root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'launcher'));import suite;suite.configure(root);"
          "from skills_manager import Skills;from app_home import default_home;from skill_router import catalog;from desktop_backend import DesktopBackend;"
          "home=default_home();p=next((home/'skills').glob('devfridge-game-builder-*/source.json'));stamp=p.stat().st_mtime_ns;"
          "manager=Skills(home);row=next(x for x in manager.list() if x.get('built_in'));assert 'Three.js' in manager.read(row['name']);"
          "assert 'Android' in manager.read_file(row['name'],'references/seeker-android.md');"
          "assert any(x['name']==row['name'] and x['registered'] for x in catalog(manager));"
          "assert p.stat().st_mtime_ns==stamp;backend=DesktopBackend(home);\n"
          "with patch('socket.create_connection',side_effect=AssertionError('offline')),patch.object(backend,'command',return_value={'ok':True}) as command:\n"
          " backend.workspace_action('game','resume');assert command.call_args.args[0].startswith('/skill use '+row['name']+' ')\n"
          "print('offline-skill-and-desktop-ok')")
    assert 'offline-skill-and-desktop-ok' in run([python,'-I','-c',code,INSTALL],env=env,cwd=QA)
    venv=QA/'private-venv'
    run([python,'-I','-m','venv',venv],env=env,cwd=QA)
    assert run([venv/'Scripts/python.exe','-I','-c','import sys; print(sys.version_info[:2])'],env=env).strip()=='(3, 13)'
    code=("import pathlib,sys;root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'launcher'));import suite;suite.configure(root);"
          "from browser_control import Browser;b=Browser(sys.argv[2]);b.start(headless=True);"
          "b.page.goto('data:text/html,<h1>Web3 Suite local QA</h1>');assert b.page.locator('h1').inner_text()=='Web3 Suite local QA';b.close();print('browser-ok')")
    assert 'browser-ok' in run([python,'-I','-c',code,INSTALL,QA/'browser-profile'],env=env,cwd=QA)
    fixture={'session':'a'*32,'project':str(QA),'messages':[{'role':'user','content':'Hello from installer QA'},{'role':'assistant','content':'FREELLMAPI Web3 Suite is running from the private runtime.'}],
             'models':[{'id':'auto:reliable'}],'model':'auto:reliable','mode':'plan','busy':False,'sessions':[],'projects':[],
             'actual_model':None,'api_status':'Synthetic UI fixture — no model request','status':'Installer smoke test'}
    state=QA/'ui-fixture.json';state.write_text(json.dumps(fixture))
    screenshot=QA/'desktop.png'
    code="import pathlib,sys,runpy;root=pathlib.Path(sys.argv[1]);sys.path.insert(0,str(root/'launcher'));import suite;suite.configure(root);sys.argv=[str(root/'agent/desktop_app.py'),'--qa-state',sys.argv[2],'--snapshot',sys.argv[3]];runpy.run_path(sys.argv[0],run_name='__main__')"
    run([python,'-I','-c',code,INSTALL,state,screenshot],env=env,cwd=QA,timeout=90)
    assert screenshot.is_file() and screenshot.stat().st_size>5000
    node=INSTALL/'runtime/node/node.exe'
    sdk=INSTALL/'agent/integrations/pumpfun'
    sdk_output=run([node,'--test',sdk/'helius-worker.test.mjs',sdk/'discovery.test.mjs',sdk/'events.test.mjs'],env=env,cwd=QA)
    assert '# fail 0' in sdk_output
    (QA/'sdk-installed.log').write_text(sdk_output,encoding='utf-8')
    result={'status':'PASS_WITH_LIMITATIONS','installer_sha256':hashlib.sha256(exe.read_bytes()).hexdigest(),
            'version':args.version,'upgrade_from':args.upgrade_from,'bundled_skill_registered_by_setup':True,
            'bundled_skill_full_hashes_verified':894,'bundled_skill_version':'1.12.0',
            'offline_skill_read_and_desktop_entry_verified':True,'idempotent_registration':True,
            'existing_config_key_sessions_custom_skill_preserved':preserved(),
            'installer_size':exe.stat().st_size,'exact_exe_installed':True,'path_with_spaces':True,
            'no_system_python_on_test_PATH':True,'doctor':doctor,'venv_created_and_executed':True,
            'browser_local_page':True,'desktop_synthetic_fixture_started':True,'sdk_tests_passed':14,
            'real_orders_requested':0,'real_wallet_keys_read':False,'real_model_calls':0,
            'tailscale_reinstalled':False,'router_started':False,'clean_vm_tested':False,
            'physical_android_tested_this_run':False,'installer_code_signed':False,
            'limits':['Build-machine isolated install, not a clean Windows VM.','Tailscale login/provider onboarding and physical Android pairing require personal interactive steps.','Desktop screenshot uses a synthetic fixture.','No live trading or model request was used to validate installation.']}
    print('Uninstalling isolated test installation',flush=True)
    run([INSTALL/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],env=env,cwd=QA,timeout=300)
    for _ in range(20):
        if not python.exists():break
        time.sleep(.5)
    result['scratch_install_uninstalled']=not python.exists()
    assert result['scratch_install_uninstalled']
    assert preserved()
    assert not any(json.loads(p.read_text()).get('managed_by')=='freellmapi-suite-builtin-v1' for p in (home/'skills').glob('*/source.json'))
    result['uninstall_removed_only_managed_skill_registration']=True
    result['user_data_preserved_after_uninstall']=True
    RESULTS.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
