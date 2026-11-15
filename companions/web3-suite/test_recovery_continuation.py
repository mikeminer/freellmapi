import json,os,subprocess,sys,tempfile,textwrap,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import evolution_tools,operating_rules,reliable_runtime
from model_health import RateLimited
from qa_interruption import finalize,forget,recover_reports,remember

class RecoveryContinuationTests(unittest.TestCase):
 def test_real_turn_continues_after_evaluation_cooldown(self):
  from freellmapi import Agent
  with tempfile.TemporaryDirectory() as tmp:
   agent=Agent(Path(tmp),'http://localhost','test','auto');agent.session=Path(tmp)/'session.json';agent.mode='auto'
   def call(ident,name,args): return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':ident,'function':{'name':name,'arguments':json.dumps(args)}}]}}]}
   responses=[call('eval','strategy_evaluate',{'id':'a'*32,'model':'model','cases_json':'[]'}),call('write','write_file',{'path':'independent.txt','content':'done'}),{'choices':[{'message':{'role':'assistant','content':'Independent step saved; evaluation unavailable.'}}]}]
   with patch.object(agent,'api',side_effect=responses),patch.object(operating_rules,'evaluate',side_effect=RateLimited('cooldown')),patch('freellmapi.output'):
    agent.turn('Valuta le strategie e completa il passo indipendente.')
   self.assertEqual((Path(tmp)/'independent.txt').read_text(),'done')
   self.assertEqual(agent.turn_outcome,'completed')
   self.assertIn('strategy_evaluate',agent.suspended_tools)
 def test_provider_block_suspends_strategy_only_and_preserves_identity(self):
  with tempfile.TemporaryDirectory() as tmp:
   agent=SimpleNamespace(root=Path(tmp),depth=0,approve=lambda _:True,api=Mock(side_effect=RateLimited('cooldown 90s')))
   row=operating_rules.propose(tmp,agent.root,{'mode':'concise'})
   args={'id':row['id'],'model':'example','cases_json':json.dumps([{'prompt':str(i),'expected':i} for i in range(3)])}
   result=evolution_tools.dispatch(agent,'strategy_evaluate',args,tmp)
   self.assertEqual(result['status'],'blocked')
   self.assertNotIn('repair_verify',agent.suspended_tools)
   saved=operating_rules.read(tmp,agent.root,row['id'])
   self.assertEqual(saved['evaluation_progress']['identity']['model'],'example')
   self.assertEqual(saved['last_evaluation_error']['type'],'RateLimited')
   for _ in range(3): self.assertEqual(json.loads(reliable_runtime.execute(agent,'id','strategy_evaluate',args,[]))['status'],'blocked')
   self.assertEqual(agent.api.call_count,1)
 def test_finalize_interrupted_report_does_not_invent_success(self):
  with tempfile.TemporaryDirectory() as tmp:
   path=Path(tmp)/'qa_runs'/'run'/'results.json';path.parent.mkdir(parents=True)
   path.write_text(json.dumps({'status':'IN_PROGRESS','api_requests':0,'phases':{'done':{'status':'FAIL'},'pending':{'status':'NOT_RUN'}}}))
   finalize(path,'loop_detected');result=json.loads(path.read_text())
   self.assertEqual(result['status'],'INTERRUPTED');self.assertIsNone(result['api_requests'])
   self.assertEqual(result['phases']['done']['status'],'FAIL')
   self.assertEqual(result['phases']['pending']['status'],'NOT_VERIFIED')
   before=path.read_bytes();finalize(path,'loop_detected');self.assertEqual(before,path.read_bytes())
 def test_missing_results_json_is_created_without_pass(self):
  with tempfile.TemporaryDirectory() as tmp:
   run=Path(tmp)/'qa_runs'/'live'; run.mkdir(parents=True)
   (run/'REPORT.md').write_text('# Report\n- suite: PASS\n',encoding='utf-8')
   path=run/'results.json'; finalize(path,'incomplete'); result=json.loads(path.read_text(encoding='utf-8'))
   self.assertEqual(result['status'],'INTERRUPTED'); self.assertEqual(result['phases']['report']['status'],'NOT_VERIFIED')
   self.assertNotIn('"PASS"',path.read_text(encoding='utf-8'))
   self.assertIn('non sono prove',(run/'REPORT.md').read_text(encoding='utf-8'))
 def test_recover_skips_a_live_owner_and_finalizes_a_dead_one(self):
  with tempfile.TemporaryDirectory() as tmp:
   home=Path(tmp)/'home'; report=Path(tmp)/'qa_runs'/'run'/'results.json'; report.parent.mkdir(parents=True)
   report.write_text(json.dumps({'status':'IN_PROGRESS','api_requests':None,'phases':{'work':{'status':'IN_PROGRESS','reason':'turno aperto'}}}),encoding='utf-8')
   sleeper=subprocess.Popen([sys.executable,'-c','import time; time.sleep(30)'])
   try:
    self.assertTrue(remember(home,report)['ok'])
    index=json.loads((home/'active-reports.json').read_text(encoding='utf-8')); index[0]['pid']=sleeper.pid
    (home/'active-reports.json').write_text(json.dumps(index),encoding='utf-8')
    first=recover_reports(home)
    self.assertEqual(first['remaining'][0]['pid'],sleeper.pid)
    self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['status'],'IN_PROGRESS')
   finally:
    subprocess.run(['taskkill','/F','/T','/PID',str(sleeper.pid)],capture_output=True,timeout=15)
    sleeper.wait(timeout=15)
   second=recover_reports(home)
   saved=json.loads(report.read_text(encoding='utf-8'))
   self.assertEqual(saved['status'],'INTERRUPTED')
   self.assertEqual(saved['phases']['work']['status'],'NOT_VERIFIED')
   self.assertNotIn('"status": "PASS"',report.read_text(encoding='utf-8'))
   self.assertEqual(second['remaining'],[])
   self.assertTrue(forget(home,report)['ok'])
 def test_prepare_finalizes_an_orphaned_report(self):
  from runtime_bootstrap import prepare
  with tempfile.TemporaryDirectory() as tmp:
   home=Path(tmp)/'home'; report=Path(tmp)/'qa_runs'/'run'/'results.json'; report.parent.mkdir(parents=True)
   report.write_text(json.dumps({'status':'IN_PROGRESS','api_requests':None,'phases':{'work':{'status':'IN_PROGRESS','reason':'orfano'}}}),encoding='utf-8')
   remember(home,report)
   (home/'active-reports.json').write_text(json.dumps([{'path':str(report.resolve()),'pid':2**31-1}]),encoding='utf-8')
   prepare(home)
   saved=json.loads(report.read_text(encoding='utf-8'))
   self.assertEqual(saved['status'],'INTERRUPTED'); self.assertEqual(saved['phases']['work']['status'],'NOT_VERIFIED')
 def test_external_taskkill_mid_turn_then_resume_reuses_the_repair(self):
  with tempfile.TemporaryDirectory() as tmp:
   base=Path(tmp); appdata=base/'appdata'; root=base/'project'; ready=base/'ready.txt'; error=base/'error.txt'; log=base/'child.log'
   project=root/'qa_runs'/'kill_run'/'bench'; project.mkdir(parents=True)
   (project/'math_lib.py').write_text('def add(a,b):\n    return a+b\n',encoding='utf-8')
   child=base/'child.py'; child.write_text(textwrap.dedent(r'''
    import json,os,sys,traceback
    from pathlib import Path
    os.environ["APPDATA"]=sys.argv[1]
    import freellmapi
    from freellmapi import Agent
    import improvement_tools
    root=Path(sys.argv[2]); ready=Path(sys.argv[3]); error=Path(sys.argv[4]); project=Path(sys.argv[5])
    agent=Agent(root,"http://127.0.0.1:9","test","auto",interactive=False)
    agent.mode="auto"; agent.auto_skills=False; agent.streaming=False
    agent.enabled_tools.update(improvement_tools.NAMES)
    state={"n":0}
    def fake_api(route,data=None):
     n=state["n"]; state["n"]=n+1
     def pack(name,args,ident):
      return {"choices":[{"message":{"role":"assistant","content":None,"tool_calls":[{"id":ident,"type":"function","function":{"name":name,"arguments":json.dumps(args)}}]}}],"model":"scripted-local","usage":{"total_tokens":1}}
     if route!="chat/completions": return {"data":[{"id":"scripted-local"}]}
     if n==0:
      body=json.dumps({"status":"IN_PROGRESS","api_requests":None,"phases":{"work":{"status":"IN_PROGRESS","reason":"turno in corso"}}})
      return pack("write_file",{"path":"qa_runs/kill_turn/results.json","content":body},"write1")
     if n==1:
      return pack("repair_start",{"problem":"il banco accetta lo zero come positivo","project_path":str(project)},"repair1")
     if n==2:
      ready.write_text("READY",encoding="utf-8")
      return pack("shell",{"command":"Start-Sleep -Seconds 180","timeout":180},"sleep1")
     return {"choices":[{"message":{"role":"assistant","content":"chiusura"}}],"model":"scripted-local","usage":{"total_tokens":1}}
    agent.api=fake_api
    try: agent.turn("Ripara il bug freellmapi del banco: non accettare zero. Non attivare.")
    except Exception:
     error.write_text(traceback.format_exc(),encoding="utf-8"); raise
   ''').lstrip(),encoding='utf-8')
   env=os.environ.copy(); env['APPDATA']=str(appdata); env['PYTHONPATH']=str(Path(__file__).resolve().parent); env['PYTHONUTF8']='1'; env['PYTHONUNBUFFERED']='1'
   handle=log.open('w',encoding='utf-8',errors='replace')
   proc=subprocess.Popen([sys.executable,'-X','utf8',str(child),str(appdata),str(root),str(ready),str(error),str(project)],cwd=str(Path(__file__).resolve().parent),env=env,stdout=handle,stderr=subprocess.STDOUT)
   deadline=time.time()+90
   while time.time()<deadline and not ready.exists():
    if proc.poll() is not None: break
    time.sleep(0.2)
   self.assertTrue(ready.exists(), (error.read_text(encoding='utf-8') if error.exists() else '')+'\n'+(log.read_text(encoding='utf-8') if log.exists() else ''))
   time.sleep(0.8)
   killed=subprocess.run(['taskkill','/F','/T','/PID',str(proc.pid)],capture_output=True,timeout=20)
   proc.wait(timeout=20)
   handle.close()
   self.assertEqual(killed.returncode,0)
   report=root/'qa_runs'/'kill_turn'/'results.json'
   self.assertEqual(json.loads(report.read_text(encoding='utf-8'))['status'],'IN_PROGRESS')
   home=appdata/'freellmapi-cli'
   sessions=[]
   for item in (home/'sessions').glob('*.json'):
    try: data=json.loads(item.read_text(encoding='utf-8'))
    except (OSError,ValueError): continue
    if isinstance(data,list): sessions.append(item)
   self.assertEqual(len(sessions),1, [item.name for item in (home/'sessions').glob('*')])
   session=sessions[0]
   messages=json.loads(session.read_text(encoding='utf-8'))
   self.assertFalse(any(row.get('tool_call_id')=='sleep1' for row in messages))
   recovered=recover_reports(home)
   self.assertTrue(recovered['ok'])
   saved=json.loads(report.read_text(encoding='utf-8'))
   self.assertEqual(saved['status'],'INTERRUPTED')
   self.assertEqual(saved['phases']['work']['status'],'NOT_VERIFIED')
   self.assertNotIn('"status": "PASS"',report.read_text(encoding='utf-8'))
   import freellmapi
   original=freellmapi.HOME
   freellmapi.HOME=home
   try:
    import improvement_tools
    resumed=freellmapi.Agent(root,'http://127.0.0.1:9','test','auto',interactive=False)
    resumed.mode='auto'; resumed.auto_skills=False; resumed.streaming=False; resumed.context_limit=1_000_000
    resumed.messages=messages; resumed.enabled_tools.update(improvement_tools.NAMES)
    state={'n':0}
    def resume_api(route,data=None):
     n=state['n']; state['n']=n+1
     if route!='chat/completions': return {'data':[{'id':'scripted-local'}]}
     if n==0:
      return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'repair2','type':'function','function':{'name':'repair_start','arguments':json.dumps({'problem':'Procedi','project_path':str(project)})}}]}}],'model':'scripted-local','usage':{'total_tokens':1}}
     return {'choices':[{'message':{'role':'assistant','content':'Stato invariato. Nessuna nuova attivazione.'}}],'model':'scripted-local','usage':{'total_tokens':1}}
    resumed.api=resume_api
    with patch('freellmapi.output'): resumed.turn('Procedi')
    records=list((home/'repair-lab').glob('*/record.json'))
    self.assertEqual(len(records),1)
    record=json.loads(records[0].read_text(encoding='utf-8'))
    self.assertEqual(record['state'],'staged')
    self.assertNotIn(record['state'],('activated_restart_required','activating'))
    from work_ledger import load
    ledger=load(resumed.session)
    self.assertIn('Ripara il bug freellmapi',ledger['original_request'])
    self.assertEqual(ledger['bound_repair'],record['id'])
    self.assertGreaterEqual(ledger.get('continuations',0),1)
    target=os.environ.get('FREELLMAPI_KILL_EVIDENCE')
    if target:
     payload={'observed':True,'origin':'local_process','ok':True,'exit_code':0,'kill_returncode':killed.returncode,'status_after_taskkill':'IN_PROGRESS','status_after_recover':saved['status'],'phase_after_recover':saved['phases']['work']['status'],'pass_status_present':'"status": "PASS"' in report.read_text(encoding='utf-8'),'shell_result_recorded':any(row.get('tool_call_id')=='sleep1' for row in messages),'repair_records':1,'repair_state':record['state'],'repair_id':record['id'],'original_request':ledger['original_request'],'continuations':ledger.get('continuations',0),'activation_repeated':record['state'] in ('activated_restart_required','activating'),'owner_pid_checked':True}
     Path(target).parent.mkdir(parents=True,exist_ok=True)
     Path(target).write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding='utf-8')
   finally:
    freellmapi.HOME=original
 def test_strategy_block_does_not_prevent_independent_file_work(self):
  from freellmapi import Agent
  with tempfile.TemporaryDirectory() as tmp:
   agent=Agent(Path(tmp),'http://localhost','test','auto');agent.session=Path(tmp)/'session.json';agent.mode='auto'
   agent.suspended_tools={'strategy_evaluate':{'status':'blocked'}}
   definitions=agent.tool_definitions()
   self.assertNotIn('strategy_evaluate',[d['function']['name'] for d in definitions])
   result=reliable_runtime.execute(agent,'independent','write_file',{'path':'proof.txt','content':'preserved'},definitions)
   self.assertTrue(json.loads(result)['changed']);self.assertEqual((Path(tmp)/'proof.txt').read_text(),'preserved')
