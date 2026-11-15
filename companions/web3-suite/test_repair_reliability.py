"""Regressions for repair isolation, identity, recovery, reports and resume."""
import hashlib,json,os,shutil,subprocess,sys,tempfile,textwrap,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import improvement_tools
import release_transaction
import repair_lab as lab
from loop_guard import LoopGuard
from qa_evidence import validate
from work_ledger import load, record_request, record_tool


def _write(path,text):
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(textwrap.dedent(text).lstrip(),encoding='utf-8')


MATH='''
def add_strictly_positive(a,b):
 if a<0 or b<0: raise ValueError("Must be positive")
 return a+b
def generation():
 return 1
'''
EXISTING='''
import unittest
from math_lib import add_strictly_positive
class Existing(unittest.TestCase):
 def test_sum(self): self.assertEqual(add_strictly_positive(2,3),5)
 def test_negative(self):
  with self.assertRaises(ValueError): add_strictly_positive(-1,2)
'''
REGRESSION='''
import unittest
from math_lib import add_strictly_positive
class Regression(unittest.TestCase):
 def test_zero(self):
  with self.assertRaises(ValueError): add_strictly_positive(0,1)
 def test_positive(self): self.assertEqual(add_strictly_positive(1,2),3)
'''
LABEL='''
import unittest
from math_lib import generation
class Label(unittest.TestCase):
 def test_generation(self): self.assertEqual(generation(),2)
'''


class RepairReliabilityTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.home=Path(self.tmp.name)/'home'
        self.run=Path(self.tmp.name)/'qa_runs'/'reliability_e2e'
        self.project=self.run/'project'
        _write(self.project/'math_lib.py',MATH)
        _write(self.project/'test_existing.py',EXISTING)

    def test_sys_path_and_importlib_contamination_are_correctable(self):
        ident=lab.start(self.home,'zero non rifiutato',self.project)['id']
        lab.edit(self.home,ident,'math_lib.py','a<0 or b<0','a<=0 or b<=0')
        stage=str((lab.load(self.home,ident)[0]/'stage').resolve())
        cases={
            'sys.path': 'import sys,unittest\nfrom pathlib import Path\nsys.path.insert(0,str(Path(r"'+stage+'")))\nimport math_lib\nclass T(unittest.TestCase):\n def test_zero(self):\n  with self.assertRaises(ValueError): math_lib.add_strictly_positive(0,1)\n',
            'importlib': 'import importlib.util,unittest\nspec=importlib.util.spec_from_file_location("math_lib",r"'+stage+'\\math_lib.py")\nmath_lib=importlib.util.module_from_spec(spec); spec.loader.exec_module(math_lib)\nclass T(unittest.TestCase):\n def test_zero(self):\n  with self.assertRaises(ValueError): math_lib.add_strictly_positive(0,1)\n',
        }
        for kind,code in cases.items():
            with self.subTest(kind=kind):
                row=lab.verify(self.home,ident,code)
                self.assertEqual(row['code'],'test_contamination',row.get('evidence',{}).get('message'))
                self.assertNotEqual(row['state'],'tested')
                baseline=row['evidence']['baseline']
                self.assertTrue(baseline.get('contamination'))
                self.assertGreater(baseline.get('tests',0),0)
                self.assertEqual(baseline.get('failures'),0)
                self.assertFalse(baseline.get('ok'))
                loaded=[item for item in baseline['provenance']['contaminated_modules']+baseline['provenance']['contaminated_opens'] if 'math_lib' in Path(str(item.get('file',''))).name]
                self.assertTrue(loaded,baseline['provenance'])
                opened=Path(loaded[0]['file'])
                self.assertTrue(str(opened).lower().startswith(stage.lower()),opened)
                self.assertEqual(loaded[0]['sha256'],hashlib.sha256(opened.read_bytes()).hexdigest())
                self.assertIn('test_contamination',row['evidence']['message'])
                self.assertIn('sys.path',row['evidence']['message'])
                report=json.loads((lab.load(self.home,ident)[0]/'results.json').read_text(encoding='utf-8'))
                self.assertEqual(report['phases']['baseline_regression']['status'],'FAIL')
                self.assertNotIn('baseline_regression: PASS',(lab.load(self.home,ident)[0]/'REPORT.md').read_text(encoding='utf-8'))
        with self.assertRaisesRegex(ValueError,'Due verifiche'): lab.verify(self.home,ident,REGRESSION)

    def test_equivalent_copy_and_reword_keep_the_attempt_count(self):
        problem='il calcolo rifiuta solo i numeri negativi'
        first=lab.start(self.home,problem,self.project)
        copied=self.run/'copy'; shutil.copytree(self.project,copied)
        second=lab.start(self.home,'per favore sistema il calcolo che rifiuta solo i numeri negativi',copied)
        self.assertEqual(second['id'],first['id']); self.assertTrue(second.get('existing'))
        ident=first['id']; lab.edit(self.home,ident,'math_lib.py','a<0 or b<0','a<=0 or b<=0')
        always='import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n'
        self.assertEqual(lab.verify(self.home,ident,always)['code'],'baseline_not_reproduced')
        lab.verify(self.home,ident,always)
        with self.assertRaisesRegex(ValueError,'Due verifiche'): lab.verify(self.home,ident,always)
        other=self.run/'another'; shutil.copytree(self.project,other)
        self.assertEqual(lab.start(self.home,'nuovo testo completamente diverso sul medesimo sorgente',other)['id'],ident)

    def _verified_fix(self):
        ident=lab.start(self.home,'zero non rifiutato',self.project)['id']
        self.assertEqual(Path(lab.load(self.home,ident)[1]['source_root']),self.project.resolve())
        lab.edit(self.home,ident,'math_lib.py','a<0 or b<0','a<=0 or b<=0')
        result=lab.verify(self.home,ident,REGRESSION)
        self.assertEqual(result['state'],'tested',result.get('evidence'))
        self.assertGreater(result['evidence']['baseline']['failures'],0)
        self.assertFalse(result['evidence']['baseline'].get('contamination'))
        self.assertTrue(result['evidence']['regression']['ok'])
        self.assertTrue(result['evidence']['suite']['ok'])
        base_modules=result['evidence']['baseline']['provenance']['modules']
        stage_modules=result['evidence']['regression']['provenance']['modules']
        self.assertTrue(any(row['origin']=='tree' and row['file'].endswith('math_lib.py') for row in base_modules))
        self.assertNotEqual(
            next(row['sha256'] for row in base_modules if row['file'].endswith('math_lib.py')),
            next(row['sha256'] for row in stage_modules if row['file'].endswith('math_lib.py')))
        report_path=self.run/'results.json'
        self.assertTrue(report_path.is_file()); self.assertTrue((self.run/'REPORT.md').is_file())
        checked=validate(report_path)
        self.assertTrue(checked['ok'],checked)
        report=json.loads(report_path.read_text(encoding='utf-8'))
        self.assertEqual(report['phases']['baseline_regression']['status'],'PASS')
        self.assertEqual(report['phases']['candidate_regression']['status'],'PASS')
        self.assertIsNone(report['api_requests'])
        self.assertEqual(report['phases']['suite']['status'],'PASS')
        markdown=(self.run/'REPORT.md').read_text(encoding='utf-8')
        self.assertIn('baseline_regression: PASS',markdown)
        self.assertIn('suite: PASS',markdown)
        self.assertNotIn('baseline_regression: FAIL',markdown)
        return ident

    def test_clean_regression_differs_between_baseline_and_candidate(self):
        self._verified_fix()

    def test_activation_injected_crash_conflict_and_restored_suite(self):
        ident=self._verified_fix()
        activated=lab.promote(self.home,ident)
        self.assertEqual(activated['state'],'activated_restart_required')
        activation=json.loads((lab.load(self.home,ident)[0]/'activation.json').read_text(encoding='utf-8'))
        self.assertTrue(activation['hashes_match']); self.assertEqual(activation['exit_code'],0)
        self.assertTrue((self.project/('test_autorepair_'+ident+'.py')).is_file())
        with self.assertRaisesRegex(ValueError,'repair_already_activated'): lab.promote(self.home,ident)
        second=lab.start(self.home,'generation counter',self.project)
        self.assertNotEqual(second['id'],ident)
        other=second['id']
        lab.edit(self.home,other,'math_lib.py','return 1','return 2')
        verified=lab.verify(self.home,other,LABEL)
        self.assertEqual(verified['state'],'tested',verified.get('evidence'))
        before={path: hashlib.sha256(path.read_bytes()).hexdigest() for path in self.project.glob('*.py')}
        script=_write_and_return(self.home/'crash_promote.py','''
import os,sys
sys.path.insert(0,sys.argv[1])
import repair_lab as target
real=target.replace
def crash(source,dest):
    real(source,dest); os._exit(97)
target.replace=crash
target.promote(sys.argv[2],sys.argv[3])
''')
        completed=subprocess.run([sys.executable,'-X','utf8',str(script),str(Path(__file__).resolve().parent),str(self.home),other],capture_output=True,text=True,timeout=120)
        self.assertEqual(completed.returncode,97,completed.stderr[-2000:]+completed.stdout[-2000:])
        self.assertEqual(release_transaction.recover(self.home),[other])
        restored=lab.load(self.home,other)[1]
        self.assertEqual(restored['state'],'rolled_back')
        proof=json.loads((lab.load(self.home,other)[0]/'rollback.json').read_text(encoding='utf-8'))
        self.assertTrue(proof['hashes_match']); self.assertTrue(proof['new_files_removed']); self.assertEqual(proof['origin'],'repair_runner')
        self.assertFalse((self.project/('test_autorepair_'+other+'.py')).exists())
        self.assertIn('return 1',(self.project/'math_lib.py').read_text(encoding='utf-8'))
        self.assertIn('a<=0 or b<=0',(self.project/'math_lib.py').read_text(encoding='utf-8'))
        suite=lab.run_tests(self.project,self.home/'restored-suite','restored')
        self.assertTrue(suite['ok'],suite)
        self.assertEqual(suite['origin'],'repair_runner')
        self.assertGreater(suite['tests'],0)
        user=(self.project/'math_lib.py').read_text(encoding='utf-8').replace('return a+b','return a+b  # user edit')
        (self.project/'math_lib.py').write_text(user,encoding='utf-8')
        with self.assertRaisesRegex(ValueError,'recovery_conflict'): lab.rollback(self.home,ident)
        self.assertEqual((self.project/'math_lib.py').read_text(encoding='utf-8'),user)
        self.assertEqual(lab.load(self.home,ident)[1]['state'],'recovery_conflict')
        self.assertEqual(before[self.project/'test_existing.py'],hashlib.sha256((self.project/'test_existing.py').read_bytes()).hexdigest())

    def test_bad_destination_does_not_fall_back_or_edit_controllers(self):
        agent=SimpleNamespace(session=self.home/'session.json',root=self.project,depth=0,approve=lambda _text: True,continuation_turn=False,active_qa_reports=set())
        with self.assertRaisesRegex(ValueError,'repair_destination_invalid'):
            improvement_tools.dispatch(agent,'repair_start',{'problem':'bug','project_path':str(self.home)},self.home)
        self.assertTrue(str(agent.repair_destination_error).startswith('repair_destination_invalid'))
        with self.assertRaisesRegex(ValueError,'Nessun ripiego'):
            improvement_tools.dispatch(agent,'repair_start',{'problem':'bug'},self.home)
        records=list((self.home/'repair-lab').glob('*/record.json')) if (self.home/'repair-lab').exists() else []
        self.assertEqual(records,[])
        from freellmapi import Agent
        runtime=Agent(self.project,'http://127.0.0.1:9','test','auto')
        runtime.session=self.home/'session.json'; runtime.mode='auto'; runtime.loop_guard=LoopGuard()
        runtime.repair_destination_error=agent.repair_destination_error
        controller=self.project/'repair_lab.py'; controller.write_text('original = 1\n',encoding='utf-8')
        blocked=json.loads(runtime._guard_call('write_file',{'path':'repair_lab.py','content':'original = 2\n'}))
        self.assertEqual(blocked['code'],'protected_controller')
        self.assertEqual(controller.read_text(encoding='utf-8'),'original = 1\n')
        shell=json.loads(runtime._guard_call('shell',{'command':'Set-Content repair_lab.py x'}))
        self.assertEqual(shell['code'],'protected_controller')

    def test_retry_requires_prerequisite_and_shell_or_subagent_cannot_bypass(self):
        guard=LoopGuard()
        self.assertIsNone(guard.observe('repair_verify',{'id':'a'*32,'test_code':'one'},'Errore: invalid_id: ID riparazione non valido',True))
        blocked=json.loads(guard.before('repair_verify',{'id':'b'*32,'test_code':'completely different'}))
        self.assertEqual(blocked['code'],'prerequisite_required')
        guard.observe('repair_status',{},'{"repairs":[]}')
        self.assertIsNone(guard.before('repair_verify',{'id':'c'*32,'test_code':'third'}))
        suspended=LoopGuard()
        for _ in range(3): suspended.observe('repair_verify',{'id':'d'*32},'{"ok":false,"code":"test_contamination"}',True)
        self.assertEqual(json.loads(suspended.before('repair_edit',{'id':'e'*32}))['code'],'phase_suspended')
        self.assertIsNone(suspended.before('write_file',{'path':'notes.txt'}))
        from freellmapi import Agent
        runtime=Agent(self.project,'http://127.0.0.1:9','test','auto')
        runtime.session=self.home/'guard.json'; runtime.mode='auto'; runtime.context_limit=131072
        calls=[]
        def response(*_args):
            calls.append(1)
            if len(calls)==1:
                return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'sleep','function':{'name':'shell','arguments':json.dumps({'command':'Start-Sleep 30'})}}]}}]}
            return {'choices':[{'message':{'role':'assistant','content':'Report aggiornato senza PASS.'}}]}
        runtime.loop_guard=LoopGuard(); runtime.loop_guard.suspensions['repair_verify']={'ok':False}
        self.assertEqual(json.loads(runtime._guard_call('agent_start',{'task':'rifai la riparazione'}))['code'],'subagent_bypass_blocked')
        runtime.cooldowns={'remote':time.time()+3600}
        with patch.object(runtime,'api',side_effect=response),patch('freellmapi.output'),patch('shell_runner.run',side_effect=AssertionError('sleep executed')):
            runtime.turn('Continua il lavoro indipendente.')
        self.assertEqual(runtime.turn_outcome,'completed')
        self.assertIn('wait_bypass_blocked',runtime.messages[-2]['content'])

    def test_resume_and_compaction_do_not_recreate_or_reactivate(self):
        ident=lab.start(self.home,'zero non rifiutato',self.project)['id']
        session=self.home/'resume.json'
        record_request(session,'Correggi il calcolo del banco di prova')
        record_tool(session,'repair_start',{'problem':'zero non rifiutato','project_path':str(self.project)},{'id':ident,'ok':True,'state':'staged','source_root':str(self.project.resolve())})
        record_request(session,'Procedi',continuation=True)
        saved=load(session)
        self.assertTrue(saved['original_request'].startswith('Correggi il calcolo'))
        self.assertEqual(saved['continuations'],1)
        agent=SimpleNamespace(session=session,root=self.project,depth=0,approve=lambda _text: True,continuation_turn=True,active_qa_reports=set())
        resumed=improvement_tools.dispatch(agent,'repair_start',{'problem':'Procedi'},self.home)
        self.assertEqual(resumed['id'],ident); self.assertTrue(resumed.get('existing'))
        self.assertEqual(len(list((self.home/'repair-lab').glob('*/record.json'))),1)
        other=self.run/'elsewhere'; other.mkdir()
        with self.assertRaisesRegex(ValueError,'continuation_scope'):
            improvement_tools.dispatch(agent,'repair_start',{'problem':'costruisci un altro programma','project_path':str(other)},self.home)
        from freellmapi import Agent
        runtime=Agent(self.project,'http://127.0.0.1:9','test','auto')
        runtime.session=session; runtime.mode='ask'; runtime.context_limit=131072
        runtime.messages=[
            {'role':'user','content':'Correggi il calcolo del banco di prova'},
            {'role':'assistant','content':'Sto esaminando il banco.'},
            {'role':'user','content':'Mostrami lo stato.'},
            {'role':'assistant','content':'La riparazione e ancora aperta.'},
            {'role':'user','content':'Continua a mostrare lo stato del banco.'},
            {'role':'assistant','content':'Resta aperta.'},
        ]
        with patch.object(runtime,'api',return_value={'choices':[{'message':{'content':'Riassunto del modello, non una prova.'}}]}),patch('freellmapi.output'):
            runtime.compact(force=True)
        self.assertIn('Correggi il calcolo del banco di prova',runtime.system())
        self.assertEqual(load(session)['original_request'],'Correggi il calcolo del banco di prova')
        self.assertTrue(load(session)['model_claims'])
        self.assertFalse(any(row.get('verified') and row.get('tool')!='repair_start' for row in load(session).get('events',[])))


def _write_and_return(path,text):
    _write(path,text); return path


if __name__=='__main__':
    unittest.main()
