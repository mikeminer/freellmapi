import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from memory_vault import Vault
from freellmapi import Agent,attach_vault

class MemoryTests(unittest.TestCase):
 def test_questionnaire_answer_is_archived(self):
  import json
  with tempfile.TemporaryDirectory() as d:
   v=Vault(Path(d)/'vault',d)
   messages=[{'role':'assistant','tool_calls':[{'id':'q1','function':{'name':'ask_user'}}]},
             {'role':'tool','tool_call_id':'q1','content':json.dumps({'question':'Piattaforma?','answer':'Browser','cancelled':False})}]
   v.record(messages,'session')
   self.assertIn('Browser',v.search('Piattaforma'))
 def test_cross_model_restart(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','model-a'); a.session=Path(d)/'session.json'
   attach_vault(a,Path(d)/'vault')
   a.memory.remember('Preferisco risposte in italiano.')
   a.messages=[{'role':'user','content':'Il progetto Polare utilizza SQLite.'}]; a.save()
   b=Agent(d,'http://localhost','test','model-b'); b.session=a.session
   attach_vault(b,Path(d)/'vault')
   self.assertIn('italiano',b.system())
   self.assertIn('SQLite',b.memory.search('Polare'))
 def test_record_preserves_and_deduplicates(self):
  with tempfile.TemporaryDirectory() as d:
   v=Vault(Path(d)/'vault',d)
   old=[{'role':'user','content':'original fact'}]
   v.record(old,'session'); v.record(old,'session')
   self.assertEqual(len(list((v.path/'Conversations').glob('*.md'))),1)
   v.record([{'role':'user','content':'summary'}],'session')
   self.assertIn('original fact',v.search('original'))
 def test_edit_reload_and_forget(self):
  with tempfile.TemporaryDirectory() as d:
   v=Vault(Path(d)/'vault',d); name=v.remember('Old preference')
   v.note_path(name).write_text('New preference',encoding='utf-8')
   self.assertIn('New preference',v.context('preference'))
   v.forget(name); self.assertFalse(v.note_path(name).exists())
   with self.assertRaises(ValueError): v.read('../secret.md')
 def test_existing_note_denied(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'none.json'; attach_vault(a,Path(d)/'vault')
   p=a.vault_root/'existing.md'; p.write_text('original')
   with patch.object(a,'approve',return_value=False): a.tool('vault_write',{'path':'existing.md','content':'changed'})
   self.assertEqual(p.read_text(),'original')

if __name__=='__main__': unittest.main()
