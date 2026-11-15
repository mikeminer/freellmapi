import json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from continuation import instruction_index
from evolution_tools import normalize_arguments
from loop_guard import LoopGuard,LoopDetected
import skill_router,operating_rules,release_transaction,repair_lab

class LiveRunRegressions(unittest.TestCase):
 def test_object_arguments_normalized_without_dropping_unknown_settings(self):
  value=normalize_arguments('strategy_propose',{'settings_json':{'mode':'stepwise'}})
  self.assertEqual(json.loads(value['settings_json']),{'mode':'stepwise'})
  with self.assertRaises(ValueError): normalize_arguments('strategy_propose',{'settings_json':{'description':'invented'}})
 def test_invalid_json_is_actionable(self):
  with self.assertRaisesRegex(ValueError,'JSON valido'):
   normalize_arguments('strategy_propose',{'settings_json':'case_study.json'})
 def test_mixed_errors_do_not_reset_failure_limit(self):
  guard=LoopGuard()
  for n in range(3): guard.observe('strategy_propose',{},'Errore: category'+str(n),True)
  guard.observe('strategy_status',{},'{}')
  with self.assertRaises(LoopDetected): guard.observe('strategy_propose',{},'Errore: different',True)
 def test_continue_preserves_instruction_boundary_and_skips_skills(self):
  messages=[{'role':'user','content':'Test all six phases. Do not search skills.'},{'role':'assistant','content':'phase one'},{'role':'user','content':'procedi'}]
  self.assertEqual(instruction_index(messages),0)
  with patch('skill_router.catalog',side_effect=AssertionError('unexpected skill search')):
   skill_router.Router().run(Mock(),'procedi',Mock())
 def test_invalid_cases_fail_before_api(self):
  with tempfile.TemporaryDirectory() as tmp:
   agent=Mock();agent.root=Path(tmp)
   version=operating_rules.propose(tmp,agent.root,{'mode':'concise'})
   with self.assertRaisesRegex(ValueError,'prompt, expected'):
    operating_rules.evaluate(agent,tmp,version['id'],'auto',[{'expected_model':'auto'}]*4)
   agent.api.assert_not_called()
 def test_rollback_does_not_claim_success_when_replace_silently_fails(self):
  with tempfile.TemporaryDirectory() as tmp:
   folder=Path(tmp);target=folder/'module.py';target.write_text('after');backup=folder/'backup';backup.write_text('before')
   record={'state':'activating','transaction':[{'target':str(target),'before':repair_lab.digest(backup),'after':repair_lab.digest(target),'backup':'backup'}]}
   with patch.object(repair_lab,'replace',return_value=None),self.assertRaisesRegex(ValueError,'non verificato'):
    release_transaction.restore(folder,record)
   self.assertEqual(record['state'],'recovery_conflict')
 def test_interrupted_evaluation_resumes_without_replaying_completed_call(self):
  with tempfile.TemporaryDirectory() as tmp:
   agent=Mock();agent.root=Path(tmp);agent.last_request={}
   version=operating_rules.propose(tmp,agent.root,{'mode':'concise'})
   cases=[{'prompt':str(i),'expected':1} for i in range(3)]
   response={'model':'real-route','choices':[{'message':{'content':'1'},'finish_reason':'stop'}],'usage':{'total_tokens':10}}
   agent.api.side_effect=[response,RuntimeError('provider down')]
   with self.assertRaisesRegex(RuntimeError,'provider down'): operating_rules.evaluate(agent,tmp,version['id'],'model',cases)
   saved=operating_rules.read(tmp,agent.root,version['id'])
   self.assertIn('baseline',saved['evaluation_progress']['pairs'][0])
   agent.api.reset_mock();agent.api.side_effect=None;agent.api.return_value=response
   result=operating_rules.evaluate(agent,tmp,version['id'],'model',cases)
   self.assertEqual(agent.api.call_count,5)
   self.assertEqual(len(result['evidence']['pairs']),3)
