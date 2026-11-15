import json
import tempfile
import unittest
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor
from memory_vault import Vault
from project_context import verification_report


class ContextTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.addCleanup(self.temp.cleanup)
        self.v=Vault(Path(self.temp.name)/'vault',self.temp.name); self.c=self.v.operational

    def ingest(self,tool='shell',result='Exit code: 0\n3 tests passed'):
        self.c.ingest([{'role':'user','content':'Build my game'},
            {'role':'assistant','tool_calls':[{'id':'t','function':{'name':tool,'arguments':'{}'}}]},
            {'role':'tool','tool_call_id':'t','content':result}], 'session')
        return list(self.c.load()['events'])

    def test_evidence_dedup_and_request_not_verified(self):
        ids=self.ingest(); self.ingest()
        self.assertEqual(len(self.c.load()['events']),2)
        with self.assertRaises(ValueError): self.c.checkpoint('verified','Done',[ids[0]])
        ident=self.c.checkpoint('verified','Command exited 0',[ids[1]])
        self.assertEqual(self.c.load()['claims'][0]['id'],ident)
        self.assertIn('interpretation',self.c.load()['claims'][0]['authority'])

    def test_failed_result_not_verification(self):
        ids=self.ingest(result='Exit code: 1\nfailed')
        with self.assertRaises(ValueError): self.c.checkpoint('verified','passed',[ids[-1]])

    def test_correction_preserves_history(self):
        old=self.c.checkpoint('state','old',user=True)
        new=self.c.checkpoint('state','new',replaces=old,user=True)
        self.assertEqual(self.c.load()['claims'][0]['superseded_by'],new)
        state=(self.c.folder/'State.md').read_text(encoding='utf-8')
        self.assertIn('new',state); self.assertNotIn('[state] old',state)

    def test_exclusion_and_source_links(self):
        ids=self.ingest(); self.c.checkpoint('state','tested',[ids[1]])
        path=self.c.load()['events'][ids[1]]['path']
        self.assertIn(path[:-3],(self.c.folder/'State.md').read_text(encoding='utf-8'))
        self.c.exclude(path)
        self.assertNotIn('[state] tested',self.c.snapshot('tested')['text'])
        with self.assertRaises(ValueError): self.c.checkpoint('state','test',[ids[1]])
        self.c.exclude('Profile/Me.md')
        self.assertNotIn('Profile/Me.md',[i['path'] for i in self.c.snapshot('')['items']])

    def test_archive_not_automatically_loaded_and_budget(self):
        self.v.record([{'role':'user','content':'SECRET_ARCHIVE detail'}],'old')
        snap=self.c.snapshot('SECRET_ARCHIVE',budget=6000)
        self.assertNotIn('SECRET_ARCHIVE detail',snap['text'])
        self.assertLessEqual(snap['chars'],6000)
        self.assertIn('SECRET_ARCHIVE',self.v.search('SECRET_ARCHIVE'))

    def test_handoff_reload_and_concurrent_workers(self):
        def worker(i): self.c.ingest([{'role':'user','content':f'task {i}'}],f'session{i}',f'subagent:{i}')
        with ThreadPoolExecutor(max_workers=3) as pool: list(pool.map(worker,range(12)))
        self.assertEqual(len(self.c.load()['events']),12)
        self.c.checkpoint('constraint','Use SQLite',user=True)
        reloaded=Vault(self.v.path,self.temp.name)
        self.assertIn('Use SQLite',reloaded.operational.handoff('continue'))

    def test_verification_does_not_claim_functional_success(self):
        report=verification_report([{'role':'user','content':'install'},
            {'role':'assistant','tool_calls':[{'id':'i','function':{'name':'seeker_install'}}]},
            {'role':'tool','tool_call_id':'i','content':'Success'}])
        self.assertIn('non certificazione',report)
        self.assertIn('seeker_install: Success',report)

    def test_unknown_source_rejected(self):
        with self.assertRaises(ValueError): self.c.checkpoint('state','made up',['missing'])


if __name__=='__main__': unittest.main()
