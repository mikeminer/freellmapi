import os
import re
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from shell_runner import run


@unittest.skipUnless(os.name=='nt','Windows shell integration')
class ShellRunnerTests(unittest.TestCase):
    def test_daemon_inherited_stdout_does_not_block_completion(self):
        with tempfile.TemporaryDirectory() as d:
            script=Path(d)/'spawn.py'
            script.write_text("import subprocess,sys\np=subprocess.Popen([sys.executable,'-c','import time;time.sleep(30)'],stdout=sys.stdout,stderr=sys.stderr)\nprint('CHILD_PID='+str(p.pid),flush=True)\n")
            started=time.monotonic()
            result=run(f'& python "{script}"',d,d,timeout=10)
            try:
                self.assertIn('Exit code: 0',result)
                self.assertLess(time.monotonic()-started,10)
            finally:
                pid=re.search(r'CHILD_PID=(\d+)',result)
                if pid: subprocess.run(['taskkill','/PID',pid[1],'/T','/F'],capture_output=True)

    def test_timeout_returns_without_deadlock(self):
        with tempfile.TemporaryDirectory() as d:
            started=time.monotonic()
            result=run('Start-Sleep -Seconds 30',d,d,timeout=1)
            self.assertIn('timeout (1s)',result)
            self.assertLess(time.monotonic()-started,12)


if __name__=='__main__': unittest.main()
