import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from freellmapi import Agent
from api_stream import StreamInterrupted,consume
from model_health import fallback_candidates


class FailoverTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.agent=Agent(self.tmp.name,'http://localhost','test','auto')
        self.agent.session=Path(self.tmp.name)/'session.json'
        self.agent.last_successful_model='slow'
        self.agent.model_observations={'slow':{'tool_calls_observed':True},'alternate':{'tool_calls_observed':True}}

    def test_second_timeout_switches_model_without_replaying_tools(self):
        payload={'model':'auto','messages':[{'role':'tool','tool_call_id':'t','content':'8 tests OK'}]}
        with patch.object(self.agent,'api',side_effect=[StreamInterrupted('timeout'),StreamInterrupted('timeout'),{'ok':True}]) as api,patch.object(self.agent,'tool') as tool:
            self.assertEqual(self.agent.request_turn(payload),{'ok':True})
        self.assertEqual([c.args[1]['model'] for c in api.call_args_list],['auto','slow','alternate'])
        self.assertTrue(all(c.args[1]['messages'] is payload['messages'] for c in api.call_args_list))
        tool.assert_not_called(); self.assertIn('slow',self.agent.cooldowns)

    def test_all_failures_are_bounded(self):
        with patch.object(self.agent,'api',side_effect=StreamInterrupted('timeout')) as api:
            with self.assertRaises(StreamInterrupted): self.agent.request_turn({'model':'auto','messages':[]})
        self.assertEqual(api.call_count,3)

    def test_interrupt_does_not_start_another_request(self):
        with patch.object(self.agent,'api',side_effect=[StreamInterrupted('timeout'),KeyboardInterrupt]) as api:
            with self.assertRaises(KeyboardInterrupt): self.agent.request_turn({'model':'auto','messages':[]})
        self.assertEqual(api.call_count,2)
        def stream():
            raise KeyboardInterrupt
            yield b''
        with self.assertRaises(KeyboardInterrupt): consume(stream(),lambda text:None)

    def test_candidates_respect_known_context_and_availability(self):
        catalog=[{'id':'slow','available':False},{'id':'alternate','context_window':10}]
        self.assertEqual(fallback_candidates(self.agent,{'model':'auto','messages':[]},catalog),[])

    def test_unverified_vision_not_used(self):
        payload={'model':'auto','messages':[{'content':[{'type':'image_url','image_url':{'url':'data:image/png;base64,AA'}}]}]}
        self.assertEqual(fallback_candidates(self.agent,payload,[]),[])

    def test_auto_waits_and_resumes_same_payload(self):
        self.agent.mode='auto'
        payload={'model':'auto','messages':[{'role':'tool','tool_call_id':'t','content':'already done'}]}
        with patch.object(self.agent,'_request_attempt',side_effect=[StreamInterrupted('timeout'),{'ok':True}]) as attempts,patch.object(self.agent,'wait_for_recovery') as wait,patch.object(self.agent,'tool') as tool:
            self.assertEqual(self.agent.request_turn(payload),{'ok':True})
        wait.assert_called_once_with(30)
        self.assertTrue(all(call.args[0] is payload for call in attempts.call_args_list))
        tool.assert_not_called()

    def test_auto_wait_respects_router_quota_and_cancellation(self):
        import time
        from model_health import RateLimited
        self.agent.mode='auto'; self.agent.cooldowns['auto']=time.time()+3600
        with patch.object(self.agent,'_request_attempt',side_effect=RateLimited('quota')) as attempts,patch.object(self.agent,'wait_for_recovery',side_effect=KeyboardInterrupt) as wait:
            with self.assertRaises(KeyboardInterrupt): self.agent.request_turn({'model':'auto','messages':[]})
        self.assertGreater(wait.call_args.args[0],3500); attempts.assert_called_once()

    def test_full_turn_does_not_repeat_completed_tool_after_outage(self):
        import json
        self.agent.mode='auto'
        call={'id':'write-once','type':'function','function':{'name':'write_file','arguments':json.dumps({'path':'result.txt','content':'saved'})}}
        first={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[call]}}]}
        final={'choices':[{'message':{'role':'assistant','content':'Completato.'}}]}
        real_tool=self.agent.tool
        with patch.object(self.agent,'_request_attempt',side_effect=[first,StreamInterrupted('timeout'),final]) as attempts,patch.object(self.agent,'wait_for_recovery') as wait,patch.object(self.agent,'tool',wraps=real_tool) as tool:
            self.agent.turn('Scrivi result.txt')
        self.assertEqual(self.agent.turn_outcome,'completed')
        self.assertEqual((Path(self.tmp.name)/'result.txt').read_text(),'saved')
        self.assertEqual(sum(c.args[0]=='write_file' for c in tool.call_args_list),1)
        self.assertEqual(sum(m['role']=='user' for m in self.agent.messages),1)
        self.assertIs(attempts.call_args_list[1].args[0],attempts.call_args_list[2].args[0])
        wait.assert_called_once()


if __name__=='__main__': unittest.main()
