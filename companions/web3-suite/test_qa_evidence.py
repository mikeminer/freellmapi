import hashlib
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from qa_evidence import validate


class EvidenceTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root=Path(self.tmp.name); self.e=self.root/'e.json'; self.e.write_text(json.dumps({'tests':[{'id':'test_one','status':'PASS'}]}))
        self.report={'scenarios':[{'id':'one','status':'PASS','test_ids':['test_one'],'evidence':{'path':'e.json','sha256':hashlib.sha256(self.e.read_bytes()).hexdigest()}}],
                     'summary':{'counts':{'PASS':1,'FAIL':0,'NON_VERIFICATO':0}}}
    def check(self):
        path=self.root/'RESULTS.json'; path.write_text(json.dumps(self.report)); return validate(path)
    def test_verified_pass(self): self.assertTrue(self.check()['ok'])
    def test_changed_evidence_rejected(self):
        self.e.write_text('{}'); self.assertFalse(self.check()['ok'])
    def test_duplicate_tests_not_counted_twice(self):
        self.report['scenarios'][0]['test_ids']*=2; self.assertFalse(self.check()['ok'])
    def test_missing_evidence_rejected(self):
        self.e.unlink(); self.assertFalse(self.check()['ok'])
    def test_pending_not_completed(self):
        self.report['scenarios'][0]['status']='PENDING'; self.assertFalse(self.check()['ok'])
    def test_failed_record_cannot_pass(self):
        self.e.write_text(json.dumps({'tests':[{'id':'test_one','status':'FAIL'}]}))
        self.report['scenarios'][0]['evidence']['sha256']=hashlib.sha256(self.e.read_bytes()).hexdigest()
        self.assertFalse(self.check()['ok'])
        self.report['scenarios'][0].update(status='NON_VERIFICATO',reason='Non so')
        self.assertFalse(self.check()['ok'])

    def test_agent_does_not_publish_unverified_success(self):
        from freellmapi import Agent
        agent=Agent(self.root,'http://localhost','test','auto'); agent.mode='auto'; agent.session=self.root/'s.json'
        call={'id':'report','function':{'name':'write_file','arguments':json.dumps({'path':'qa_runs/run/RESULTS.json','content':json.dumps({'scenarios':[{'id':'browser','status':'PASS'}]})})}}
        initial={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[call]}}]}
        final={'choices':[{'message':{'role':'assistant','content':'Tutti i test superati!'}}]}
        with patch.object(agent,'api',side_effect=[initial,final,final,final]),patch('freellmapi.output') as output:
            agent.turn('Scrivi il rapporto dei test')
        self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertFalse(any('Tutti i test superati!' in str(c) for c in output.call_args_list))
