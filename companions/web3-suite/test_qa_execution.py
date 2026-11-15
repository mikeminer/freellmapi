import hashlib,json,tempfile,unittest
from pathlib import Path
from autonomy import Budget,BudgetExceeded
from qa_execution import timeout_for
from qa_evidence import validate
from qa_audit_runner import EvidenceResult
import io
from unittest.mock import Mock,patch

class AuditRecoveryTests(unittest.TestCase):
 def test_explicit_no_skills_is_respected(self):
  import skill_router
  with patch('skill_router.catalog',side_effect=AssertionError('unexpected discovery')):
   skill_router.Router().run(Mock(),'Esegui test. Non cercare o installare skill.',Mock())
 def test_long_suite_default_preserves_explicit_timeout(self):
  self.assertEqual(timeout_for('python Documents/freellmapi/qa_audit_runner.py --output x'),600)
  self.assertEqual(timeout_for('python qa_audit_runner.py --output x',120),120)
  self.assertEqual(timeout_for('python app.py'),60)
 def test_output_directory_change_cannot_evade_limit(self):
  budget=Budget(repeats=2)
  budget.action('shell',{'command':'python qa_audit_runner.py --output "one"'})
  budget.action('shell',{'command':'python qa_audit_runner.py --output two'})
  with self.assertRaises(BudgetExceeded): budget.action('shell',{'command':'python qa_audit_runner.py --output three'})
 def test_partial_evidence_survives_before_suite_end(self):
  with tempfile.TemporaryDirectory() as tmp:
   result=EvidenceResult(io.StringIO(),True,0);result.progress_path=Path(tmp)/'progress.json'
   case=unittest.FunctionTestCase(lambda:None);result.startTest(case);result.addSuccess(case)
   data=json.loads(result.progress_path.read_text());self.assertFalse(data['complete']);self.assertEqual(data['tests'][0]['status'],'PASS')
 def test_live_report_rejects_unsupported_pass_and_fake_zero(self):
  with tempfile.TemporaryDirectory() as tmp:
   p=Path(tmp)/'results.json';p.write_text(json.dumps({'status':'COMPLETED','api_requests':0,'phases':{'test':{'status':'PASS','notes':'trust me'}}}))
   result=validate(p);self.assertFalse(result['ok']);self.assertTrue(any('api_requests' in e for e in result['errors']))
 def test_live_report_accepts_hashed_evidence_and_explicit_unknown(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);e=root/'e.json';e.write_text('{"exit_code":0,"observed":true,"origin":"local_process","ok":true}')
   p=root/'results.json';p.write_text(json.dumps({'status':'COMPLETED','api_requests':None,'phases':{'local':{'status':'PASS','mode':'local','evidence':{'path':'e.json','sha256':hashlib.sha256(e.read_bytes()).hexdigest()}},'live':{'status':'NOT_VERIFIED','reason':'Provider unavailable'}}}))
   self.assertTrue(validate(p)['ok'])
 def test_hash_alone_does_not_prove_content(self):
  with tempfile.TemporaryDirectory() as tmp:
   root=Path(tmp);e=root/'e.json';e.write_text('{"exit_code":0}')
   p=root/'results.json';p.write_text(json.dumps({'status':'COMPLETED','api_requests':None,'phases':{'local':{'status':'PASS','mode':'local','evidence':{'path':'e.json','sha256':hashlib.sha256(e.read_bytes()).hexdigest()}}}}))
   result=validate(p);self.assertFalse(result['ok']);self.assertTrue(any('hash' in error.lower() or 'osservate' in error.lower() for error in result['errors']))
