import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from extensions import MCP, Extensions
from freellmapi import Agent
import freellmapi

SERVER = '''import json,sys
for line in sys.stdin:
 m=json.loads(line)
 if 'id' not in m: continue
 method=m['method']
 if method=='initialize': r={'protocolVersion':'2025-11-25','capabilities':{'tools':{}},'serverInfo':{'name':'test','version':'1'}}
 elif method=='tools/list': r={'tools':[{'name':'echo','description':'echo','inputSchema':{'type':'object','properties':{'text':{'type':'string'}}}}]}
 elif method=='tools/call': r={'content':[{'type':'text','text':m['params']['arguments']['text']}]}
 else: r={}
 print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':r}),flush=True)
'''

class ExtensionTests(unittest.TestCase):
 def test_missing_config_bootstrap(self):
  with tempfile.TemporaryDirectory() as d:
   with patch.object(freellmapi,'HOME',Path(d)), patch.object(sys,'argv',['freellmapi','-Check']), patch.object(freellmapi,'stored_token',return_value='test'), patch.dict('os.environ',{'FREELLMAPI_SESSION_TOKEN':'test'}), patch.object(Agent,'api',return_value={'data':[]}):
    freellmapi.main()
   self.assertEqual(json.loads((Path(d)/'config.json').read_text())['base_url'],'http://127.0.0.1:31415')
 def test_mcp_real_process(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'server.py'; p.write_text(SERVER)
   m=MCP({'command':sys.executable,'args':[str(p)]}, d)
   try:
    self.assertEqual(m.tools()[0]['name'],'echo')
    self.assertEqual(m.request('tools/call',{'name':'echo','arguments':{'text':'hello'}})['content'][0]['text'],'hello')
   finally: m.close()
   self.assertIsNotNone(m.process.poll())
 def test_compact_keeps_tool_pairs_and_archive(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'session.json'
   recent=[{'role':'user','content':'latest'}, {'role':'assistant','content':None,'tool_calls':[{'id':'1'}]}, {'role':'tool','tool_call_id':'1','content':'result'}]
   a.messages=[{'role':'user','content':'old'}, {'role':'assistant','content':'answer'}]+recent
   with patch.object(a,'api',return_value={'choices':[{'message':{'content':'summary'}}]}): a.compact(True)
   self.assertEqual(a.messages[1:],recent)
   self.assertEqual(len(list((Path(d)/'archives').glob('*.json'))),1)
 def test_compact_failure_preserves_history(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.messages=[{'role':'user','content':str(i)} for i in range(4)]
   original=list(a.messages)
   with patch.object(a,'api',side_effect=RuntimeError('offline')):
    with self.assertRaises(RuntimeError): a.compact(True)
   self.assertEqual(a.messages,original)
 def test_subagent_isolated_readonly(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'parent.json'; children=[]
   def run(child, task): children.append(child); child.messages=[{'role':'assistant','content':'report'}]
   with patch.object(Agent,'turn',run): self.assertEqual(a.tool('delegate',{'task':'inspect'}),'report')
   child=children[0]
   self.assertEqual(child.mode,'plan'); self.assertFalse(child.interactive)
   self.assertNotEqual(child.session,a.session)
   self.assertNotIn('delegate',[x['function']['name'] for x in child.tool_definitions()])
 def test_plugin_command(self):
  with tempfile.TemporaryDirectory() as d:
   p=Path(d)/'plugin'; p.mkdir()
   (p/'plugin.json').write_text(json.dumps({'name':'review','instructions':'Review carefully','commands':{'review':'Review $ARGUMENTS'}}))
   (Path(d)/'extensions.json').write_text(json.dumps({'plugins':[str(p)]}))
   e=Extensions(d); a=Agent(d,'http://localhost','test','auto')
   with patch.object(a,'approve',return_value=True): e.load(a)
   self.assertEqual(e.commands['review'],'Review $ARGUMENTS')
   self.assertEqual(e.plugins,['review'])

if __name__=='__main__': unittest.main()
