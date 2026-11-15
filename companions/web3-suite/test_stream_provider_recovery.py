"""Exact Nemotron stream_error from session ee77, with ETH already answered."""
import copy
import io
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from api_stream import consume,StreamAPIError
from freellmapi import Agent
from model_health import transient
from stream_recovery import StreamRecoveryExhausted

ERROR={'message':'Provider error (Nemotron 3.5 Lightning 30B A3B (NV)): stream interrupted','type':'stream_error'}


class StreamProviderRecoveryTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup)
        self.agent=Agent(tmp.name,'http://127.0.0.1:9','synthetic','auto:reliable')
        self.agent.session=Path(tmp.name)/'session.json';self.agent.mode='auto';self.agent.server_router=True
        self.agent.last_successful_model='nvidia/nemotron-3.5-lightning-30b-a3b'
        self.agent.last_request={'routing':{'x-routed-via':'nvidia/nvidia/nemotron-3.5-lightning-30b-a3b'}}
        self.agent.model_observations={}
        self.payload={'model':'auto:reliable','tools':[{}],'messages':[{'role':'tool','tool_call_id':'question','content':'{"question":"Which coin?","answer":"ETH"}'},{'role':'tool','tool_call_id':'analysis','content':'ETH native analysis already obtained'}]}
        self.ready=lambda name:{'id':name,'execution_status':'ready','available':True,'context_window':200000,'supported_parameters':['tools']}

    def test_exact_sse_error_is_transient_but_auth_error_is_permanent(self):
        for error,expected in [(ERROR,True),({'type':'authentication_error','code':'invalid_api_key'},False)]:
            with self.assertRaises(StreamAPIError) as caught:consume(io.BytesIO(('data: '+json.dumps({'error':error})+'\n\n').encode()),lambda _:None)
            self.assertEqual(transient(caught.exception),expected)

    def test_auto_preset_changes_failed_worker_without_replaying_question_or_tools(self):
        before=copy.deepcopy(self.payload)
        catalog=[self.ready(self.agent.last_successful_model),{**self.ready('short'),'context_window':100},self.ready('compatible-worker')]
        with patch.object(self.agent,'api',side_effect=[StreamAPIError(ERROR),{'ok':True}]) as api,patch.object(self.agent,'_api',return_value={'data':catalog}) as catalog_read,patch.object(self.agent,'tool') as tools,patch.object(self.agent,'wait_for_recovery') as waits:
            result=self.agent.request_turn(self.payload)
        self.assertEqual(result,{'ok':True});self.assertEqual(api.call_count,2)
        catalog_read.assert_called_once_with('models?execution_status=ready')
        self.assertEqual(api.call_args.args[1]['model'],'compatible-worker')
        self.assertIs(api.call_args.args[1]['messages'],self.payload['messages'])
        self.assertEqual(self.payload,before);tools.assert_not_called();waits.assert_not_called()

    def test_failed_workers_stop_after_two_distinct_alternatives_without_wait_loop(self):
        catalog=[self.ready('first'),self.ready('second'),self.ready('third')]
        with patch.object(self.agent,'api',side_effect=StreamAPIError(ERROR)) as api,patch.object(self.agent,'_api',return_value={'data':catalog}),patch.object(self.agent,'wait_for_recovery') as waits:
            with self.assertRaises(StreamRecoveryExhausted):self.agent.request_turn(self.payload)
        self.assertEqual([c.args[1]['model'] for c in api.call_args_list],['auto:reliable','first','second']);waits.assert_not_called()

    def test_empty_ready_pool_and_catalog_failure_are_bounded(self):
        for value in ({'data':[]},TimeoutError('catalog unavailable')):
            with patch.object(self.agent,'api',side_effect=StreamAPIError(ERROR)) as api,patch.object(self.agent,'_api',side_effect=[value]),patch.object(self.agent,'wait_for_recovery') as waits:
                with self.assertRaises(StreamRecoveryExhausted):self.agent.request_turn(self.payload)
            api.assert_called_once();waits.assert_not_called()

    def test_incomplete_tool_arguments_from_failed_stream_are_never_executable(self):
        wire='data: '+json.dumps({'choices':[{'delta':{'tool_calls':[{'index':0,'id':'unsafe','function':{'name':'hyperliquid_submit','arguments':'{"id":'}}]}}]})+'\n\ndata: '+json.dumps({'error':ERROR})+'\n\n'
        with self.assertRaises(StreamAPIError):consume(io.BytesIO(wire.encode()),lambda _:None)

    def test_duplicate_aliases_unknown_tools_and_global_caps_are_not_bypassed(self):
        from model_health import RateLimited
        self.agent.mode='ask'
        catalog=[{**self.ready('unknown'),'supported_parameters':None},self.ready('p/first'),self.ready('first'),self.ready('second')]
        with patch.object(self.agent,'api',side_effect=[StreamAPIError(ERROR),RateLimited('account cap',code='monthly_budget_exceeded')]) as api,patch.object(self.agent,'_api',return_value={'data':catalog}):
            with self.assertRaises(RateLimited):self.agent.request_turn(self.payload)
        self.assertEqual([c.args[1]['model'] for c in api.call_args_list],['auto:reliable','p/first'])


if __name__=='__main__':unittest.main()
