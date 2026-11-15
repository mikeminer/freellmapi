"""Retired feature and exact Seeker ETH failure; no wallet/network writes."""
import json,os,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import trading_service as service,trading_engine as engine,hyperliquid_tools as h
import pump_service,pump_local

class RemovedPaperTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.home=Path(t.name)
        self.agent=SimpleNamespace(mode='auto',depth=0,hyperliquid_controller=None,approve=Mock(return_value=True))
        self.key=self.enterContext(patch('hl_credentials.get',side_effect=AssertionError('No keys')))
    def test_legacy_virtual_config_is_disabled_without_risk_or_history_loss(self):
        raw={**service.DEFAULT,'mode':'paper','initial_equity':'1000'}
        path=service.folder(self.home)/'config.json';path.write_text(json.dumps(raw))
        store=engine.Store(service.folder(self.home)/'paper.sqlite');store.set('context_marker','KEEP-ETH-1812')
        before=path.read_bytes();cfg=service.load(self.home);state=service.status(self.home)
        self.assertEqual(cfg['mode'],'live');self.assertNotIn('initial_equity',cfg)
        self.assertEqual(cfg['max_order_usd'],'20');self.assertEqual(cfg['max_leverage'],1)
        self.assertFalse(state['real_orders_allowed']);self.assertFalse(state['separate_service_authorization']);self.assertNotIn('paper_account',state)
        self.assertEqual(path.read_bytes(),before);self.assertEqual(store.get('context_marker'),'KEEP-ETH-1812')
    def test_latest_eth_25x_plan_is_not_armed_or_used_to_raise_limits(self):
        plan={'decision':'watch','coin':'ETH','side':'buy','size':'0.11','entry':'1812','take_profit':'1845','stop_loss':'1802','leverage':25,'reason':'Reproduction only',
              'trigger':{'kind':'pullback','direction':'below','level':'1812','confirmation':'touch','ttl_seconds':43200}}
        service.save(self.home,service.DEFAULT)
        with patch('trading_runtime.Reader',side_effect=AssertionError('No market needed')),patch.object(service,'start') as start,patch.object(h,'arm') as arm:
            result=h.dispatch(self.agent,'hyperliquid_monitor',{'action':'watch','plan':json.dumps(plan)},self.home)
        self.assertEqual(result['reason'],'watch_coin_outside_service_policy');self.assertFalse(result['retryable']);self.assertFalse(result['sent'])
        self.assertNotIn('PAPER',result['recovery']);self.assertFalse(result['service_policy']['service_will_start_on_valid_watch'])
        self.assertEqual(service.load(self.home)['max_order_usd'],'20');self.assertEqual(service.load(self.home)['max_leverage'],1)
        start.assert_not_called();arm.assert_not_called()
    def test_cli_and_model_no_longer_start_virtual_trading(self):
        from freellmapi import TOOLS
        spec=next(t for t in TOOLS if t['function']['name']=='hyperliquid_service')
        self.assertEqual(spec['function']['parameters']['properties']['action']['enum'],['status','start','stop'])
        for old in ('paper','start paper','configure paper','soak status'):
            with patch.object(service,'start') as start:
                output=[];self.assertTrue(service.command(self.agent,'/trading '+old,self.home,output.append))
            start.assert_not_called();self.assertNotIn('/trading paper',output[0])
        with patch.object(service.subprocess,'Popen') as child,self.assertRaises(service.TradingError):service.start(self.home,'paper')
        child.assert_not_called()
    def test_no_production_fake_broker_or_fake_receipt_success(self):
        from order_manager import OrderManager
        self.assertFalse(hasattr(engine,'PaperBroker'));self.assertFalse(hasattr(pump_service.Engine,'record_paper'))
        self.assertFalse(OrderManager.live_receipt_verified({'state':'paper_filled','real_order_sent':False}))
        self.assertFalse(hasattr(service,'configure_paper'));self.assertFalse(hasattr(service,'configure_paper_markets'))
    def test_retired_global_latch_never_blocks_live_authorization(self):
        import paper_soak_guard,paper_soak
        (service.folder(self.home)/'PAPER_ONLY').write_text('legacy latch')
        self.assertFalse(paper_soak_guard.active(self.home));paper_soak_guard.require_paper(self.home,'live')
        self.assertEqual(paper_soak.current(self.home)['status'],'removed')
        for action in (paper_soak.start,paper_soak.install,paper_soak.run):
            with self.assertRaisesRegex(ValueError,'paper_trading_removed'):action(self.home)
    def test_read_only_inference_still_blocks_keys_and_sends(self):
        import hyperliquid_signer
        with patch.dict(os.environ,{'FREELLMAPI_READ_ONLY':'1'}):
            with self.assertRaises(h.HLProblem):h.submit(self.agent,self.home,'missing')
            self.assertEqual(hyperliquid_signer.execute(self.home,'missing')['code'],'read_only_signing_forbidden')
            with self.assertRaisesRegex(pump_local.PumpError,'read_only'):pump_local.execute(self.home,'missing')
        self.key.assert_not_called()
    def test_analysis_exposes_current_monitor_limits_before_a_model_plans(self):
        service.save(self.home,service.DEFAULT)
        with patch('hyperliquid_analysis.analyze',return_value={'profile':'test','coin':'ETH'}),patch.object(h,'profile',return_value={'name':'test','network':'mainnet'}),patch.object(h,'fingerprint',return_value='public-scope'):
            result=h.dispatch(self.agent,'hyperliquid_analyze',{'coin':'ETH'},self.home)
        scope=result['execution_policy'];self.assertEqual(scope['mode'],'live');self.assertEqual(scope['risk_limits']['max_leverage'],1)
        self.assertEqual(scope['risk_limits']['max_order_usd'],'20');self.assertFalse(scope['coin_allowed']);self.assertIn('For conditional/H24',result['planning_instruction'])

if __name__=='__main__':unittest.main()
