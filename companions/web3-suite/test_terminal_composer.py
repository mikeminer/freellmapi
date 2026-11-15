import unittest
from prompt_toolkit.input import create_pipe_input
from prompt_toolkit.output import DummyOutput
from terminal_composer import create

class ComposerTests(unittest.TestCase):
 def run_keys(self,keys,mode='ask',on_mode=None):
  with create_pipe_input() as pipe:
   app,pre=create('auto',mode,'API: pronta',on_mode,input=pipe,output=DummyOutput())
   pipe.send_text(keys)
   return app.run(pre_run=pre)
 def test_enter_submits_text(self): self.assertEqual(self.run_keys('ciao\r'),'ciao')
 def test_shift_tab_changes_real_mode_preserving_draft(self):
  changes=[]
  self.assertEqual(self.run_keys('bozza\x1b[Z\x1b[Z\r',on_mode=changes.append),'bozza')
  self.assertEqual(changes,['plan','auto'])
 def test_shortcuts_preserve_draft(self): self.assertEqual(self.run_keys('test\x18\x18\r'),'test')
 def test_cancel_does_not_submit(self):
  with self.assertRaises(KeyboardInterrupt): self.run_keys('non inviare\x03')
 def test_eof_empty_exits(self):
  with self.assertRaises(EOFError): self.run_keys('\x04')
 def test_bracketed_paste_keeps_all_lines(self):
  self.assertEqual(self.run_keys('\x1b[200~prima\nseconda\x1b[201~\r'),'prima\nseconda')
