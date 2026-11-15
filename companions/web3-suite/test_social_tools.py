import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from datetime import datetime,timezone,timedelta
from unittest.mock import patch,Mock
import social_tools as s

class SocialTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)
        self.agent=SimpleNamespace(mode='auto',interactive=True,depth=0,browser=Mock())
    def draft(self): return s.save(self.home,'Uno\n---\nDue','@alice')
    def test_registered_and_no_external_service(self):
        import freellmapi,tool_routing
        names={t['function']['name'] for t in freellmapi.TOOLS}
        self.assertTrue(s.NAMES<=names)
        self.assertFalse(any(n.startswith('typefully_') for n in names))
        self.assertIn('social_save_draft',tool_routing.suggested('Prepara un thread su X'))
        self.assertFalse(s.status(self.home)['x']['api_key_required'])
    def test_persist_and_dedup(self):
        a=self.draft();b=self.draft()
        self.assertEqual(a['id'],b['id']);self.assertEqual(len(s.drafts(self.home)),1)
        self.assertEqual(s.drafts(self.home,a['id'])['text'],'Uno\n---\nDue')
    def test_invalid_content(self):
        for text,account in [('', 'alice'),('a','../x')]:
            with self.assertRaises(ValueError): s.save(self.home,text,account)
    def test_schedule_timezone(self):
        d=self.draft()
        for date in ['tomorrow','2099-01-01T10:00:00','2000-01-01T10:00:00Z']:
            with self.assertRaises(ValueError): s.schedule(self.home,d['id'],date)
    def test_schedule_denied(self):
        with patch.object(s,'confirm',return_value=False):
            self.assertTrue(s.schedule(self.home,self.draft()['id'],'2099-01-01T10:00:00Z')['denied'])
    def test_account_changed_during_confirmation(self):
        draft=self.draft()
        def change(*args):
            s.save(self.home,draft['text'],'bob',draft['id']); return True
        with patch.object(s,'confirm',side_effect=change):
            with self.assertRaisesRegex(ValueError,'modificata'):
                s.schedule(self.home,draft['id'],'2099-01-01T10:00:00Z')
        self.assertEqual(s.drafts(self.home,draft['id'])['state'],'draft')
    def due(self,seconds=10):
        d=self.draft()
        with s.database(self.home) as db:
            db.execute("UPDATE drafts SET state='scheduled',due=? WHERE id=?",((datetime.now(timezone.utc)-timedelta(seconds=seconds)).isoformat(),d['id']))
        return d
    def test_due_once_and_persistent(self):
        d=self.due();self.assertEqual(s.poll(self.home),'/_social '+d['id']);self.assertIsNone(s.poll(self.home))
    def test_missed_not_sent(self):
        d=self.due(400);self.assertIsNone(s.poll(self.home));self.assertEqual(s.drafts(self.home,d['id'])['state'],'missed')
    def test_cancel(self):
        d=self.due();self.assertTrue(s.dispatch(self.agent,'social_cancel',{'draft_id':d['id']},self.home)['cancelled']);self.assertIsNone(s.poll(self.home))
    def test_edit_schedule_blocked(self):
        d=self.due();self.assertTrue(s.save(self.home,'changed','alice',d['id'])['duplicate_prevented'])
    def test_no_submit_without_confirm(self):
        with patch.object(s,'confirm',return_value=False),patch('x_publisher.prepare') as prep:
            self.assertTrue(s.publish(self.agent,self.home,self.draft()['id'])['denied']);prep.assert_not_called()
    def test_uncertain_not_replayed(self):
        d=self.draft()
        with patch.object(s,'confirm',return_value=True),patch('x_publisher.prepare',return_value=(Mock(),Mock())),patch('x_publisher.submit',side_effect=TimeoutError) as send:
            self.assertEqual(s.publish(self.agent,self.home,d['id'])['state'],'uncertain')
            self.assertTrue(s.publish(self.agent,self.home,d['id'])['duplicate_prevented']);self.assertEqual(send.call_count,1)
    def test_prepare_failure_no_click(self):
        with patch.object(s,'confirm',return_value=True),patch('x_publisher.prepare',side_effect=ValueError('login')),patch('x_publisher.submit') as send:
            self.assertEqual(s.publish(self.agent,self.home,self.draft()['id'])['state'],'blocked');send.assert_not_called()
    def test_plan_cannot_publish(self):
        self.agent.mode='plan'
        with patch('x_publisher.prepare') as prep:
            self.assertTrue(s.publish(self.agent,self.home,self.draft()['id'])['denied']);prep.assert_not_called()
    def test_submitted_link_stored(self):
        d=self.due();s.poll(self.home)
        with patch.object(s,'confirm') as confirm,patch('x_publisher.prepare',return_value=(Mock(),Mock())),patch('x_publisher.submit',return_value={'state':'submitted','url':'https://x.com/alice/status/1'}):
            self.assertEqual(s.publish(self.agent,self.home,d['id'],scheduled=True)['state'],'submitted');confirm.assert_not_called()
        self.assertIn('/status/1',s.drafts(self.home,d['id'])['result'])
    def test_manual_resolution_does_not_send(self):
        d=self.draft()
        with s.database(self.home) as db: db.execute("UPDATE drafts SET state='uncertain' WHERE id=?",(d['id'],))
        with patch('questions.ask',return_value={'answer':'Post assente: sblocca bozza'}):
            s.command('/social resolve '+d['id'],self.home,lambda _:None)
        self.assertEqual(s.drafts(self.home,d['id'])['state'],'draft')

if __name__=='__main__': unittest.main()
