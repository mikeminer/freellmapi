"""Real regression: transport retries + invalid JSON must share one budget."""
import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from trading_runtime import plan_once
from controller_recovery import request as recover

def reply(model,text='{"decision":"hold","reason":"No confirmed setup"}'):
    return {'model':model,'choices':[{'message':{'content':text}}],'usage':{'total_tokens':10}}

class PlannerBudgetTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.home=Path(self.temp.name)
        (self.home/'config.json').write_text(json.dumps({'base_url':'http://127.0.0.1:9/v1'}))
        self.payload={'model_route':'broken','snapshot':{'coin':'BTC'},'account':{'baseline':'1000'},'policy':{'max_leverage':1},'history':[{'id':'ALREADY-FINISHED'}]}
    def agent(self,replies):
        rows=[{'id':n,'execution_status':'ready','available':True} for n in ('a','b','c','healthy')]
        return SimpleNamespace(api=Mock(side_effect=replies),_api=Mock(return_value={'data':rows}),save=Mock(),cooldowns={},model_observations={})
    def run_plan(self,a):
        with patch('freellmapi.Agent',return_value=a),patch('freellmapi.stored_token',return_value='synthetic-token'),patch('freellmapi.HOME',self.home):
            return plan_once(self.home,self.payload)
    def test_two_transport_calls_then_bad_json_does_not_get_a_fourth_attempt(self):
        a=self.agent([TimeoutError('first provider'),reply('a','invalid json'),TimeoutError('third call')]);api=a.api
        with self.assertRaises(RuntimeError) as caught:self.run_plan(a)
        self.assertNotIn('planner_request_limit',str(caught.exception));self.assertNotIn('planner_budget_exhausted',str(caught.exception))
        self.assertEqual(api.call_count,3)
    def test_last_budgeted_call_can_succeed_after_transport_and_protocol_failure(self):
        a=self.agent([TimeoutError('first provider'),reply('a','invalid json'),reply('b')]);api=a.api
        result=self.run_plan(a);self.assertEqual(result['requests'],3);self.assertEqual(result['model'],'b');self.assertEqual(api.call_count,3)
        inputs=[c.args[1]['messages'] for c in api.call_args_list]
        self.assertTrue(all(x==inputs[0] for x in inputs));self.assertIn('ALREADY-FINISHED',inputs[0][1]['content'])
        self.assertTrue(all('tools' not in c.args[1] for c in api.call_args_list))
    def test_three_malformed_replies_exhaust_cleanly_without_extra_call(self):
        a=self.agent([reply('a','bad'),reply('b','bad'),reply('c','bad')]);api=a.api
        with self.assertRaisesRegex(ValueError,'planner_unavailable'):self.run_plan(a)
        self.assertEqual(api.call_count,3)
    def test_failed_route_cooldown_survives_new_planner_process(self):
        first=self.agent([TimeoutError('provider down'),reply('a')]);self.run_plan(first)
        data=json.loads((self.home/'trading-service/planner-health.json').read_text())
        self.assertGreater(data['cooldowns']['broken'],time.time()+250)
        second=self.agent([reply('a')]);api=second.api;result=self.run_plan(second)
        self.assertEqual(result['requests'],1);self.assertNotEqual(api.call_args.args[1]['model'],'broken')
    def test_auth_error_is_not_hidden_or_retried(self):
        a=self.agent([RuntimeError('API HTTP 401: invalid_api_key')]);api=a.api
        with self.assertRaisesRegex(RuntimeError,'401'):self.run_plan(a)
        self.assertEqual(api.call_count,1);a._api.assert_not_called()
    def test_one_remaining_transport_attempt_is_honored(self):
        a=self.agent([TimeoutError('down')]);api=a.api
        with self.assertRaises(RuntimeError):recover(a,{'model':'broken'},lambda _:None,max_attempts=1)
        self.assertEqual(api.call_count,1)
    def test_missing_choices_are_protocol_failures_with_bounded_recovery(self):
        a=self.agent([{'model':'broken','choices':[]},reply('a')]);api=a.api
        self.assertEqual(self.run_plan(a)['model'],'a');self.assertEqual(api.call_count,2)

if __name__=='__main__':unittest.main()
