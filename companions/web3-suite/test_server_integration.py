import io
import json
import tempfile
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from freellmapi import Agent
from model_health import RateLimited
from server_integration import eligible,supports_routing


class ServerIntegrationTests(unittest.TestCase):
    def test_actual_provider_alias_keeps_requested_route_capability(self):
        response={'model':'provider/Meta-Llama-3_3','choices':[{'message':{'role':'assistant','tool_calls':[{'id':'x','type':'function','function':{'name':'probe','arguments':'{}'}}]}}]}
        with patch.object(self.a,'_api',return_value=response),patch('freellmapi.reliable_runtime.atomic_bytes'):
            self.a.api('chat/completions',{'model':'llama-route'})
        self.assertTrue(self.a.model_observations['llama-route']['tool_calls_observed']);self.assertEqual(self.a.model_observations['llama-route']['actual_model'],'provider/Meta-Llama-3_3')
    def test_console_unicode_failure_is_not_an_api_interruption(self):
        from api_stream import consume
        chunk={'model':'m','choices':[{'delta':{'content':'entry\u2011TP','tool_calls':[{'index':0,'id':'native-id','function':{'name':'hyperliquid_status','arguments':'{}'}}]},'finish_reason':'tool_calls'}]}
        body=('data: '+json.dumps(chunk)+'\n\ndata: [DONE]\n\n').encode()
        def display(text):raise UnicodeEncodeError('cp1252',text,0,1,'console character')
        result=consume(io.BytesIO(body),display)
        self.assertEqual(result['choices'][0]['message']['content'],'entry\u2011TP');self.assertIn('_render_warning',result)
        self.assertEqual(result['choices'][0]['message']['tool_calls'][0]['id'],'native-id')
    def test_cp1252_stream_renders_safely_without_changing_model_context(self):
        import terminal_ui as ui
        raw=io.BytesIO();terminal=io.TextIOWrapper(raw,encoding='cp1252',errors='strict')
        with patch('terminal_ui.sys.stdout',terminal),patch('terminal_ui.fancy',return_value=False):
            ui.stream_text('Originale 3D \u2011 \U0001f680');ui.finish_stream()
        terminal.flush();self.assertTrue(raw.getvalue());terminal.detach()
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.a=Agent(self.tmp.name,'http://localhost','test','auto'); self.a.server_router=True
        self.ready={'id':'ready-model','available':True,'execution_status':'ready','supported_parameters':['tools'],'context_window':128000}
    def test_fresh_readiness_then_delegate_no_cached_model(self):
        self.a.last_successful_model='stale'
        p={'model':'auto','messages':[{'role':'tool','content':'already done'}],'tools':[{}]}
        with patch.object(self.a,'api',side_effect=[TimeoutError('silent'),{'ok':True}]) as api,patch.object(self.a,'_api',return_value={'data':[self.ready]}) as get:
            self.assertEqual(self.a.request_turn(p),{'ok':True})
        get.assert_called_once_with('models?execution_status=ready')
        self.assertEqual(api.call_args.args[1]['model'],'auto:reliable')
        self.assertIs(api.call_args.args[1]['messages'],p['messages'])
    def test_named_chain_and_preset_never_escape(self):
        for name in ('auto:coding','auto:smart','auto:reliable'):
            with patch.object(self.a,'api',side_effect=RateLimited('quota')) as api,patch.object(self.a,'_api') as get:
                with self.assertRaises(RateLimited): self.a.request_turn({'model':name})
            api.assert_called_once(); get.assert_not_called()
    def test_empty_ready_pool_no_retry(self):
        with patch.object(self.a,'api',side_effect=RateLimited('quota')) as api,patch.object(self.a,'_api',return_value={'data':[{'id':'auto'}]}):
            with self.assertRaises(RateLimited): self.a.request_turn({'model':'auto'})
        api.assert_called_once()
    def test_runtime_selected_worker_can_failover_without_losing_tool_results(self):
        self.a._automatic_delivery_route='runtime-worker'
        p={'model':'runtime-worker','messages':[{'role':'tool','content':'build already done'}],'tools':[{}]}
        with patch.object(self.a,'api',side_effect=[TimeoutError('silent'),{'ok':True}]) as api,patch.object(self.a,'_api',return_value={'data':[self.ready]}):
            self.assertEqual(self.a.request_turn(p),{'ok':True})
        self.assertEqual(api.call_args.args[1]['model'],'auto:reliable')
        self.assertIs(api.call_args.args[1]['messages'],p['messages'])
    def test_delivery_worker_selection_excludes_failed_alias_and_short_context(self):
        from android_delivery import recovery_route
        rows=[{**self.ready,'id':'weak-worker'}, {**self.ready,'id':'short','context_window':100},self.ready]
        with patch.object(self.a,'_api',return_value={'data':rows}) as get:
            result=recovery_route(self.a,{'model':'auto','tools':[{}],'messages':[{'role':'tool','content':'done'}]}, {'provider/weak-worker'})
        self.assertEqual(result,'ready-model')
        self.assertEqual(self.a.context_limit,128000)
        get.assert_called_once_with('models?execution_status=ready')
    def test_runtime_stale_catalog_model_404_falls_back_with_context(self):
        self.a._automatic_delivery_route='stale'
        p={'model':'stale','messages':[{'role':'tool','content':'work retained'}],'tools':[{}]}
        with patch.object(self.a,'api',side_effect=[RuntimeError('API HTTP 404: {"code":"model_not_found"}'),{'ok':True}]) as api,patch.object(self.a,'_api',return_value={'data':[self.ready]}):
            self.assertEqual(self.a.request_turn(p),{'ok':True})
        self.assertEqual(api.call_args.args[1]['model'],'auto:reliable')
        self.assertIs(api.call_args.args[1]['messages'],p['messages'])
        self.assertGreater(self.a.cooldowns['stale'],0)
    def test_delivery_prefers_observed_tool_worker_to_unproven_catalog_entry(self):
        from android_delivery import recovery_route
        self.a.model_observations={'provider/probed':{'tool_probe_passed':True}}
        with patch.object(self.a,'_api',return_value={'data':[{**self.ready,'id':'unproven'}, {**self.ready,'id':'probed'}]}):
            self.assertEqual(recovery_route(self.a,{'tools':[{}]},set()),'probed')
    def test_fallback_timeout_uses_actual_route_not_hour_long_stale_model_cooldown(self):
        self.a.mode='auto';self.a.session=__import__('pathlib').Path(self.tmp.name)/'route.json'
        self.a._automatic_delivery_route='stale'
        p={'model':'stale','messages':[{'role':'tool','content':'work preserved'}],'tools':[{}]}
        with patch.object(self.a,'api',side_effect=[RuntimeError('API HTTP 404: {"code":"model_not_found"}'),TimeoutError('fallback timeout'),{'ok':True}]) as api,patch.object(self.a,'_api',return_value={'data':[self.ready]}),patch.object(self.a,'wait_for_recovery') as wait:
            self.assertEqual(self.a.request_turn(p),{'ok':True})
        self.assertLess(wait.call_args.args[0],120)
        self.assertEqual(p['model'],'auto:reliable')
        self.assertEqual(api.call_args.args[1]['model'],'auto:reliable')
    def test_monthly_key_cap_is_not_model_failover(self):
        with patch.object(self.a,'api',side_effect=RateLimited('budget',code='quota_exceeded')),patch.object(self.a,'_api') as get:
            with self.assertRaises(RateLimited): self.a.request_turn({'model':'auto'})
        get.assert_not_called()
    def test_catalog_filters_capabilities_context_and_state(self):
        rows=[self.ready,{**self.ready,'execution_status':'exhausted'},{**self.ready,'supported_parameters':[]},{**self.ready,'context_window':100}]
        self.assertEqual(eligible(rows,{'tools':[{}]}),[self.ready])
        self.assertTrue(supports_routing(rows)); self.assertFalse(supports_routing([{'id':'auto'}]))
    def test_no_nested_http_retries_for_server_handled_502(self):
        e=HTTPError('http://localhost',502,'bad',{},io.BytesIO(b'{"error":{"code":"upstream_failed"}}'))
        with patch('urllib.request.build_opener') as opener:
            opener.return_value.open.side_effect=e
            with self.assertRaises(RuntimeError): self.a._api('chat/completions',{'model':'auto'})
            opener.return_value.open.assert_called_once()
    def test_idempotency_key_stable_per_route(self):
        self.a._inference_request_key='same-request'
        def reply(*args,**kwargs):
            r=io.BytesIO(b'{}'); r.headers={}; return r
        with patch('urllib.request.build_opener') as build:
            build.return_value.open.side_effect=reply
            for name in ('auto','auto','auto:reliable'): self.a._api('chat/completions',{'model':name})
            keys=[c.args[0].get_header('Idempotency-key') for c in build.return_value.open.call_args_list]
        self.assertEqual(keys[0],keys[1]); self.assertNotEqual(keys[0],keys[2])
    def test_idempotency_scope_ends_before_compaction_or_skill_request(self):
        for result in ({'ok':True},ValueError('invalid')):
            with patch.object(self.a,'_request_with_recovery',side_effect=[result]):
                try: self.a.request_turn({'model':'auto'})
                except ValueError: pass
            self.assertIsNone(self.a._inference_request_key)
    def test_active_stream_can_exceed_first_byte_timeout(self):
        clock=[0.0]
        class Response:
            headers={'Content-Type':'text/event-stream'}
            def __enter__(self): return self
            def __exit__(self,*args): pass
            def __iter__(self):
                for raw in (b': keepalive\n',b'\n',b'data: {"model":"m","choices":[{"delta":{"content":"ok"},"finish_reason":"stop"}]}\n',b'\n',b'data: [DONE]\n',b'\n'):
                    clock[0]+=50; yield raw
        self.a.budget.seconds=0
        with patch('freellmapi.time.monotonic',side_effect=lambda:clock[0]),patch('urllib.request.build_opener') as build:
            build.return_value.open.return_value=Response()
            result=self.a._api('chat/completions',{'model':'auto','stream':True})
        self.assertGreater(clock[0],120); self.assertEqual(result['choices'][0]['message']['content'],'ok')
    def test_permanent_stream_error_not_retried(self):
        from api_stream import consume,StreamAPIError
        from model_health import transient
        with self.assertRaises(StreamAPIError) as caught:
            consume(io.BytesIO(b'data: {"error":{"code":"invalid_api_key","type":"authentication_error"}}\n\n'),lambda text:None)
        self.assertFalse(transient(caught.exception))
