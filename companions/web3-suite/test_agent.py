import tempfile, unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent
class Tests(unittest.TestCase):
 def test_tools(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto')
   with self.assertRaises(ValueError): a.path('../outside')
   with patch('builtins.input',return_value='n'): a.tool('write_file',{'path':'x','content':'ok'})
   self.assertFalse((Path(d)/'x').exists())
   with patch('builtins.input',return_value='s'): a.tool('write_file',{'path':'x','content':'ok'})
   self.assertEqual((Path(d)/'x').read_text(),'ok')
   self.assertIn('ok',a.tool('read_file',{'path':'x'}))
   a.mode='plan'
   with patch('subprocess.Popen') as p:
    a.tool('shell',{'command':'exit 0'})
    p.assert_not_called()
 def test_roundtrip(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'session.json'; a.context_limit=131072
   replies=[{'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'1','type':'function','function':{'name':'list_files','arguments':'{}'}}]}}]},{'choices':[{'message':{'role':'assistant','content':'done'}}]}]
   with patch.object(a,'api',side_effect=replies) as api:
    a.turn('list'); self.assertEqual(api.call_count,2)
   self.assertEqual(a.messages[2]['tool_call_id'],'1')
if __name__=='__main__': unittest.main()
