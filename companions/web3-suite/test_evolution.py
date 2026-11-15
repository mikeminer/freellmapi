import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import repair_lab as lab
import release_transaction as releases
import managed_state as states
import operating_rules as rules
import dependency_env as deps

TEST="""import unittest,subject,os,json
from pathlib import Path
class Regression(unittest.TestCase):
 def test_modules(self): self.assertEqual(subject.value(),5)
 def test_state(self):
  p=Path(os.environ['APPDATA'])/'freellmapi-cli'/'managed-state'/'demo.json'
  value=json.loads(p.read_text())
  self.assertEqual(value['schema_version'],2)
  self.assertEqual(value['data']['label'],'kept')
"""
class MultiReleaseTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)/'home';self.root=Path(self.tmp.name)/'src';self.root.mkdir()
  (self.root/'subject.py').write_text('import other\ndef value(): return other.value()+1\n')
  (self.root/'other.py').write_text('def value(): return 1\n')
  p=patch.object(lab,'ROOT',self.root);p.start();self.addCleanup(p.stop)
  states.register(self.home,'demo',{'name':'kept'})
 def prepared(self):
  ident=lab.start(self.home,'multi')['id']
  lab.edit(self.home,ident,'subject.py','+1','+2');lab.edit(self.home,ident,'other.py','return 1','return 3')
  states.prepare(self.home,ident,'demo',2,[{'op':'rename','key':'name','to':'label'}])
  self.assertEqual(lab.verify(self.home,ident,TEST)['state'],'tested')
  return ident
 def test_multi_code_state_commit_and_rollback(self):
  ident=self.prepared();self.assertEqual(lab.promote(self.home,ident)['state'],'activated_restart_required')
  self.assertEqual(json.loads(states.path(self.home,'demo').read_text())['schema_version'],2)
  lab.rollback(self.home,ident)
  self.assertIn('+1',(self.root/'subject.py').read_text());self.assertIn('return 1',(self.root/'other.py').read_text())
  self.assertEqual(json.loads(states.path(self.home,'demo').read_text())['data'],{'name':'kept'})
 def test_failed_post_tests_restore_all(self):
  ident=self.prepared()
  with patch.object(lab,'run_tests',return_value={'ok':False}): self.assertEqual(lab.promote(self.home,ident)['state'],'rolled_back')
  self.assertIn('+1',(self.root/'subject.py').read_text());self.assertEqual(json.loads(states.path(self.home,'demo').read_text())['schema_version'],1)
 def test_mid_commit_exception_restores_all(self):
  ident=self.prepared();original=lab.replace;count=[0]
  def fail(source,target):
   count[0]+=1
   if count[0]==2: raise OSError('simulated disk failure')
   return original(source,target)
  with patch.object(lab,'replace',side_effect=fail):
   with self.assertRaises(OSError):lab.promote(self.home,ident)
  self.assertIn('+1',(self.root/'subject.py').read_text());self.assertIn('return 1',(self.root/'other.py').read_text())
 def test_crash_journal_recovered_on_startup(self):
  ident=self.prepared();lab.promote(self.home,ident)
  folder,record=lab.load(self.home,ident);record['state']='activating';lab.store(folder/'record.json',record)
  self.assertEqual(releases.recover(self.home),[ident]);self.assertIn('+1',(self.root/'subject.py').read_text())
 def test_new_state_write_blocks_rollback_without_data_loss(self):
  ident=self.prepared();lab.promote(self.home,ident)
  states.path(self.home,'demo').write_text('{"new_user_data":true}')
  with self.assertRaisesRegex(ValueError,'altro processo'):lab.rollback(self.home,ident)
  self.assertIn('new_user_data',states.path(self.home,'demo').read_text())
 def test_unsafe_state_and_bad_migration_rejected(self):
  with self.assertRaises(ValueError):states.register(self.home,'../config',{})
  ident=lab.start(self.home,'bad migration')['id']
  with self.assertRaises(ValueError):states.prepare(self.home,ident,'demo',3,[])
  with self.assertRaises(ValueError):states.prepare(self.home,ident,'demo',2,[{'op':'rename','key':'absent','to':'label'}])
 def test_new_module_is_removed_on_rollback(self):
  ident=lab.start(self.home,'add module')['id'];lab.add_module(self.home,ident,'fresh.py','ANSWER=7')
  code='import unittest,importlib.util\nclass T(unittest.TestCase):\n def test_module(self): self.assertIsNotNone(importlib.util.find_spec("fresh"))\n'
  self.assertEqual(lab.verify(self.home,ident,code)['state'],'tested');lab.promote(self.home,ident);self.assertTrue((self.root/'fresh.py').exists())
  lab.rollback(self.home,ident);self.assertFalse((self.root/'fresh.py').exists())

