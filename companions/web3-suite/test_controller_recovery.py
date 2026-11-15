import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from controller_recovery import request
from model_health import RateLimited

class ControllerRecoveryTests(unittest.TestCase):
    def agent(self,errors):
        rows=[{'id':n,'execution_status':'ready','available':True,'supported_parameters':['tools'],'context_window':32000} for n in ('fast','slow','failed')]
        return SimpleNamespace(api=Mock(side_effect=errors),_api=Mock(return_value={'data':rows}),save=Mock(),cooldowns={},model_observations={'provider/fast':{'tool_calls_observed':True,'stream_tool_verified':True,'last_seconds':1}},tool=Mock())
    def test_failover_preserves_exact_messages_and_sticky_route_no_tool_replay(self):
        a=self.agent([RateLimited('quota'),{'choices':[]},{'choices':[]}]);messages=[{'role':'tool','content':'entry intent 123 already prepared'}];p={'model':'failed','tools':[{}],'messages':messages}
        request(a,p,lambda s:None);request(a,p,lambda s:None)
        self.assertEqual([c.args[1]['model'] for c in a.api.call_args_list],['failed','fast','fast'])
        self.assertTrue(all(c.args[1]['messages'] is messages for c in a.api.call_args_list));a.tool.assert_not_called()
    def test_timeout_and_removed_alias_are_bounded_and_skip_bad_routes(self):
        a=self.agent([TimeoutError('first'),RuntimeError('API HTTP 404: model_not_found'),TimeoutError('last')])
        with self.assertRaises(RuntimeError):request(a,{'model':'failed','tools':[{}]},lambda s:None)
        self.assertEqual(a.api.call_count,3);self.assertEqual(len({c.args[1]['model'] for c in a.api.call_args_list}),3)
    def test_account_budget_and_auth_errors_never_route_around_credentials(self):
        for error in (RateLimited('monthly',code='quota_exceeded'),RuntimeError('API HTTP 401: invalid_api_key'),RuntimeError('API HTTP 400: local invalid tool_choice')):
            a=self.agent([error])
            with self.assertRaises(type(error)):request(a,{'model':'auto'},lambda s:None)
            a._api.assert_not_called();self.assertEqual(a.api.call_count,1)
    def test_unavailable_or_no_tool_models_are_not_selected(self):
        a=self.agent([TimeoutError('bad')]);a._api.return_value={'data':[{'id':'embedding','execution_status':'ready','supported_parameters':[]}]}
        with self.assertRaises(RuntimeError):request(a,{'model':'auto','tools':[{}]},lambda s:None)
        self.assertEqual(a.api.call_count,1)
    def test_expired_transport_cooldown_does_not_exclude_a_model_forever(self):
        a=self.agent([{'ok':True}]);a.hyperliquid_failed_routes={'fast'};a.cooldowns={'fast':0};a.hyperliquid_route=None
        a.hyperliquid_protocol_bad={'auto'}
        request(a,{'model':'auto','tools':[{}]},lambda s:None)
        self.assertEqual(a.api.call_args.args[1]['model'],'fast')
    def test_stream_timeout_retries_inference_nonstream_once_without_tool_replay(self):
        a=self.agent([TimeoutError('no SSE'),{'ok':True},{'ok':True}]);messages=[{'role':'tool','content':'prepared same ID 123'}]
        payload={'model':'fast','tools':[{}],'messages':messages,'stream':True,'stream_options':{'include_usage':True}}
        request(a,payload,lambda s:None);request(a,payload,lambda s:None)
        self.assertEqual([c.args[1]['model'] for c in a.api.call_args_list],['fast','fast','fast'])
        self.assertTrue(a.api.call_args_list[0].args[1]['stream']);self.assertFalse(a.api.call_args_list[1].args[1]['stream']);self.assertNotIn('stream_options',a.api.call_args_list[1].args[1])
        self.assertTrue(all(c.args[1]['messages'] is messages for c in a.api.call_args_list));a.tool.assert_not_called();self.assertTrue(payload['stream'])
    def test_unverified_streaming_uses_json_tools_without_changing_user_setting(self):
        a=self.agent([{'ok':True}]);a.model_observations['provider/fast']['stream_tool_verified']=False
        payload={'model':'fast','tools':[{}],'stream':True,'stream_options':{'include_usage':True}}
        request(a,payload,lambda s:None)
        self.assertFalse(a.api.call_args.args[1]['stream']);self.assertNotIn('stream_options',a.api.call_args.args[1]);self.assertTrue(payload['stream'])

if __name__=='__main__':unittest.main()
