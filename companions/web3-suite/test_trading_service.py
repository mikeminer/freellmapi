"""Crash, risk and paper-mode boundary tests; no exchange writes permitted."""
import copy,json,os,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from trading_engine import Engine,Store,TradingError,validate_plan
from test_support_trading import FakeBroker
from trading_service import DEFAULT

class Crash(BaseException):pass

class TradingEngineTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('order_manager.OrderManager.live_receipt_verified',side_effect=lambda r:r.get('state')=='mock_filled'))
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        self.config=copy.deepcopy(DEFAULT);self.config['entry_mode']='immediate';self.now=1000.;self.store=Store(self.home/'paper.sqlite');self.broker=FakeBroker(self.store,self.config)
        self.snapshot={'coin':'BTC','retrieved_at':self.now,'mid':'100','bid':'99.99','ask':'100.01','sz_decimals':3,'max_leverage':40}
        self.plan={'decision':'enter','coin':'BTC','side':'buy','size':'0.1','entry':'100','take_profit':'101','stop_loss':'99','leverage':1,'reason':'Synthetic signal, not real market advice'}
    def engine(self,planner=None,hook=lambda phase:None):
        return Engine(self.store,self.config,lambda coin:copy.deepcopy(self.snapshot),planner or Mock(return_value=self.plan),self.broker,lambda:self.now,hook)

    def test_entry_and_tp_close_are_simulated_and_state_is_durable(self):
        with patch('hyperliquid_tools.submit',side_effect=AssertionError('real order forbidden')),patch('hyperliquid_signer.execute',side_effect=AssertionError('real signing forbidden')):
            cycle=self.engine().tick();self.assertEqual(self.store.recent()[0]['phase'],'done')
            self.assertEqual(len(Store(self.store.path).get('fixture_account')['positions']),1)
            self.broker.mark(dict(self.snapshot,mid='102'))
            self.assertEqual(self.store.get('fixture_account')['positions'],[])
            self.assertGreater(float(self.store.get('fixture_account')['cash']),1000)
            with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM fixture_fills WHERE intent=?',(cycle,)).fetchone()[0],1)

    def test_crash_after_fill_before_completion_recovers_without_duplicate_execution(self):
        def crash(phase):
            if phase=='effect_observed':raise Crash()
        with self.assertRaises(Crash):self.engine(hook=crash).tick()
        before=self.store.get('fixture_account');reopened=Store(self.store.path);broker=FakeBroker(reopened,self.config)
        engine=Engine(reopened,self.config,Mock(),Mock(),broker,lambda:self.now)
        with patch.object(broker,'execute',side_effect=AssertionError('must not replay')):self.assertTrue(engine.recover())
        self.assertEqual(reopened.get('fixture_account'),before);self.assertEqual(reopened.unfinished(),[])
        with reopened.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM fixture_fills').fetchone()[0],1)

    def test_crash_before_dispatch_abandons_old_plan_without_submitting(self):
        def crash(phase):
            if phase=='prepared':raise Crash()
        with self.assertRaises(Crash):self.engine(hook=crash).tick()
        with patch.object(self.broker,'execute',side_effect=AssertionError('must not replay')):self.assertTrue(self.engine().recover())
        self.assertEqual(self.store.recent()[0]['phase'],'abandoned');self.assertEqual(self.store.get('fixture_account')['positions'],[])

    def test_uncertain_external_effect_pauses_and_never_resubmits(self):
        broker=Mock();broker.account.return_value=self.broker.account(self.snapshot);broker.prepare.return_value='id';broker.execute.return_value={'state':'uncertain'};broker.recover.return_value={'resolved':False,'resent':False}
        engine=Engine(self.store,self.config,lambda c:self.snapshot,lambda *args:self.plan,broker,lambda:self.now)
        engine.tick();self.assertEqual(self.store.get('paused'),'uncertain_execution')
        for _ in range(20):engine.tick()
        self.assertEqual(broker.execute.call_count,1)

    def test_invalid_plan_has_backoff_and_does_not_create_fills(self):
        planner=Mock(return_value=dict(self.plan,size='NaN'));engine=self.engine(planner)
        engine.tick()
        for _ in range(30):engine.tick()
        self.assertEqual(planner.call_count,1);self.assertGreater(self.store.get('next_due'),self.now)
        self.assertEqual(self.store.get('fixture_account')['positions'],[])

    def test_risk_limits_block_stale_wrong_coin_leverage_notional_and_fund_bypass(self):
        account=self.broker.account(self.snapshot)
        bad=[dict(self.plan,coin='ETH'),dict(self.plan,size='NaN'),dict(self.plan,size='0.00001'),dict(self.plan,leverage=40),
             dict(self.plan,size='1'),dict(self.plan,stop_loss='102'),dict(self.plan,entry='120'),dict(self.plan,command='shell')]
        for plan in bad:
            with self.subTest(plan=plan),self.assertRaises(TradingError):validate_plan(plan,self.snapshot,account,self.config,self.store,self.now)
        with self.assertRaises(TradingError):validate_plan(self.plan,dict(self.snapshot,retrieved_at=0),account,self.config,self.store,self.now)

    def test_drawdown_stop_survives_restart_and_does_not_close_positions_implicitly(self):
        account=self.store.get('fixture_account');account['cash']='989';self.store.set('fixture_account',account)
        self.engine().tick();self.assertEqual(self.store.get('paused'),'loss_stop')
        self.assertFalse(self.engine().recover());self.assertIsNone(self.engine().tick())
        self.assertEqual(Store(self.store.path).get('paused'),'loss_stop')

    def test_paper_execution_intent_is_idempotent(self):
        self.broker.execute('same-intent',dict(self.plan,notional='10'))
        before=self.store.get('fixture_account');self.broker.execute('same-intent',dict(self.plan,notional='10'))
        self.assertEqual(self.store.get('fixture_account'),before)

    def test_paper_boundary_rejects_signer_and_submit_before_credentials(self):
        import hyperliquid_tools as h,hyperliquid_signer as signer
        from trading_runtime import LiveBroker
        with patch.dict(os.environ,{'FREELLMAPI_READ_ONLY':'1'}),patch('hl_credentials.get') as key,patch.object(h,'permissions') as permissions:
            with self.assertRaises(h.HLProblem):h.submit(Mock(),self.home,'not-an-id')
            self.assertEqual(signer.execute(self.home,'not-an-id')['code'],'read_only_signing_forbidden')
            with self.assertRaises(TradingError):LiveBroker(self.home,self.store,self.config)
        key.assert_not_called();permissions.assert_not_called()

    def test_singleton_prevents_two_workers(self):
        from trading_runtime import singleton
        with singleton(self.home/'worker.lock'):
            with self.assertRaises(OSError):
                with singleton(self.home/'worker.lock'):pass

    def test_feed_rejects_old_or_different_market_data(self):
        from trading_runtime import PublicFeed
        feed=PublicFeed('mainnet',['BTC']);levels=[[{'px':'99'}],[{'px':'101'}]]
        for coin,stamp in [('ETH',int(time.time()*1000)),('BTC',0)]:feed.consume({'channel':'l2Book','data':{'coin':coin,'time':stamp,'levels':levels}})
        self.assertIsNone(feed.quote('BTC'))
        feed.consume({'channel':'l2Book','data':{'coin':'BTC','time':int(time.time()*1000),'levels':levels}})
        self.assertEqual(feed.quote('BTC')['mid'],'100')

    def test_account_limits_also_reject_pending_orders_frequency_and_margin(self):
        account=self.broker.account(self.snapshot)
        for changed in ({'pending_notional':'19'}, {'pending_entry_coins':['ETH']}, {'pending_entry_coins':['BTC']}, {'turnover':'199'}, {'available':'1'}, {'entry_times':[self.now]*3}):
            with self.subTest(changed=changed),self.assertRaises(TradingError):
                validate_plan(self.plan,self.snapshot,dict(account,**changed),self.config,self.store,self.now)

    def test_monitor_start_does_not_require_a_second_service_grant(self):
        import trading_service as service
        service.save(self.home,dict(self.config,mode='live'))
        with patch('trading_runtime.LiveBroker.grant',side_effect=AssertionError('No second approval')),patch('subprocess.Popen',return_value=Mock(pid=123)) as popen:
            result=service.start(self.home)
        self.assertTrue(result['started']);self.assertFalse(result['real_orders_enabled_by_start']);popen.assert_called_once()

    def test_paper_stop_and_resume_preserve_history_and_do_not_touch_real_grants(self):
        import trading_service as service
        service.save(self.home,self.config);self.engine().tick()
        with patch('hyperliquid_tools.stop',return_value={'automation_stopped':True}):
            self.assertTrue(service.stop(self.home)['new_orders_stopped'])
        store=Store(service.folder(self.home)/'live.sqlite');store.set('paused','loss_stop')
        with self.assertRaisesRegex(TradingError,'review_required_loss_stop'):service.resume(self.home)
        self.assertTrue((service.folder(self.home)/'STOP').exists())

    def test_reader_failure_stops_before_planning_and_has_backoff(self):
        planner=Mock();engine=Engine(self.store,self.config,Mock(side_effect=TimeoutError('market unavailable')),planner,self.broker,lambda:self.now)
        engine.tick()
        for _ in range(30):engine.tick()
        planner.assert_not_called();self.assertEqual(len(self.store.recent()),1)
        self.assertEqual(self.store.recent()[0]['phase'],'abandoned')

    def test_models_cannot_authorize_live_service_or_forge_financial_consent(self):
        import hyperliquid_tools as h
        for action in ('authorize','start_live','install'):
            result=h.dispatch(Mock(), 'hyperliquid_service',{'action':action},self.home)
            self.assertFalse(result['ok']);self.assertEqual(result['code'],'service_action')
        self.assertFalse((self.home/'trading-service/live.sqlite').exists())

    def test_planner_model_and_usage_are_recorded_with_cycle(self):
        def planner(*args):return {'decision':'hold','reason':'Synthetic hold'}
        planner.telemetry={'model':'actual-test-model','requests':1,'usage':{'total_tokens':10}}
        self.engine(planner).tick()
        self.assertEqual(self.store.get('last_inference')['model'],'actual-test-model')

    def test_simulated_stop_loss_closes_once_without_real_sdk(self):
        self.engine().tick();self.broker.mark(dict(self.snapshot,mid='98'))
        after=self.store.get('fixture_account');self.assertEqual(after['positions'],[]);self.assertLess(float(after['cash']),1000)
        self.broker.mark(dict(self.snapshot,mid='97'));self.assertEqual(self.store.get('fixture_account'),after)
        with self.store.db() as db:self.assertEqual(db.execute("SELECT COUNT(*) FROM events WHERE kind='mock_exit'").fetchone()[0],1)

    def test_malformed_model_reply_fails_over_with_identical_context_and_three_call_cap(self):
        from types import SimpleNamespace
        from trading_runtime import plan_once
        self.home.joinpath('config.json').write_text(json.dumps({'base_url':'http://127.0.0.1:9/v1'}))
        replies=[{'model':'bad','choices':[{'message':{'content':'I am just a chatbot'}}]},
                 {'model':'good','choices':[{'message':{'content':'{"decision":"hold","reason":"No setup"}'}}]}]
        api=Mock(side_effect=replies)
        agent=SimpleNamespace(api=api,_api=Mock(return_value={'data':[{'id':'good','execution_status':'ready','available':True}]}),save=Mock(),cooldowns={},model_observations={})
        request={'snapshot':self.snapshot,'policy':self.config,'context':'PRESERVE ME'}
        with patch('freellmapi.Agent',return_value=agent),patch('freellmapi.stored_token',return_value='synthetic-unified-api-token'),patch('freellmapi.HOME',self.home):
            result=plan_once(self.home,request)
        self.assertEqual(result['model'],'good');self.assertEqual(result['requests'],2)
        self.assertEqual([c.args[1]['model'] for c in api.call_args_list],['auto:reliable','good'])
        self.assertEqual(api.call_args_list[0].args[1]['messages'],api.call_args_list[1].args[1]['messages'])
        self.assertNotIn('tools',api.call_args_list[0].args[1])

    def test_length_truncated_plans_never_create_watch_or_orders_and_keep_context(self):
        from types import SimpleNamespace
        from trading_runtime import plan_once
        self.home.joinpath('config.json').write_text(json.dumps({'base_url':'http://127.0.0.1:9/v1'}))
        replies=[{'model':'truncated-'+str(i),'choices':[{'finish_reason':'length','message':{'content':'Truncated narrative without a complete JSON plan'}}]} for i in range(3)]
        api=Mock(side_effect=replies)
        agent=SimpleNamespace(api=api,_api=Mock(return_value={'data':[{'id':'ready-'+str(i),'execution_status':'ready','available':True} for i in range(3)]}),save=Mock(),cooldowns={},model_observations={})
        request={'snapshot':self.snapshot,'policy':self.config,'context':'KEEP-ORIGINAL-TRIGGER'}
        with patch('freellmapi.Agent',return_value=agent),patch('freellmapi.stored_token',return_value='synthetic-unified-token'),patch('freellmapi.HOME',self.home),self.assertRaises(TradingError):plan_once(self.home,request)
        self.assertEqual(api.call_count,3)
        payloads=[c.args[1] for c in api.call_args_list]
        self.assertTrue(all(p['messages']==payloads[0]['messages'] for p in payloads))
        self.assertTrue(all(p['response_format']=={'type':'json_object'} for p in payloads));self.assertEqual(self.store.unfinished(),[])

if __name__=='__main__':unittest.main()
