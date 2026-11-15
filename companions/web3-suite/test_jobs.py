import tempfile
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent

class JobTests(unittest.TestCase):
 def parent(self,d):
  a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'main.json'; return a
 def test_parallel_workers(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.parent(d); barrier=threading.Barrier(3)
   def work(child,task):
    barrier.wait(timeout=5); child.messages=[{'role':'assistant','content':task}]
   with patch.object(Agent,'turn',work):
    jobs=a.job_manager(); x=jobs.start('one'); y=jobs.start('two'); barrier.wait(timeout=5)
    jobs.jobs[x]['thread'].join(5); jobs.jobs[y]['thread'].join(5)
   self.assertEqual(jobs.jobs[x]['state'],'completed'); self.assertIn('two',jobs.result(y))
 def test_writes_queued_and_approved(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.parent(d)
   def work(child,task):
    child.messages=[{'role':'assistant','content':child.tool('write_file',{'path':task+'.txt','content':task})}]
   with patch.object(Agent,'turn',work),patch.object(a,'approve',return_value=True):
    jobs=a.job_manager(); x=jobs.start('one'); y=jobs.start('two')
    deadline=time.time()+5
    while jobs.pending.qsize()<2 and time.time()<deadline: time.sleep(.01)
    self.assertFalse((Path(d)/'one.txt').exists())
    jobs.wait(); jobs.jobs[x]['thread'].join(3); jobs.jobs[y]['thread'].join(3)
   self.assertEqual((Path(d)/'one.txt').read_text(),'one')
   self.assertEqual((Path(d)/'two.txt').read_text(),'two')
 def test_cancel_waiting_prevents_write(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.parent(d)
   def work(child,task): child.tool('write_file',{'path':'cancel.txt','content':'no'})
   with patch.object(Agent,'turn',work):
    jobs=a.job_manager(); ident=jobs.start('cancel')
    deadline=time.time()+5
    while jobs.pending.empty() and time.time()<deadline: time.sleep(.01)
    jobs.cancel(ident); jobs.service(); jobs.jobs[ident]['thread'].join(3)
   self.assertFalse((Path(d)/'cancel.txt').exists())
 def test_shell_and_mcp(self):
  with tempfile.TemporaryDirectory() as d:
   a=self.parent(d)
   class Client:
    def request(self,method,args): return {'content':[{'type':'text','text':'MCP OK'}]}
   a.extensions.routes={'mcp_test':(Client(),'echo')}
   def work(child,task):
    shell=child.tool('shell',{'command':'Write-Output "background OK"'})
    mcp=child.tool('mcp_test',{'text':'test'})
    child.messages=[{'role':'assistant','content':shell+'\n'+mcp}]
   with patch.object(Agent,'turn',work),patch.object(a,'approve',return_value=True):
    jobs=a.job_manager(); ident=jobs.start('tools'); jobs.wait(); jobs.jobs[ident]['thread'].join(3)
   self.assertIn('background OK',jobs.result(ident)); self.assertIn('MCP OK',jobs.result(ident))

if __name__=='__main__': unittest.main()
