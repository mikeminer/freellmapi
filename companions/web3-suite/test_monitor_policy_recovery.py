"""Reproduce PUMP rejected by BTC-only policy; never arm, sign, start or trade."""
import copy,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hyperliquid_tools as h
import trading_service as s
from trading_engine import Store


class MonitorPolicyRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        self.config={**copy.deepcopy(s.DEFAULT),'mode':'live'};s.save(self.home,self.config)
        (s.folder(self.home)/'STOP').write_text('user-stopped')
        self.enterContext(patch.object(s,'live_authorized',return_value=True))
        self.agent=SimpleNamespace(mode='ask',depth=0,hyperliquid_controller=None)
        self.plan={'decision':'watch','coin':'PUMP','side':'buy','size':'10','entry':'.003',
                   'take_profit':'.0031','stop_loss':'.0029','leverage':1,'reason':'Synthetic scope test only',
                   'trigger':{'kind':'breakout','direction':'above','level':'.003','confirmation':'touch','ttl_seconds':3600}}
        self.reader=self.enterContext(patch('trading_runtime.Reader',side_effect=AssertionError('No market I/O on a blocked scope')))
        self.start=self.enterContext(patch.object(s,'start',side_effect=AssertionError('Do not start a blocked watch')))
        self.signer=self.enterContext(patch('hyperliquid_signer.execute',side_effect=AssertionError('No signing')))
        self.grant=self.enterContext(patch.object(h,'arm',side_effect=AssertionError('No new authorization')))

    def test_reproduced_pump_error_has_saved_scope_and_correct_recovery(self):
        result=h.dispatch(self.agent,'hyperliquid_monitor',{'action':'watch','plan':json.dumps(self.plan)},self.home)
        self.assertFalse(result['ok']);self.assertEqual(result['code'],'monitor_policy_mismatch')
        self.assertFalse(result['sent']);self.assertFalse(result['retryable']);self.assertTrue(result['requires_user_action'])
        self.assertEqual(result['reason'],'watch_coin_outside_service_policy')
        scope=result['service_policy'];self.assertEqual(scope['allowed_coins'],['BTC']);self.assertEqual(scope['mode'],'live')
        self.assertEqual(scope['risk_limits']['max_exposure_usd'],'20');self.assertEqual(scope['risk_limits']['max_loss_usd'],'10')
        self.assertEqual(scope['risk_limits']['max_leverage'],1);self.assertFalse(scope['scope_ready'])
        self.assertEqual(result['recovery_tools'],[{'name':'hyperliquid_monitor','arguments':{'action':'preflight','coin':'PUMP'}}])
        self.assertNotIn('PAPER',result['message']);self.assertIn('BTC',result['message'])

    def test_preflight_is_read_only_without_database_creation_or_authority(self):
        path=s.folder(self.home)/'live.sqlite';before=(s.folder(self.home)/'config.json').read_bytes()
        result=s.monitor_action(self.agent,self.home,{'action':'preflight','coin':'PUMP'})
        self.assertEqual(result['blockers'],['watch_coin_outside_service_policy','account_authority_missing_or_expired']);self.assertFalse(result['armed'])
        self.assertFalse(path.exists());self.assertEqual(result['orders_submitted'],0)
        self.assertEqual((s.folder(self.home)/'config.json').read_bytes(),before)
        self.assertEqual((s.folder(self.home)/'STOP').read_text(),'user-stopped')

    def test_repeated_blocked_requests_do_not_accumulate_intents_or_change_context(self):
        store=Store(s.folder(self.home)/'live.sqlite');store.set('context_marker',{'session':'KEEP','plan':'PUMP'})
        for _ in range(10):
            result=h.dispatch(self.agent,'hyperliquid_monitor',{'action':'watch','plan':json.dumps(self.plan)},self.home)
            self.assertFalse(result['retryable'])
        with store.db() as db:
            for table in ('cycles','entry_watches','order_dispatches'):self.assertEqual(db.execute('SELECT COUNT(*) FROM '+table).fetchone()[0],0)
        self.assertEqual(store.get('context_marker'),{'session':'KEEP','plan':'PUMP'});self.assertEqual(s.load(self.home),self.config)
        self.reader.assert_not_called();self.start.assert_not_called();self.signer.assert_not_called();self.grant.assert_not_called()

    def test_live_preflight_exposes_expired_consent_without_loading_signer(self):
        state={'policy':{**self.config,'mode':'live','coins':['PUMP']},'state':'stopped_or_stale','enabled':False,'real_orders_allowed':False}
        with patch.object(s,'status',return_value=state):result=s.monitor_preflight(self.home,'PUMP')
        self.assertEqual(result['blockers'],['account_authority_missing_or_expired']);self.assertFalse(result['scope_ready'])

    def test_preflight_without_coin_never_claims_a_plan_is_ready(self):
        result=s.monitor_preflight(self.home)
        self.assertIsNone(result['coin_allowed']);self.assertFalse(result['scope_ready']);self.assertFalse(result['armed'])

    def test_removed_paper_settings_cannot_change_live_scope(self):
        self.assertFalse(hasattr(s,'configure_paper_markets'))
        self.assertFalse(hasattr(s,'configure_paper'))
        self.assertEqual(s.load(self.home),self.config)

if __name__=='__main__':unittest.main()
