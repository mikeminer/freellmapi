"""Always-on monitor reuses account authority; no real keys, RPC or orders."""
import contextlib,copy,json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import Mock,patch
import trading_service as service,trading_runtime as runtime,hyperliquid_tools as h
from trading_engine import Store,TradingError

class SharedAccountServiceTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.home=Path(t.name)
        self.profile=h.configure(self.home,'mainnet-master','mainnet','0x'+'1'*40)
        self.cfg=copy.deepcopy(service.DEFAULT);service.save(self.home,self.cfg)
        self.key=self.enterContext(patch('hl_credentials.get',side_effect=AssertionError('No wallet keys')))
    def grant(self):
        return {'active':True,'session':str(self.home/'original-chat.json'),'scope':h.fingerprint(self.profile),'expires':time.time()+600,
          'stop_epoch':h.stop_epoch(self.home,'mainnet-master'),'network':'mainnet','coins':['BTC'],'profile':'mainnet-master','generation':'same-account-generation',
          'max_order_usd':'20','max_exposure_usd':'20','max_loss_usd':'10','max_turnover_usd':'200','max_leverage':1,'max_orders_hour':3,
          'strategy':'Synthetic account strategy only','interval':60}
    def test_broker_accepts_original_chat_account_grant_without_creating_another(self):
        g=self.grant();broker=runtime.LiveBroker(self.home,Store(self.home/'state.sqlite'),self.cfg)
        with patch.object(h,'current_grant',return_value=g):p,observed=broker.grant()
        self.assertEqual(observed['generation'],g['generation']);self.assertEqual(str(broker.agent.session),g['session'])
        self.assertEqual(observed['max_loss_usd'],'10');self.key.assert_not_called()
    def test_shared_account_scope_expiry_and_stop_are_still_enforced(self):
        broker=runtime.LiveBroker(self.home,Store(self.home/'state.sqlite'),self.cfg)
        for change in ({'expires':0},{'scope':'other-wallet'},{'stop_epoch':'old-stop'},{'active':False}):
            with self.subTest(change=change),patch.object(h,'current_grant',return_value={**self.grant(),**change}),self.assertRaises(TradingError):broker.grant()
        self.key.assert_not_called()
    def test_single_account_policy_sync_preserves_economic_values_and_does_not_arm_or_start(self):
        g=self.grant()
        with patch.object(service,'start') as start,patch.object(h,'arm') as arm:result=service.sync_account_policy(self.home,g)
        self.assertTrue(result['account_policy_shared']);self.assertFalse(result['separate_service_authorization'])
        self.assertEqual(service.load(self.home)['max_order_usd'],'20');self.assertEqual(service.load(self.home)['max_leverage'],1)
        start.assert_not_called();arm.assert_not_called()
    def test_start_keeps_existing_stop_and_does_not_consult_financial_grants(self):
        (service.folder(self.home)/'STOP').write_text('existing-stop')
        with patch.object(runtime.LiveBroker,'grant',side_effect=AssertionError('No permission request')),patch.object(service.subprocess,'Popen',return_value=Mock(pid=123)):
            result=service.start(self.home)
        self.assertTrue(result['started']);self.assertTrue((service.folder(self.home)/'STOP').exists());self.key.assert_not_called()
    def test_worker_heartbeats_without_authority_and_never_invokes_planner_or_execution(self):
        feed=Mock(connected=True,failures=0,last_error=None);feed.start.return_value=feed
        broker=Mock();broker.grant.side_effect=TradingError('account_authority_missing_or_expired')
        engine=Mock();counter=[0]
        def sleep(_seconds):
            counter[0]+=1
            if counter[0]>=2:(service.folder(self.home)/'SHUTDOWN').write_text('QA stop monitor')
        with patch.object(runtime,'LiveBroker',return_value=broker),patch.object(runtime,'Reader'),patch.object(runtime,'PublicFeed',return_value=feed),patch.object(runtime,'Engine',return_value=engine),patch.object(runtime,'keep_awake',return_value=contextlib.nullcontext(False)),patch.object(runtime.time,'sleep',side_effect=sleep),patch.object(service,'export_vault'):
            runtime.worker(self.home,self.cfg)
        engine.tick.assert_not_called();engine.recover.assert_called_once();feed.close.assert_called_once()
        state=Store(service.folder(self.home)/'live.sqlite');self.assertIsNotNone(state.get('heartbeat'));self.assertFalse(state.get('execution_readiness')['account_authorized'])
        self.assertEqual(counter[0],2);self.key.assert_not_called()

    def test_status_does_not_report_a_dead_worker_or_paused_execution_as_ready(self):
        store=runtime.Store(service.folder(self.home)/'live.sqlite')
        store.set('heartbeat',{'at':time.time(),'pid':123})
        with patch.object(service,'worker_running',return_value=False),patch.object(runtime.LiveBroker,'grant') as grant:
            self.assertEqual(service.status(self.home)['state'],'stopped_or_stale')
            store.set('paused','uncertain_execution')
            grant.reset_mock();state=service.status(self.home)
        self.assertFalse(state['real_orders_allowed']);self.assertTrue(state['execution_paused']);grant.assert_not_called()

    def test_strategy_can_change_under_stop_while_monitor_locks_remain_held(self):
        directory=service.folder(self.home);(directory/'STOP').write_text('existing-stop')
        before=service.load(self.home)
        with runtime.singleton(directory/'supervisor.lock'),runtime.singleton(directory/'worker.lock'):
            result=service.select_strategy(self.home,'rules')
        after=service.load(self.home)
        self.assertEqual(after['strategy_source'],'rules')
        self.assertEqual({k:v for k,v in after.items() if k!='strategy_source'},{k:v for k,v in before.items() if k!='strategy_source'})
        self.assertFalse(result['service_started']);self.assertFalse(result['grant_created']);self.key.assert_not_called()

if __name__=='__main__':unittest.main()
