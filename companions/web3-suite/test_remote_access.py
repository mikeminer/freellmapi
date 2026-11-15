import io,json,ssl,tempfile,threading,time,unittest,urllib.error,urllib.request
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import remote_access as r

class RemoteTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.home=Path(self.temp.name)
        self.cancel=Mock();self.remote=r.Remote(self.home,lambda:{'model':'test','messages':[{'role':'user','content':'MEMORY-MARKER'}]},self.cancel).start(port=0,private_vpn=True);self.addCleanup(self.remote.close)
        self.opener=urllib.request.build_opener(urllib.request.ProxyHandler({}))
        self.pair=self.remote.pair();self.login=self.call('/v1/pair',{'code':self.pair['code'],'name':'Synthetic Android'});self.token=self.login['token'];self.device=self.login['device']
    def call(self,path,body=None,token=None,headers=None):
        h={'Content-Type':'application/json'}
        if token:h['Authorization']='Bearer '+token
        h.update(headers or {})
        req=urllib.request.Request(self.remote.url+path,data=json.dumps(body).encode() if body is not None else None,headers=h)
        with self.opener.open(req,timeout=8) as response:return json.load(response)
    def test_usb_authenticated_state_and_no_unpaired_access(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/v1/state')
        self.assertEqual(e.exception.code,401);self.assertEqual(self.call('/v1/state',token=self.token)['messages'][0]['content'],'MEMORY-MARKER')
    def test_instance_and_cursor_reset_recover_history_without_replay(self):
        state=self.call('/v1/state',token=self.token)
        self.assertEqual(state['instance'],self.remote.instance)
        gap=self.remote.poll(999,timeout=0)
        self.assertTrue(gap['gap']);self.assertEqual(gap['cursor'],self.remote.sequence)
        self.assertTrue(self.remote.inputs.empty())
        other=r.Remote(self.home,lambda:{})
        try:self.assertNotEqual(other.instance,state['instance']);self.assertIsNone(other.pop())
        finally:other.trading.close()
    def test_mobile_monitor_status_command_stays_native_and_does_not_send_orders(self):
        with patch('hyperliquid_tools.submit',side_effect=AssertionError('Read-only monitor status')) as submit:
            value=self.remote.enqueue(self.device,{'request_id':'monitor-native-status','text':'/trading monitor'})
            self.assertEqual(value['state'],'queued');self.assertEqual(self.remote.pop(),'/trading monitor')
        submit.assert_not_called()
    def test_mobile_timings_command_is_native_and_read_only(self):
        value=self.remote.enqueue(self.device,{'request_id':'timing-native-status','text':'/timings'})
        self.assertEqual(value['state'],'queued');self.assertEqual(self.remote.pop(),'/timings')
    def test_pair_code_is_one_use_and_token_is_only_hashed_on_pc(self):
        with self.assertRaises(urllib.error.HTTPError):self.call('/v1/pair',{'code':self.pair['code']})
        self.assertNotIn(self.token,self.remote.devices_path.read_text());self.assertNotIn('api_key',self.remote.devices_path.read_text())
    def test_pair_code_expiry_and_global_attempt_cap(self):
        p=self.remote.pair();self.remote.pair_deadline=0
        with self.assertRaises(PermissionError):self.remote.pair_device({'code':p['code']})
        p=self.remote.pair()
        for _ in range(8):
            with self.assertRaises(PermissionError):self.remote.pair_device({'code':'not-code'})
        with self.assertRaises(PermissionError):self.remote.pair_device({'code':p['code']})
    def test_browser_origin_is_rejected_even_with_device_token(self):
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/v1/input',{'request_id':'request001','text':'hello'},self.token,{'Origin':'https://example.invalid'})
        self.assertEqual(e.exception.code,401);self.assertTrue(self.remote.inputs.empty())
    def test_duplicate_input_processed_once_and_persistent_after_restart(self):
        args={'request_id':'request001','text':'hello'};one=self.call('/v1/input',args,self.token);two=self.call('/v1/input',args,self.token)
        self.assertEqual(one['state'],'queued');self.assertTrue(two['reused']);self.assertEqual(self.remote.pop(),'hello');self.assertIsNone(self.remote.pop())
        other=r.Remote(self.home,lambda:{})
        result=other.enqueue(self.device,args);self.assertTrue(result['reused']);self.assertEqual(result['state'],'interrupted_not_replayed');self.assertIsNone(other.pop())
    def test_finished_incomplete_execution_stays_deduplicated_after_restart(self):
        args={'request_id':'synthetic-old-order','text':'esegui ordine fittizio; no real funds'}
        self.remote.enqueue(self.device,args);self.assertEqual(self.remote.pop(),args['text']);self.remote.complete('incomplete')
        with patch('hyperliquid_tools.submit',side_effect=AssertionError('Never replay an old order')) as submit:
            other=r.Remote(self.home,lambda:{'messages':[{'role':'user','content':'PRESERVED-CONTEXT'}]})
            answer=other.enqueue(self.device,args)
            self.assertTrue(answer['reused']);self.assertEqual(answer['outcome'],'incomplete');self.assertEqual(answer['state'],'finished')
            self.assertIsNone(other.pop());self.assertEqual(other.state(self.device)['messages'][0]['content'],'PRESERVED-CONTEXT')
        submit.assert_not_called()

    def _pair(self,home):
        first=r.Remote(home,lambda:{});second=r.Remote(home,lambda:{})
        self.addCleanup(first.trading.close);self.addCleanup(second.trading.close)
        device='synthetic-device'
        for remote in (first,second):remote.devices[device]={'id':device,'name':'synthetic','expires':time.time()+86400}
        return first,second,device
    def _saved(self,home):
        rows=json.loads((home/'remote'/'requests.json').read_text(encoding='utf-8'))
        return {row['value']['request_id']:row['value'] for row in rows}
    def test_stale_bridge_preserves_foreign_receipt_and_does_not_requeue(self):
        home=Path(self.temp.name)/'shared-requests';first,second,device=self._pair(home)
        finished={'request_id':'receipt-finished-a','text':'synthetic finished receipt'}
        first.enqueue(device,finished);self.assertEqual(first.pop(),finished['text']);first.complete('completed')
        second.enqueue(device,{'request_id':'receipt-fresh-b','text':'synthetic fresh receipt'})
        saved=self._saved(home)
        self.assertEqual(saved['receipt-finished-a']['state'],'finished')
        self.assertEqual(saved['receipt-finished-a']['outcome'],'completed')
        self.assertEqual(saved['receipt-fresh-b']['state'],'queued')
        queued_before=second.inputs.qsize()
        with patch('hyperliquid_tools.submit',side_effect=AssertionError('must not replay a finished receipt')):
            again=second.enqueue(device,finished)
            visible=second.state(device,'receipt-finished-a')['request_receipt']
        self.assertTrue(again['reused']);self.assertEqual(again['state'],'finished');self.assertEqual(again['outcome'],'completed')
        self.assertEqual(second.inputs.qsize(),queued_before)
        self.assertTrue(visible['found']);self.assertEqual(visible['receipt']['state'],'finished');self.assertEqual(visible['receipt']['outcome'],'completed')
        self.assertEqual(self._saved(home)['receipt-finished-a']['state'],'finished')
    def test_late_finish_is_visible_and_running_receipt_is_not_rewritten(self):
        home=Path(self.temp.name)/'late-finish';first=r.Remote(home,lambda:{});self.addCleanup(first.trading.close)
        device='synthetic-device';first.devices[device]={'id':device,'name':'synthetic','expires':time.time()+86400}
        ident={'request_id':'late-finish-0001','text':'synthetic late'}
        first.enqueue(device,ident);self.assertEqual(first.pop(),ident['text'])
        second=r.Remote(home,lambda:{});self.addCleanup(second.trading.close)
        second.devices[device]={'id':device,'name':'synthetic','expires':time.time()+86400}
        second.enqueue(device,{'request_id':'late-other-0001','text':'synthetic other'})
        saved=self._saved(home)
        self.assertEqual(saved['late-finish-0001']['state'],'running')
        self.assertEqual(saved['late-other-0001']['state'],'queued')
        first.complete('completed')
        visible=second.state(device,'late-finish-0001')['request_receipt']
        self.assertEqual(visible['receipt']['state'],'finished');self.assertEqual(visible['receipt']['outcome'],'completed')
        before=second.inputs.qsize();again=second.enqueue(device,ident)
        self.assertTrue(again['reused']);self.assertEqual(again['state'],'finished');self.assertEqual(again['outcome'],'completed')
        self.assertEqual(second.inputs.qsize(),before)
        self.assertEqual(self._saved(home)['late-finish-0001']['state'],'finished')
    def test_concurrent_bridges_do_not_drop_receipts(self):
        home=Path(self.temp.name)/'concurrent-requests';first,second,device=self._pair(home)
        barrier=threading.Barrier(2)
        def send(remote,ident):
            barrier.wait(timeout=5);remote.enqueue(device,{'request_id':ident,'text':'synthetic '+ident})
        for index in range(12):
            left='concurrent-a-'+f'{index:04d}';right='concurrent-b-'+f'{index:04d}'
            workers=[threading.Thread(target=send,args=(first,left)),threading.Thread(target=send,args=(second,right))]
            for worker in workers:worker.start()
            for worker in workers:worker.join(5);self.assertFalse(worker.is_alive())
            for remote in (first,second):
                while True:
                    if remote.pop() is None:break
                    remote.complete('completed')
        saved=self._saved(home)
        for index in range(12):
            self.assertIn('concurrent-a-'+f'{index:04d}',saved)
            self.assertIn('concurrent-b-'+f'{index:04d}',saved)

    def test_exact_receipt_lookup_recovers_older_than_recent_fifteen_without_enqueue(self):
        for i in range(20):
            self.remote.enqueue(self.device,{'request_id':'old-request-'+str(i),'text':'Synthetic read-only request'})
            self.remote.pop();self.remote.complete('incomplete' if i==0 else 'completed')
        plain=self.call('/v1/state',token=self.token)
        self.assertNotIn('old-request-0',[x['request_id'] for x in plain['requests']])
        with patch.object(self.remote,'enqueue',side_effect=AssertionError('GET must never enqueue')):
            exact=self.call('/v1/state?request_id=old-request-0',token=self.token)['request_receipt']
        self.assertTrue(exact['found']);self.assertEqual(exact['receipt']['outcome'],'incomplete')
        self.assertIsNone(self.remote.pop())
        other=r.Remote(self.home,lambda:{})
        try:self.assertEqual(other.state(self.device,'old-request-0')['request_receipt']['receipt']['state'],'finished')
        finally:other.trading.close()

    def test_exact_lookup_is_device_scoped_and_unknown_never_claims_nonexecution(self):
        self.remote.enqueue(self.device,{'request_id':'only-device-one','text':'Synthetic request'})
        missing=self.remote.state('different-device','only-device-one')['request_receipt']
        self.assertFalse(missing['found']);self.assertIsNone(missing['receipt']);self.assertNotIn('not_executed',missing)
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/v1/state?request_id=bad!',token=self.token)
        self.assertEqual(e.exception.code,400)
    def test_no_unrestricted_shell_endpoint_or_internal_command_injection(self):
        for text in ('/_hyperliquid mainnet-master','/remote pair','/run Remove-Item *','/exit'):
            with self.subTest(text=text),self.assertRaises(ValueError):self.remote.enqueue(self.device,{'request_id':'request001','text':text})
        with self.assertRaises(urllib.error.HTTPError) as e:self.call('/v1/shell',{'command':'whoami'},self.token)
        self.assertEqual(e.exception.code,404);self.assertTrue(self.remote.inputs.empty())
    def test_question_one_at_a_time_and_open_response(self):
        self.remote.owner=self.device;result=[]
        worker=threading.Thread(target=lambda:result.append(self.remote.ask('Quale progetto?',['A','B'])));worker.start()
        try:
            deadline=time.monotonic()+3
            while not self.remote.question and time.monotonic()<deadline:time.sleep(.01)
            q=self.call('/v1/state',token=self.token)['question'];self.assertEqual(q['options'],['A','B'])
            self.call('/v1/answer',{'id':q['id'],'answer':'Risposta libera C'},self.token);worker.join(2)
            self.assertEqual(result[0]['answer'],'Risposta libera C')
            with self.assertRaises(ValueError):self.remote.answer_question(self.device,{'id':q['id'],'answer':'A'})
        finally:self.remote.close();worker.join(2)
    def test_question_cannot_be_answered_by_other_device(self):
        self.remote.owner=self.device;self.remote.question={'owner':self.device,'id':'abc','question':'Which?','options':['A','B']}
        with self.assertRaises(ValueError):self.remote.answer_question('other-device',{'id':'abc','answer':'A'})
        self.assertIsNone(self.remote.answer)
    def test_approval_uses_explicit_mobile_answer_in_ask_mode(self):
        from freellmapi import Agent
        a=Agent(self.home,'http://127.0.0.1:9','synthetic-token','test');self.remote.owner=self.device
        def answer():
            deadline=time.monotonic()+3
            while not self.remote.question and time.monotonic()<deadline:time.sleep(.01)
            self.remote.answer_question(self.device,{'id':self.remote.question['id'],'answer':'Nega'})
        worker=threading.Thread(target=answer);worker.start()
        with redirect_stdout(io.StringIO()),patch('builtins.input',side_effect=AssertionError('No terminal input for remote approval')):self.assertFalse(a.approve('Write fixture file'))
        worker.join(3)
    def test_revoke_cancels_question_and_queued_work(self):
        self.remote.enqueue(self.device,{'request_id':'request001','text':'hello'});self.remote.revoke()
        with self.assertRaises(PermissionError):self.remote.authorize('Bearer '+self.token)
        self.assertIsNone(self.remote.pop())
    def test_cancel_is_a_request_not_an_order_replay(self):
        self.assertFalse(self.call('/v1/cancel',{},self.token)['requested']);self.cancel.assert_not_called()
        self.remote.busy=True;self.assertTrue(self.call('/v1/cancel',{},self.token)['requested'])
        deadline=time.monotonic()+2
        while not self.cancel.called and time.monotonic()<deadline:time.sleep(.01)
        self.cancel.assert_called_once()
    def test_service_stop_is_authenticated_and_bypasses_a_busy_chat(self):
        from trading_service import folder
        self.remote.busy=True
        with self.assertRaises(urllib.error.HTTPError):self.call('/v1/trading/service-stop',{})
        self.assertFalse((folder(self.home)/'STOP').exists())
        result=self.call('/v1/trading/service-stop',{},self.token)
        self.assertTrue(result['new_orders_stopped']);self.assertFalse(result['positions_closed'])
        self.assertTrue((folder(self.home)/'STOP').exists());self.assertTrue(self.remote.inputs.empty())
    def test_secret_redaction_stream_and_private_pair_not_broadcast(self):
        import terminal_ui
        with redirect_stdout(io.StringIO()):
            terminal_ui.write('Authorization: Bearer synthetic-secret-value')
            r.show_pair(self.remote)
        value=json.dumps(self.remote.poll(0,0));self.assertNotIn('synthetic-secret-value',value);self.assertNotIn(self.remote.pair_code,value);self.assertNotIn('freellmapi://pair',value)
    def test_model_and_session_commands_are_serialized(self):
        commands=['/model auto:reliable','/resume '+'a'*32,'/new','/ask','/auto off']
        for i,text in enumerate(commands):self.remote.enqueue(self.device,{'request_id':'request00'+str(i),'text':text})
        for text in commands:self.assertEqual(self.remote.pop(),text);self.remote.complete()
    def test_event_cursor_gap_is_explicit_and_snapshot_retains_context(self):
        for i in range(1300):self.remote.publish('output',str(i))
        self.assertTrue(self.remote.poll(1,0)['gap']);self.assertEqual(self.remote.state(self.device)['messages'][0]['content'],'MEMORY-MARKER')
    def test_tls_identity_is_stable_for_reconnection(self):
        _,first=r.tls_context(self.remote.folder);_,second=r.tls_context(self.remote.folder);self.assertEqual(first,second)
    def test_plain_http_rejected_on_public_or_unverified_vpn_bind(self):
        other=r.Remote(self.home/'other',lambda:{})
        with patch('remote_access._active',None),patch('remote_access.vpn_address',return_value=None):
            with self.assertRaises(ValueError):other.start('0.0.0.0',port=0,private_vpn=True)
            with self.assertRaises(ValueError):other.start('100.64.1.2',port=0,private_vpn=True)
        with self.assertRaises(ValueError):self.remote.pair('http://example.invalid:31416')
    def test_snapshot_uses_current_catalog_without_changing_history(self):
        from freellmapi import Agent
        a=Agent(self.home,'http://127.0.0.1:9','synthetic-token','auto');a.messages=[{'role':'user','content':'MEMORY-MARKER'}]
        catalog=[{'id':'model-one'}];first=r.snapshot(a,lambda:catalog);catalog.append({'id':'model-two'});second=r.snapshot(a,lambda:catalog)
        self.assertNotIn('model-two',first['models']);self.assertIn('model-two',second['models']);self.assertEqual(second['messages'],first['messages'])
    def test_mobile_snapshot_friendly_metadata_and_current_request_usage(self):
        from freellmapi import Agent
        from request_progress import Progress
        a=Agent(self.home,'http://127.0.0.1:9','synthetic-token','auto')
        a.sessions=SimpleNamespace(list=lambda:[{'id':'a'*32,'title':'Gioco Seeker','root':'C:/Projects/game','updated':'2026-11-14T12:00:00Z','messages':23}])
        a._request_progress=Progress('auto');a._request_progress.model('current-model');a._request_progress.activity({'total_tokens':123})
        state=r.snapshot(a,[])
        self.assertEqual(state['sessions'][0]['messages'],23);self.assertEqual(state['sessions'][0]['title'],'Gioco Seeker')
        self.assertEqual(state['request_progress']['actual_model'],'current-model');self.assertEqual(state['request_progress']['usage'],{'total_tokens':123})
    def test_remote_only_does_not_wait_for_plugin_consent_before_pairing(self):
        import freellmapi as f,os,sys
        (self.home/'config.json').write_text(json.dumps({'base_url':'http://127.0.0.1:9','model':'test','auto_skills':False,'streaming':False}),encoding='utf-8')
        original=f.Agent;agents=[]
        def make(*args,**kwargs):
            a=original(*args,**kwargs);a.extensions.load=Mock(side_effect=AssertionError('No hidden plugin approval before pairing'));agents.append(a);return a
        self.remote.pop=Mock(side_effect=EOFError)
        with patch.object(f,'HOME',self.home),patch.object(f,'Agent',side_effect=make),patch.object(original,'api',return_value={'data':[{'id':'test'}]}),patch.object(f.Seeker,'start'),patch.object(f,'output'),patch('remote_access.command',return_value=False),patch.dict(os.environ,{'FREELLMAPI_SESSION_TOKEN':'synthetic-test-only'}),patch.object(sys,'argv',['freellmapi','--remote-only','--new','--model','test']),patch('builtins.input',side_effect=AssertionError('No terminal consent in remote-only')):
            f.main()
        self.assertEqual(len(agents),1);agents[0].extensions.load.assert_not_called()
    def test_reload_can_be_requested_after_pairing(self):
        self.remote.enqueue(self.device,{'request_id':'reload001','text':'/reload'});self.assertEqual(self.remote.pop(),'/reload')

if __name__=='__main__':unittest.main()
