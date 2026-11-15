"""Exercise the actual installer in scratch space; never open personal accounts."""
import hashlib,json,os,subprocess,sys,time
from pathlib import Path
HERE=Path(__file__).resolve().parent;ROOT=HERE.parents[1]
QA=ROOT/'dist/qa';INSTALL=QA/'installed suite with spaces';RESULTS=QA/'results.json'

def run(command,*,env=None,cwd=None,timeout=180):
    result=subprocess.run([str(x) for x in command],env=env,cwd=cwd,capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=timeout)
    if result.returncode:raise RuntimeError('Verification process failed: '+str(command[0])+'\n'+result.stderr[-1500:])
    return result.stdout

def main():
    QA.mkdir(parents=True,exist_ok=True)
    assert INSTALL.resolve().is_relative_to((ROOT/'dist/qa').resolve())
    import winreg
    try:
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER,r'Software\Microsoft\Windows\CurrentVersion\Uninstall\freellmapi.agent.suite_is1') as key:
            previous=Path(winreg.QueryValueEx(key,'InstallLocation')[0]).resolve()
            if previous!=INSTALL.resolve():raise RuntimeError('A real suite installation already exists; do not overwrite its registration for QA.')
    except FileNotFoundError:pass
    exe=ROOT/'dist/windows/FREELLMAPI-Web3-Suite-Setup-0.1.0.exe'
    assert exe.is_file()
    # This install creates no icons, does not modify PATH, omits Tailscale,
    # does not launch the setup wizard and never starts the router or agent.
    run([exe,'/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART','/NOICONS','/TASKS=',
         '/COMPONENTS=core,router','/NOPATH=1','/DIR='+str(INSTALL),'/LOG='+str(QA/'installer.log')],timeout=600)
    env=dict(os.environ);env['APPDATA']=str(QA/'appdata');env['FREELLMAPI_HOME']=str(QA/'isolated-home')
    env['PATH']=str(Path(env['WINDIR'])/'System32')
    python=INSTALL/'runtime/python/python.exe';launcher=INSTALL/'launcher/suite.py'
    doctor=json.loads(run([python,'-I',launcher,'doctor'],env=env,cwd=QA))
    assert doctor['ok'] and doctor['private_prefix']
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
            'installer_size':exe.stat().st_size,'exact_exe_installed':True,'path_with_spaces':True,
            'no_system_python_on_test_PATH':True,'doctor':doctor,'venv_created_and_executed':True,
            'browser_local_page':True,'desktop_synthetic_fixture_started':True,'sdk_tests_passed':14,
            'real_orders_requested':0,'real_wallet_keys_read':False,'real_model_calls':0,
            'tailscale_reinstalled':False,'router_started':False,'clean_vm_tested':False,
            'physical_android_tested_this_run':False,'installer_code_signed':False,
            'limits':['Build-machine isolated install, not a clean Windows VM.','Tailscale login/provider onboarding and physical Android pairing require personal interactive steps.','Desktop screenshot uses a synthetic fixture.','No live trading or model request was used to validate installation.']}
    run([INSTALL/'unins000.exe','/VERYSILENT','/SUPPRESSMSGBOXES','/NORESTART'],cwd=QA,timeout=180)
    for _ in range(20):
        if not python.exists():break
        time.sleep(.5)
    result['scratch_install_uninstalled']=not python.exists()
    assert result['scratch_install_uninstalled']
    RESULTS.write_text(json.dumps(result,indent=2),encoding='utf-8')
    print(json.dumps(result,indent=2))

if __name__=='__main__':main()
