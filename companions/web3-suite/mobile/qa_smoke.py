"""Real Android APK -> HTTP USB tunnel -> real CLI/tools. Scripted LLM, isolated HOME."""
import argparse,hashlib,json,os,re,subprocess,sys,threading,time,uuid,xml.etree.ElementTree as ET
from pathlib import Path
from unittest.mock import patch
ROOT=Path(__file__).resolve().parent.parent
sys.path.insert(0,str(ROOT))
ADB=Path(os.environ.get('LOCALAPPDATA',''))/'Android/Sdk/platform-tools/adb.exe'
PACKAGE='it.freellmapi.remote'

def host(folder,qa_port=31416):
    import freellmapi as f,remote_access as r,_thread
    from reliable_runtime import atomic_bytes
    run_id=uuid.uuid4().hex
    data=folder/'isolated-home'/run_id;data.mkdir(parents=True,exist_ok=True);project=folder/'project'/run_id;project.mkdir(parents=True,exist_ok=True)
    (data/'config.json').write_text(json.dumps({'base_url':'http://127.0.0.1:9','model':'auto','streaming':False,'auto_skills':False}),encoding='utf-8')
    agent_ref=[];requests=[];tool_calls=[];finished=threading.Event()
    init=f.Agent.__init__;tool=f.Agent.tool;show=r.show_pair;start=r.Remote.start
    def start_test(remote,bind='127.0.0.1',port=31416,private_vpn=False):return start(remote,bind,qa_port,private_vpn)
    def initialize(a,*args,**kwargs):init(a,*args,**kwargs);agent_ref.append(a)
    def tracked(a,name,args):tool_calls.append(name);return tool(a,name,args)
    def pair(remote,url=None,open_browser=False):
        result=show(remote,url,open_browser=False);atomic_bytes(folder/'pair.json',json.dumps(result).encode());return result
    def reply(a,path,payload=None):
        if path=='models':return {'data':[{'id':'auto'}]+[{'id':f'qa-model-{i:03d}'} for i in range(400)]}
        assert path=='chat/completions';requests.append(payload)
        messages=payload['messages'];index=max(i for i,m in enumerate(messages) if m.get('role')=='user');current=messages[index:]
        names={c['id']:c['function']['name'] for m in current for c in m.get('tool_calls',[])}
        done=[names.get(m.get('tool_call_id')) for m in current if m.get('role')=='tool']
        def call(name,args):return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':uuid.uuid4().hex,'type':'function','function':{'name':name,'arguments':json.dumps(args)}}]},'finish_reason':'tool_calls'}],'model':'qa-scripted-model'}
        if 'write_file' not in done:return call('write_file',{'path':'remote-proof.txt','content':'REMOTE-CONTEXT-PERSISTED\n'})
        if 'read_file' not in done:return call('read_file',{'path':'remote-proof.txt'})
        return {'choices':[{'message':{'role':'assistant','content':'REMOTE-SMOKE-OK: file scritto e riletto sul PC.'},'finish_reason':'stop'}],'model':'qa-scripted-model'}
    def monitor():
        while not finished.wait(.2):
            if agent_ref:
                a=agent_ref[0];remote=r.active();proof=project/'remote-proof.txt'
                state={'session':a.session.stem,'model':a.model,'messages':len(a.messages),'context_preserved':any('REMOTE-SMOKE-WRITE' in str(m.get('content','')) for m in a.messages),'file_exists':proof.exists(),'file_content':proof.read_text(encoding='utf-8') if proof.exists() else None,'write_calls':tool_calls.count('write_file'),'read_calls':tool_calls.count('read_file'),'model_calls':len(requests),'paired_devices':len(remote.devices) if remote else 0,'pair_attempts':remote.pair_attempts if remote else 0,'busy':remote.busy if remote else False,'sessions':len(a.sessions.list()) if getattr(a,'sessions',None) else 0}
                atomic_bytes(folder/'host-state.json',json.dumps(state).encode())
                if (folder/'revoke.request').exists() and remote:remote.revoke();(folder/'revoke.request').unlink()
                if (folder/'stop.request').exists():_thread.interrupt_main();return
    worker=threading.Thread(target=monitor,daemon=True);worker.start()
    try:
        with patch.object(f,'HOME',data),patch.object(f.Agent,'__init__',initialize),patch.object(f.Agent,'tool',tracked),patch.object(f.Agent,'api',reply),patch.object(f.Seeker,'start'),patch.object(r,'show_pair',pair),patch.object(r.Remote,'start',start_test),patch.dict(os.environ,{'FREELLMAPI_SESSION_TOKEN':'synthetic-not-a-real-api-token'}),patch.object(sys,'argv',['freellmapi','--remote-only','--remote-bind','usb','--new','--model','auto']):
            os.chdir(project);f.main()
    finally:finished.set();worker.join(2)

