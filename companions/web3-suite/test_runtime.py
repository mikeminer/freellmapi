import io,json,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
from urllib.error import HTTPError
from api_stream import consume,StreamInterrupted
from freellmapi import Agent

def sse(chunks,done=True):
 return io.BytesIO((''.join('data: '+json.dumps(c)+'\n\n' for c in chunks)+('data: [DONE]\n\n' if done else '')).encode())

class RuntimeTests(unittest.TestCase):
 def test_stream_fragments(self):
  chunks=[{'model':'actual','choices':[{'delta':{'content':'ciao ','tool_calls':[{'index':0,'id':'t1','function':{'name':'read_file','arguments':'{"pa'}}]}}]},
          {'choices':[{'delta':{'content':'mondo','tool_calls':[{'index':0,'function':{'arguments':'th":"x"}'}}]},'finish_reason':'tool_calls'}]},
          {'usage':{'total_tokens':12},'choices':[]}]
  text=[]; result=consume(sse(chunks),text.append)
  self.assertEqual(''.join(text),'ciao mondo')
  self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['function']['arguments'],'{"path":"x"}')
  self.assertEqual(result['usage']['total_tokens'],12)
 def test_stream_broken(self):
  with self.assertRaises(StreamInterrupted) as error: consume(sse([{'choices':[{'delta':{'content':'partial'}}]}],False),lambda _:None)
  self.assertEqual(error.exception.partial,'partial')
 def test_partial_does_not_execute_tools(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'s.json'
   with patch.object(a,'api',side_effect=StreamInterrupted('interrupted','partial')),patch.object(a,'tool') as tool:
    a.turn('do work'); tool.assert_not_called()
   self.assertIn('partial',a.messages[-1]['content'])
 def test_http_retry_then_success(self):
  a=Agent('.','http://localhost','test','auto')
  response=io.BytesIO(b'{"data":[]}'); response.headers={}
  error=HTTPError('http://localhost',503,'unavailable',{},io.BytesIO(b'error'))
  with patch('urllib.request.build_opener') as make,patch('time.sleep'):
   make.return_value.open.side_effect=[error,response]
   self.assertEqual(a.api('models'),{'data':[]})
   self.assertEqual(make.return_value.open.call_count,2)
 def test_auth_no_retry(self):
  a=Agent('.','http://localhost','test','auto')
  with patch('urllib.request.build_opener') as make:
   make.return_value.open.side_effect=HTTPError('http://localhost',401,'auth',{},io.BytesIO(b'bad'))
   with self.assertRaises(RuntimeError): a.api('models')
   self.assertEqual(make.return_value.open.call_count,1)
 def test_status_is_state(self):
  a=Agent('.','http://localhost','test','auto')
  self.assertIn('MCP: 0/0',a.status()); self.assertIn('browser: chiuso',a.status())
 def test_retry_keeps_tool_results(self):
  with tempfile.TemporaryDirectory() as d:
   a=Agent(d,'http://localhost','test','auto'); a.session=Path(d)/'s.json'
   a.context_limit=131072
   a.messages=[{'role':'user','content':'task'},{'role':'assistant','tool_calls':[{'id':'1'}]}, {'role':'tool','tool_call_id':'1','content':'already done'}]
   result={'choices':[{'message':{'role':'assistant','content':'finished'}}]}
   with patch.object(a,'api',return_value=result),patch.object(a,'tool') as tool:
    a.turn('task',retry=True); tool.assert_not_called()
   self.assertEqual(sum(m['role']=='user' for m in a.messages),1)
   self.assertEqual(a.messages[2]['content'],'already done')
 def test_browser_action_denied(self):
  a=Agent('.','http://localhost','test','auto')
  with patch.object(a,'approve',return_value=False),patch.object(a.browser,'click') as click:
   self.assertIn('negata',a.tool('browser_click',{'ref':'e1'})); click.assert_not_called()

if __name__=='__main__': unittest.main()
