import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import repair_lab as lab
import generated_tools as gen
import improvement_runtime as runtime

REGRESSION="import unittest\nimport subject\nclass Regression(unittest.TestCase):\n def test_bug(self): self.assertEqual(subject.value(),2)\n"

class RepairTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name)/'source';self.root.mkdir();self.home=Path(self.tmp.name)/'home'
        (self.root/'subject.py').write_text('def value(): return 1\n',encoding='utf-8')
        self.patch=patch.object(lab,'ROOT',self.root);self.patch.start();self.addCleanup(self.patch.stop)
    def candidate(self):
        row=lab.start(self.home,'wrong value');lab.edit(self.home,row['id'],'subject.py','return 1','return 2');return row['id']
    def test_real_regression_full_suite_promote_and_rollback(self):
        ident=self.candidate();result=lab.verify(self.home,ident,REGRESSION)
        self.assertEqual(result['state'],'tested');self.assertEqual(result['evidence']['baseline']['failures'],1)
        self.assertIn('return 1',(self.root/'subject.py').read_text())
        result=lab.promote(self.home,ident);self.assertEqual(result['state'],'activated_restart_required')
        self.assertTrue(list(self.root.glob('test_autorepair_*.py')))
        lab.rollback(self.home,ident);self.assertIn('return 1',(self.root/'subject.py').read_text())
        self.assertFalse(list(self.root.glob('test_autorepair_*.py')))
    def test_failed_post_activation_automatically_restores(self):
        ident=self.candidate();lab.verify(self.home,ident,REGRESSION)
        with patch.object(lab,'run_tests',return_value={'ok':False,'failures':1}):
            self.assertEqual(lab.promote(self.home,ident)['state'],'rolled_back')
        self.assertIn('return 1',(self.root/'subject.py').read_text())
    def test_unreproduced_and_attempt_limit(self):
        ident=self.candidate();code=REGRESSION.replace('subject.value(),2','True')
        # A syntactically valid always-passing regression must not pass the baseline gate.
        code='import unittest\nclass T(unittest.TestCase):\n def test_ok(self): self.assertTrue(True)\n'
        self.assertEqual(lab.verify(self.home,ident,code)['state'],'failed')
        lab.verify(self.home,ident,code)
        with self.assertRaisesRegex(ValueError,'Due verifiche'):lab.verify(self.home,ident,code)
        with self.assertRaises(ValueError):lab.promote(self.home,ident)
        self.assertEqual(lab.start(self.home,'wrong value')['id'],ident)
    def test_reject_modified_test_or_stale_source(self):
        ident=self.candidate();folder,_=lab.load(self.home,ident)
        (folder/'stage'/'test_cheat.py').write_text('')
        with self.assertRaisesRegex(ValueError,'non ammesse'):lab.verify(self.home,ident,REGRESSION)
    def test_stage_tamper_after_tests_blocked(self):
        ident=self.candidate();lab.verify(self.home,ident,REGRESSION)
        folder,_=lab.load(self.home,ident);(folder/'stage'/'subject.py').write_text('def value(): return 3')
        with self.assertRaisesRegex(ValueError,'cambiato'):lab.promote(self.home,ident)
    def test_existing_source_changed_no_overwrite(self):
        ident=self.candidate();lab.verify(self.home,ident,REGRESSION)
        (self.root/'subject.py').write_text('def value(): return 77')
        with self.assertRaisesRegex(ValueError,'installato cambiato'):lab.promote(self.home,ident)
        self.assertIn('77',(self.root/'subject.py').read_text())
    def test_isolated_home_preserves_installed_dependencies(self):
        import importlib.util
        if importlib.util.find_spec('rich') is None: self.skipTest('Rich not installed')
        (self.root/'test_dependency.py').write_text('import unittest\nimport rich\nclass T(unittest.TestCase):\n def test_import(self): self.assertTrue(callable(rich.print))\n')
        output=self.home/'dependency-check';output.mkdir(parents=True)
        self.assertTrue(lab.run_tests(self.root,output,'dependency')['ok'])