def smoke(folder,qa_port=31416):
    folder.mkdir(parents=True,exist_ok=True)
    for name in ('pair.json','host-state.json','stop.request','revoke.request'):(folder/name).unlink(missing_ok=True)
    serial='emulator-5554';commands=[];reverse_created=False
    def adb(*args,raw=False):
        result=subprocess.run([str(ADB),'-s',serial,*args],capture_output=True,timeout=25)
        commands.append({'args':list(args),'exit_code':result.returncode})
        if result.returncode:raise RuntimeError(result.stderr.decode(errors='replace')+result.stdout.decode(errors='replace'))
        return result.stdout if raw else result.stdout.decode(errors='replace')
    def wait(check,timeout=25):
        end=time.monotonic()+timeout
        while time.monotonic()<end:
            if process.poll() is not None:raise ChildProcessError('Isolated QA host exited before the expected UI state; see cli.log')
            try:
                result=check()
                if result is not None and result is not False:return result
            except (OSError,ValueError,ET.ParseError,RuntimeError):pass
            time.sleep(.35)
        raise TimeoutError('QA condition timed out')
    def state():return json.loads((folder/'host-state.json').read_text(encoding='utf-8'))
    def nodes():
        adb('shell','uiautomator','dump','/sdcard/freellmapi-remote-ui.xml')
        return list(ET.fromstring(adb('shell','cat','/sdcard/freellmapi-remote-ui.xml')).iter('node'))
    def find(label,starts=False,class_name=None,contains=False):
        return next((n for n in nodes() if (class_name is None or n.get('class')==class_name) and (label in n.get('text','') if contains else n.get('text','').startswith(label) if starts else n.get('text')==label)),None)
    def tap(node):
        if node is None:raise AssertionError('UI target missing')
        x1,y1,x2,y2=map(int,re.findall(r'\d+',node.get('bounds')));adb('shell','input','tap',str((x1+x2)//2),str((y1+y2)//2))
    def screenshot(name): (folder/name).write_bytes(adb('exec-out','screencap','-p',raw=True))
    log=(folder/'cli.log').open('w',encoding='utf-8')
    process=subprocess.Popen([sys.executable,'-X','utf8',str(Path(__file__).resolve()),'--host','--output',str(folder),'--port',str(qa_port)],cwd=ROOT,stdout=log,stderr=subprocess.STDOUT)
    observations={}
    try:
        pair=wait(lambda:json.loads((folder/'pair.json').read_text(encoding='utf-8')))
        adb('install','-r',str(ROOT/'mobile/build/freellmapi-remote.apk'))
        assert adb('shell','getprop','ro.kernel.qemu').strip()=='1','QA must never clear a real phone'
        adb('shell','pm','clear',PACKAGE);adb('reverse','tcp:'+str(qa_port),'tcp:'+str(qa_port));reverse_created=True
        adb('shell','am','start','-a','android.intent.action.VIEW','-d',"'"+pair['link']+"'",'-n',PACKAGE+'/.MainActivity')
        tap(wait(lambda:find('Collega al PC')))
        wait(lambda:state()['paired_devices']==1);wait(lambda:find('Invia'));observations['paired_native_app']=True
        initial=state()['session'];initial_sessions=state()['sessions'];observations['initial_session']=initial
        edit=next(n for n in nodes() if n.get('class')=='android.widget.EditText' and 'Scrivi a freellmapi' in n.get('text',''))
        tap(edit);adb('shell','input','text','REMOTE-SMOKE-WRITE');adb('shell','input','keyevent','4');tap(wait(lambda:find('Invia')))
        tap(wait(lambda:find('Consenti'),timeout=35));wait(lambda:state()['file_exists'] and state()['read_calls']==1 and not state()['busy'],timeout=35)
        observations['real_file_verified']=state()['file_content']=='REMOTE-CONTEXT-PERSISTED\n';observations['write_calls']=state()['write_calls']
        screenshot('connected-app.png')
        tap(wait(lambda:find('auto')));tap(wait(lambda:find('Cerca tra i modelli')));adb('shell','input','text','qa-model-002');tap(wait(lambda:find('qa-model-002',class_name='android.widget.TextView')))
        wait(lambda:state()['model']=='qa-model-002');observations['model_selected_on_pc']=True
        tap(wait(lambda:find('Sessioni')));tap(wait(lambda:find('NUOVA SESSIONE')));tap(wait(lambda:find('NUOVA')));wait(lambda:state()['sessions']==initial_sessions+1 and state()['session']!=initial)
        tap(wait(lambda:find('Sessioni')));tap(wait(lambda:find('REMOTE-SMOKE-WRITE',starts=True)));wait(lambda:state()['session']==initial and state()['context_preserved'])
        observations['session_resume_context_preserved']=True
        adb('shell','am','force-stop',PACKAGE);adb('shell','am','start','-n',PACKAGE+'/.MainActivity');wait(lambda:find('Invia'))
        wait(lambda:find('REMOTE-SMOKE-OK',contains=True));observations['reconnection_without_duplicate_write']=state()['write_calls']==1
        screenshot('resumed-app.png')
        # Quick actions only compose requests; they never invoke a model or signer.
        before_calls=state()['model_calls']
        tap(wait(lambda:find('▸ Conto reale',starts=True)));tap(wait(lambda:find('Analizza')))
        tap(wait(lambda:find('Mercato:',starts=True,class_name='android.widget.EditText')));adb('shell','input','text','BTC');adb('shell','input','keyevent','4');tap(wait(lambda:find('PREPARA RICHIESTA')))
        wait(lambda:find('Analizza BTC su Hyperliquid',starts=True,class_name='android.widget.EditText'))
        observations['analysis_quick_action_only_composes']=state()['model_calls']==before_calls and state()['write_calls']==1
        tap(wait(lambda:find('Prepara ingresso')))
        tap(wait(lambda:find('Mercato:',starts=True,class_name='android.widget.EditText')));adb('shell','input','text','PUMP');adb('shell','input','keyevent','4');tap(wait(lambda:find('PREPARA RICHIESTA')))
        draft=wait(lambda:find('Prepara un piano di ingresso condizionato per PUMP',starts=True,class_name='android.widget.EditText')).get('text','')
        observations['conditional_quick_action_has_no_arm_or_order']=('non armare il monitor e non inviare ordini' in draft and state()['model_calls']==before_calls)
        tap(wait(lambda:find('▾ Conto reale',starts=True)))
        adb('shell','input','keyevent','KEYCODE_HOME');time.sleep(.5);adb('shell','am','force-stop',PACKAGE);adb('shell','am','start','-n',PACKAGE+'/.MainActivity')
        wait(lambda:find('Prepara un piano di ingresso condizionato per PUMP',starts=True,class_name='android.widget.EditText'))
        observations['draft_survives_process_stop_without_submit']=state()['model_calls']==before_calls and state()['write_calls']==1
        # A consumed link to the same PC must reuse the saved credential, not pair again.
        before_attempts=state()['pair_attempts']
        adb('shell','am','start','-a','android.intent.action.VIEW','-d',"'"+pair['link']+"'",'-n',PACKAGE+'/.MainActivity')
        wait(lambda:find('Invia'));observations['old_link_reuses_saved_pair']=state()['paired_devices']==1 and state()['pair_attempts']==before_attempts
        # Force a failed fresh-pair attempt while a valid saved connection exists.
        from urllib.parse import urlencode
        failed='freellmapi://pair?'+urlencode({'url':'http://127.0.0.1:'+str(qa_port),'fp':'0'*64,'code':'00000000'})
        adb('shell','am','start','-a','android.intent.action.VIEW','-d',"'"+failed+"'",'-n',PACKAGE+'/.MainActivity')
        tap(wait(lambda:find('Collega al PC')));wait(lambda:find('Codice già usato o scaduto.',starts=True))
        adb('shell','am','start','-a','android.intent.action.VIEW','-d',"'"+pair['link']+"'",'-n',PACKAGE+'/.MainActivity');wait(lambda:find('Invia'))
        observations['failed_pair_preserves_connection']=state()['paired_devices']==1 and state()['write_calls']==1
        (folder/'revoke.request').write_text('revoke',encoding='ascii');wait(lambda:find('Collega al PC'));observations['revocation_disconnects_app']=True
        crashes=adb('logcat','-d','-b','crash');observations['no_app_crash_in_buffer']=('Process: '+PACKAGE) not in crashes
        observations['passed']=all(observations[k] for k in ['paired_native_app','real_file_verified','model_selected_on_pc','session_resume_context_preserved','reconnection_without_duplicate_write','old_link_reuses_saved_pair','failed_pair_preserves_connection','revocation_disconnects_app','no_app_crash_in_buffer','analysis_quick_action_only_composes','conditional_quick_action_has_no_arm_or_order','draft_survives_process_stop_without_submit'])
        return observations
    finally:
        (folder/'stop.request').write_text('stop',encoding='ascii')
        try:process.wait(timeout=12)
        except subprocess.TimeoutExpired:process.terminate();process.wait(timeout=8)
        log.close()
        if reverse_created:
            try:adb('reverse','--remove','tcp:'+str(qa_port))
            except RuntimeError:observations['cleanup_reverse_failed']=True
        evidence={'scope':'Real APK/emulator, USB tunnel, actual CLI and filesystem; scripted model responses, no live model API, no financial actions','observations':observations,'cli_exit_code':process.returncode,'commands':commands,'apk_sha256':hashlib.sha256((ROOT/'mobile/build/freellmapi-remote.apk').read_bytes()).hexdigest()}
        (folder/'results.json').write_text(json.dumps(evidence,indent=2,ensure_ascii=False),encoding='utf-8')
        print(json.dumps({'output':str(folder),'observations':observations,'cli_exit_code':process.returncode}))

if __name__=='__main__':
    parser=argparse.ArgumentParser();parser.add_argument('--host',action='store_true');parser.add_argument('--output',type=Path,required=True);parser.add_argument('--port',type=int,default=31416);args=parser.parse_args()
    if args.host:host(args.output.resolve(),args.port)
    elif not smoke(args.output.resolve(),args.port).get('passed'):raise SystemExit(1)
