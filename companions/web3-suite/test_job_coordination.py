import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent


class CoordinationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.a=Agent(self.tmp.name,'http://localhost','test','auto'); self.a.session=Path(self.tmp.name)/'s.json'
        self.jobs=self.a.job_manager()
        self.release=threading.Event()
        self.addCleanup(self.cleanup)

    def cleanup(self):
        self.release.set(); self.jobs.close()
        for j in self.jobs.jobs.values(): j['thread'].join(3)

    def test_adb_reads_never_wait_for_foreground(self):
        def work(child,task):
            for name in ('seeker_devices','seeker_packages','seeker_logs','seeker_crashes'):
                child.tool(name,{})
        with patch.object(Agent,'turn',work),patch('freellmapi.seeker_dispatch',return_value='read OK') as read:
            ident=self.jobs.start('Read Seeker diagnostics'); self.jobs.jobs[ident]['thread'].join(3)
        self.assertEqual(read.call_count,4)
        self.assertTrue(self.jobs.pending.empty())
        self.assertEqual(self.jobs.jobs[ident]['state'],'completed')

    def test_wait_services_write_using_real_approval(self):
        def work(child,task): child.tool('write_file',{'path':'app.txt','content':'built'})
        with patch.object(Agent,'turn',work),patch.object(self.a,'approve',return_value=True) as approve:
            ident=self.jobs.start('Write file')
            result=self.jobs.wait_for(ident,3)
            self.jobs.jobs[ident]['thread'].join(3)
        approve.assert_called_once(); self.assertIn('completed',result)
        self.assertEqual((Path(self.tmp.name)/'app.txt').read_text(),'built')

    def test_duplicate_reuses_and_model_cancel_is_blocked(self):
        def work(child,task): self.release.wait(3)
        with patch.object(Agent,'turn',work):
            ident=self.jobs.start('Installa APK sul Seeker')
            duplicate=self.jobs.start('installa apk sul seeker!')
            self.assertEqual(ident,duplicate)
            with self.assertRaisesRegex(ValueError,'agent_wait'): self.jobs.cancel(ident,by_model=True)
            self.assertFalse(self.jobs.jobs[ident]['cancel'].is_set())
            self.jobs.cancel(ident)
            with self.assertRaisesRegex(ValueError,'già annullato'): self.jobs.start('Installa APK sul Seeker')
            self.release.set(); self.jobs.jobs[ident]['thread'].join(3)
            new=self.jobs.start('Installa APK sul Seeker',allow_restart=True)
            self.jobs.jobs[new]['thread'].join(3)
            self.assertNotEqual(new,ident)

    def test_status_does_not_repeat_whole_list(self):
        with patch.object(Agent,'turn',lambda child,task:self.release.wait(3)):
            self.jobs.start('Task')
            # Wait for worker to reach its patched turn so status is stable.
            first=self.jobs.report(compact=True)
            second=self.jobs.report(compact=True)
        self.assertIn('running',first); self.assertIn('Nessun cambiamento',second)

    def test_completed_result_returned_once_to_model(self):
        def work(child,task): child.messages=[{'role':'assistant','content':'REPORT_SENTINEL'}]
        with patch.object(Agent,'turn',work):
            ident=self.jobs.start('Task'); self.jobs.jobs[ident]['thread'].join(3)
        self.assertIn('REPORT_SENTINEL',self.jobs.result(ident,compact=True))
        self.assertNotIn('REPORT_SENTINEL',self.jobs.result(ident,compact=True))
        self.assertIn('REPORT_SENTINEL',self.jobs.result(ident))

    def test_wait_is_bounded_and_does_not_cancel(self):
        with patch.object(Agent,'turn',lambda child,task:self.release.wait(3)):
            ident=self.jobs.start('Task')
            started=time.monotonic(); result=self.jobs.wait_for(ident,.05)
        self.assertLess(time.monotonic()-started,1)
        self.assertIn('running',result); self.assertFalse(self.jobs.jobs[ident]['cancel'].is_set())


if __name__=='__main__': unittest.main()
