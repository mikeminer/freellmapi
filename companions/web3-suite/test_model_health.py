import io,json,time,unittest
from unittest.mock import patch
from urllib.error import HTTPError
from freellmapi import Agent
from model_health import RateLimited,cooldown
from terminal_ui import ModelPicker

class HealthTests(unittest.TestCase):
 def test_long_limit_once_and_cached(self):
  a=Agent('.','http://localhost','test','chat-model')
  error=HTTPError('http://localhost',429,'limited',{'Retry-After':'86400'},io.BytesIO(json.dumps({'error':{'message':'reset ~24h','retryAtMs':(time.time()+86400)*1000}}).encode()))
  with patch('urllib.request.build_opener') as build,patch('time.sleep') as sleep:
   build.return_value.open.side_effect=error
   for _ in range(2):
    with self.assertRaises(RateLimited): a.api('chat/completions',{'model':a.model})
   self.assertEqual(build.return_value.open.call_count,1); sleep.assert_not_called()
 def test_short_limit_respects_wait(self):
  a=Agent('.','http://localhost','test','chat-model')
  error=HTTPError('http://localhost',429,'limited',{'Retry-After':'3'},io.BytesIO(b'{}'))
  response=io.BytesIO(b'{}'); response.headers={}
  with patch('urllib.request.build_opener') as build,patch('time.sleep') as sleep:
   build.return_value.open.side_effect=[error,response]
   a.api('chat/completions',{'model':a.model}); sleep.assert_called_once_with(3)
 def test_models_filtered(self):
  picker=ModelPicker([{'id':'voyage-4free','available':True},{'id':'chat','available':True,'execution_status':'exhausted'}],'auto')
  self.assertNotIn('voyage-4free',[m['id'] for m in picker.entries])
  self.assertNotIn('chat',[m['id'] for m in picker.matches()])
  picker.key('TAB'); self.assertIn('chat',[m['id'] for m in picker.matches()])
 def test_capability_question_skips_router(self):
  a=Agent('.','http://localhost','test','auto')
  with patch.object(a,'api') as api:
   a.skill_router.run(a,'hello what you can do?',lambda _:None); api.assert_not_called()
 def test_model_guard_before_network(self):
  a=Agent('.','http://localhost','test','voyage-4free')
  with patch.object(a,'_api') as api:
   with self.assertRaises(RuntimeError): a.api('chat/completions',{'model':a.model})
   api.assert_not_called()

if __name__=='__main__': unittest.main()