SCHEMA={'type':'object','properties':{'x':{'type':'integer'}},'required':['x']}
class GeneratedTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)
    def test_build_run_and_runtime_rollback_without_replay(self):
        first=gen.build(self.home,'calc','test','def run(args): return args["x"]+1',SCHEMA,[{'input':{'x':1},'expected':2},{'input':{'x':2},'expected':3}])
        self.assertTrue(first['passed']);self.assertEqual(gen.run(self.home,'calc',{'x':0})['result'],1)
        second=gen.build(self.home,'calc','test','def run(args): return 1/args["x"]',SCHEMA,[{'input':{'x':1},'expected':1},{'input':{'x':2},'expected':.5}])
        self.assertTrue(second['passed']);result=gen.run(self.home,'calc',{'x':0})
        self.assertFalse(result['reexecuted']);self.assertEqual(result['rolled_back'],first['version'])
        self.assertEqual(gen.run(self.home,'calc',{'x':0})['result'],1)
    def test_failed_cases_never_activate(self):
        report=gen.build(self.home,'bad','test','def run(args): return 0',SCHEMA,[{'input':{'x':1},'expected':1},{'input':{'x':2},'expected':2}])
        self.assertFalse(report['passed']);self.assertEqual(gen.listing(self.home),[])
    def test_no_shell_files_or_introspection(self):
        for code in ['import os\ndef run(args): return 1','def run(args): return open("secret")','def run(args): return args.__class__']:
            with self.assertRaises(ValueError):gen.validate(code)
        with self.assertRaises(ValueError):gen.check_args(SCHEMA,{'x':True})
    def test_modified_code_cannot_run(self):
        report=gen.build(self.home,'calc','test','def run(args): return args["x"]',SCHEMA,[{'input':{'x':1},'expected':1},{'input':{'x':2},'expected':2}])
        (self.home/'generated-tools'/'calc'/(report['version']+'.py')).write_text('def run(args): return 99')
        with self.assertRaisesRegex(ValueError,'modificato'):gen.run(self.home,'calc',{'x':1})

class CoordinationTests(unittest.TestCase):
    def test_objectives_survive_new_agent_same_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=SimpleNamespace(session=Path(tmp)/'one.json',root=Path(tmp))
            runtime.record(a,'objective',{'prompt':'fix bug'})
            a.session=Path(tmp)/'two.json'
            self.assertEqual(runtime.state(a)['events'][0]['data']['prompt'],'fix bug')
            a.root=Path(tmp)/'different';self.assertEqual(runtime.state(a)['events'],[])
    def test_actual_model_probe_and_ready_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            def api(route,payload):
                nonce=payload['messages'][0]['content'].split()[-1]
                return {'model':'real-model','choices':[{'message':{'tool_calls':[{'function':{'name':'probe_echo','arguments':json.dumps({'value':nonce})}}]}}]}
            a=SimpleNamespace(session=Path(tmp)/'a.json',root=Path(tmp),api=api,server_router=True,messages=[],cooldowns={},tool_definitions=lambda:[],_api=lambda route:{'data':[{'id':'real-model','execution_status':'ready'}]})
            result=runtime.benchmark(a,'auto',tmp);self.assertEqual(result['actual'],'real-model');self.assertTrue(result['tool_call_passed'])
            self.assertEqual(runtime.select(a,tmp)['model'],'real-model')
            a._api=lambda route:{'data':[]}
            self.assertEqual(runtime.select(a,tmp)['model'],'auto:reliable')
    def test_native_routing(self):
        from tool_routing import suggested,simple_native_request
        prompt='Implementa strumenti per automigliorarti e correggere i tuoi bug'
        self.assertIn('repair_start',suggested(prompt));self.assertTrue(simple_native_request(prompt))
    def test_coding_probe_checks_generated_code(self):
        with tempfile.TemporaryDirectory() as tmp:
            a=SimpleNamespace(session=Path(tmp)/'a.json',root=Path(tmp),api=lambda *args:{'model':'coder','choices':[{'message':{'content':'def run(args): return sorted(set(x for x in args["values"] if isinstance(x,int) and not isinstance(x,bool)))'}}]})
            self.assertTrue(runtime.benchmark(a,'auto',tmp,'coding')['passed'])
            a.api=lambda *args:{'model':'coder','choices':[{'message':{'content':'def run(args): return args["values"]'}}]}
            self.assertFalse(runtime.benchmark(a,'auto',tmp,'coding')['passed'])
    def test_first_request_inspects_native_coordinator(self):
        from freellmapi import Agent
        with tempfile.TemporaryDirectory() as tmp:
            agent=Agent(tmp,'http://localhost','test','auto');agent.session=Path(tmp)/'session.json';agent.context_limit=128000
            answer={'choices':[{'message':{'role':'assistant','content':'Strumenti disponibili.'}}]}
            with patch.object(agent,'api',return_value=answer) as api,patch('freellmapi.output'):
                agent.turn('Voglio strumenti per automigliorarti')
            self.assertEqual(api.call_args.args[1]['tool_choice']['function']['name'],'coordinator_state')

if __name__=='__main__': unittest.main()