CASES=[{'prompt':json.dumps({'value':i}),'expected':{'value':i}} for i in range(3)]
class StrategyTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name);self.project=self.home/'project'
  self.agent=SimpleNamespace(root=self.project,last_request={})
  def api(route,payload):
   candidate='short direct' in payload['messages'][0]['content']
   return {'model':'same-model','choices':[{'message':{'content':payload['messages'][-1]['content']},'finish_reason':'stop'}],'usage':{'total_tokens':50 if candidate else 100}}
  self.agent.api=api
 def evaluated(self):
  row=rules.propose(self.home,self.project,{'mode':'concise','max_tokens':512})
  self.assertEqual(rules.evaluate(self.agent,self.home,row['id'],'same-model',CASES)['state'],'evaluated');return row['id']
 def test_paired_gate_and_versioned_activation(self):
  ident=self.evaluated();rules.activate(self.home,self.project,ident)
  self.assertEqual(rules.current(self.home,self.project)['settings']['max_tokens'],512)
  self.assertEqual(rules.rollback(self.home,self.project)['active'],'default')
 def test_no_activation_without_evidence_or_permission_changes(self):
  row=rules.propose(self.home,self.project,{'mode':'concise'})
  with self.assertRaises(ValueError):rules.activate(self.home,self.project,row['id'])
  with self.assertRaises(ValueError):rules.propose(self.home,self.project,{'approve_all':True})
 def test_wrong_answers_not_promoted(self):
  ident=rules.propose(self.home,self.project,{'mode':'concise'})['id']
  self.agent.api=lambda *args:{'model':'m','choices':[{'message':{'content':'{}'}}],'usage':{'total_tokens':1}}
  self.assertEqual(rules.evaluate(self.agent,self.home,ident,'m',CASES)['state'],'not_better')
 def test_continuous_monitor_rolls_back(self):
  ident=self.evaluated();rules.activate(self.home,self.project,ident)
  for _ in range(4): rules.observe(self.home,self.project,ident,'incomplete',2)
  self.assertEqual(rules.observe(self.home,self.project,ident,'incomplete',2)['active'],'default')
  self.assertEqual(rules.read(self.home,self.project,ident)['state'],'reverted')
 def test_corrupt_rules_revert_to_known_default(self):
  ident=self.evaluated();rules.activate(self.home,self.project,ident)
  p=rules.folder(self.home,self.project)/(ident+'.json');value=json.loads(p.read_text());value['settings']['repeat_limit']=99;p.write_text(json.dumps(value))
  self.assertEqual(rules.current(self.home,self.project)['id'],'default')
 def test_different_routes_not_comparable(self):
  ident=rules.propose(self.home,self.project,{'mode':'concise'})['id'];original=self.agent.api;count=[0]
  def api(*args):
   r=original(*args);count[0]+=1;r['model']='m'+str(count[0]);return r
  self.agent.api=api
  self.assertEqual(rules.evaluate(self.agent,self.home,ident,'auto',CASES)['state'],'not_better')

class DependencyTests(unittest.TestCase):
 def test_only_pinned_requirements(self):
  for bad in ['a>=1','https://x/pkg.whl','--index-url evil','a==1; os_name=="nt"','../local']:
   with self.assertRaises(ValueError):deps.requirements([bad])
  self.assertEqual(deps.requirements(['rich==14.3.3']),{'rich':'14.3.3'})
 def test_bad_install_does_not_register_environment(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp)/'root';root.mkdir();home=Path(tmp)/'home';(root/'subject.py').write_text('X=1')
   with patch.object(lab,'ROOT',root):
    ident=lab.start(home,'deps')['id']
    with patch.object(deps,'command',side_effect=ValueError('download failed')):
     with self.assertRaises(ValueError):deps.prepare(home,ident,['rich==14.3.3'])
    self.assertNotIn('dependencies',lab.load(home,ident)[1]);self.assertFalse((home/'runtime-environment.json').exists())

if __name__=='__main__':unittest.main()
