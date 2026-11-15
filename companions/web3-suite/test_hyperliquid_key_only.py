"""Credential-only execution: fake keys/SDK/account, no network or real orders."""
import json,tempfile,time,unittest
from pathlib import Path
from unittest.mock import patch
import hyperliquid_tools as h,hyperliquid_execution as execution,hyperliquid_authority as authority
import trading_service as service,trading_runtime as runtime
from test_hyperliquid_leverage import LeverageFixture

class KeyOnlyTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.f=LeverageFixture(temp.name);self.addCleanup(self.f.close);self.f.leverage=1;self.f.cash='1000'
        self.agent=self.f.agent;self.agent.hyperliquid_controller=None;self.agent.mode='auto'
        authority.enable(self.f.home,'main',loss_limit=None)
        self.agent.messages=[{'role':'user','content':'dammi un signal per PUMP ed eseguilo'}]
        self.agent.current_market_request={'coin':'PUMP','venue':'hyperliquid','execution_action':'order'}
        self.agent.financial_turn_started_at=time.time()-1;execution.bind_request(self.agent)
        p=h.profile(self.f.home,'main')
        self.agent.hyperliquid_analysis={('main','PUMP'):{'data_ready':True,'evidence_id':'synthetic','as_of_ms':int(time.time()*1000),'scope':h.fingerprint(p)}}
        self.plan={'decision':'enter','coin':'PUMP','side':'buy','size':'3000','entry':'0.01','take_profit':'0.011','stop_loss':'0.009','leverage':1,'reason':'Synthetic model-managed plan'}
        self.args={'profile':'main','plan':json.dumps(self.plan),'order_kind':'limit','bracket_slippage_bps':30}
    def run_plan(self,**changes):return execution.execute(self.agent,self.f.home,self.args|changes)
    def test_no_local_quantity_leverage_loss_or_coin_grants_required(self):
        cfg=service.load(self.f.home);cfg['coins']=['BTC'];service.save(self.f.home,cfg)
        before=self.f.grant()
        with patch.object(h,'guard_grant',side_effect=AssertionError('No legacy policy')):
            result=self.run_plan()
        self.assertEqual(result['receipt']['state'],'accepted');self.assertEqual(result['receipt']['body']['size'],'3000')
        self.f.exchange.bulk_orders.assert_called_once();self.assertEqual(self.f.grant(),before)
        self.assertGreater(float(result['receipt']['body']['notional_usd']),float(before['max_order_usd']))
    def test_legacy_grant_may_be_absent_and_no_new_grant_is_created(self):
        with h.database(self.f.home) as db:db.execute('DELETE FROM grants');db.commit()
        result=self.run_plan();self.assertEqual(result['receipt']['state'],'accepted')
        with h.database(self.f.home) as db:self.assertEqual(db.execute('SELECT count(*) FROM grants').fetchone()[0],0)
    def test_leverage_is_set_once_then_verified_before_entry_without_local_cap(self):
        self.f.exchange.bulk_orders.side_effect=lambda *a,**k:{'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':7}},'waitingForFill','waitingForFill']}}}
        result=self.run_plan(plan=json.dumps(self.plan|{'leverage':3}))
        self.assertEqual(self.f.leverage,3);self.f.exchange.update_leverage.assert_called_once_with(3,'PUMP',True)
        self.f.exchange.bulk_orders.assert_called_once();self.assertEqual(result['receipt']['body']['expected_leverage'],3)
        again=self.run_plan(plan=json.dumps(self.plan|{'leverage':3}));self.assertTrue(again['reused'])
        self.f.exchange.update_leverage.assert_called_once();self.f.exchange.bulk_orders.assert_called_once()
    def test_exchange_leverage_limit_remains_enforced_before_signing(self):
        result=self.run_plan(plan=json.dumps(self.plan|{'leverage':41}))
        self.assertEqual(result['code'],'leverage_invalid');self.f.exchange.update_leverage.assert_not_called();self.f.exchange.bulk_orders.assert_not_called();self.f.secret.assert_not_called()
    def test_acknowledged_but_unapplied_leverage_never_opens_or_repeats(self):
        self.f.apply=False
        result=self.run_plan(plan=json.dumps(self.plan|{'leverage':3}))
        self.assertEqual(result['code'],'leverage_not_applied');self.assertIsNotNone(result['operation_id'])
        for _ in range(5):self.assertTrue(self.run_plan(plan=json.dumps(self.plan|{'leverage':3}))['reused'])
        self.f.exchange.update_leverage.assert_called_once();self.f.exchange.bulk_orders.assert_not_called()
    def test_actual_exchange_capacity_is_still_enforced(self):
        result=self.run_plan(plan=json.dumps(self.plan|{'size':'100001'}))
        self.assertEqual(result['code'],'exchange_size_cap');self.f.exchange.bulk_orders.assert_not_called();self.f.secret.assert_not_called()
    def test_old_loss_stop_is_not_applied_in_key_only_mode(self):
        self.f.cash='5';self.f.leverage=3
        self.f.exchange.bulk_orders.side_effect=lambda *a,**k:{'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':7}},'waitingForFill','waitingForFill']}}}
        base=self.f.info
        def info(p,args):
            result=base(p,args)
            if args['type']=='activeAssetData':result['maxTradeSzs']=['1500','1500']
            return result
        with patch.object(h,'info',side_effect=info):result=self.run_plan(plan=json.dumps(self.plan|{'size':'1000','leverage':3}))
        self.assertEqual(result['receipt']['state'],'accepted');self.f.exchange.bulk_orders.assert_called_once()
    def test_changed_selected_account_never_sends_on_the_old_profile(self):
        h.configure(self.f.home,'other','mainnet','0x'+'3'*40)
        result=self.run_plan()
        self.assertEqual(result['code'],'account_scope_changed');self.f.exchange.bulk_orders.assert_not_called();self.f.secret.assert_not_called()
    def test_key_possession_does_not_start_orders_for_an_analysis_request(self):
        self.agent.current_market_request=None;self.agent.messages=[{'role':'user','content':'analizza PUMP, non inviare ordini'}]
        result=h.dispatch(self.agent,'hyperliquid_execute_plan',self.args,self.f.home)
        self.assertEqual(result['code'],'execution_not_requested');self.f.exchange.bulk_orders.assert_not_called()
    def test_old_preview_generation_cannot_be_authorized_by_new_mode(self):
        row=h.preview(self.agent,self.f.home,{'profile':'main','action':'order','coin':'PUMP','side':'buy','size':'1000','order_kind':'limit','price':'0.01','take_profit':'0.011','stop_loss':'0.009','bracket_slippage_bps':30})['preview']
        with h.database(self.f.home) as db:
            body=row['body'];body.pop('execution_generation');db.execute('UPDATE actions SET body=? WHERE id=?',(json.dumps(body),row['id']));db.commit()
        result=h.dispatch(self.agent,'hyperliquid_submit',{'id':row['id']},self.f.home)
        self.assertEqual(result['code'],'new_request_required');self.f.exchange.bulk_orders.assert_not_called();self.f.secret.assert_not_called()
    def test_connection_does_not_restart_legacy_controller_or_autoplan(self):
        self.assertFalse(service.load(self.f.home)['plan_new'])
        self.assertIsNone(h.poll(self.f.home,self.agent.session))
        self.f.exchange.bulk_orders.assert_not_called();self.f.exchange.update_leverage.assert_not_called()
    def test_legacy_inflight_authorization_cannot_cross_mode_change(self):
        import hashlib,hyperliquid_signer
        row=h.preview(self.agent,self.f.home,{'profile':'main','action':'order','coin':'PUMP','side':'buy','size':'1000','order_kind':'limit','price':'0.01','take_profit':'0.011','stop_loss':'0.009','bracket_slippage_bps':30})['preview']
        auth={'mode':'auto','scope':row['body']['scope'],'expires':time.time()+30,'review_hash':hashlib.sha256(json.dumps(row['body'],sort_keys=True).encode()).hexdigest()}
        with h.database(self.f.home) as db:db.execute("UPDATE actions SET state='authorized',response=? WHERE id=?",(json.dumps({'authorization':auth}),row['id']));db.commit()
        result=hyperliquid_signer.execute(self.f.home,row['id'])
        self.assertEqual(result['code'],'new_request_required');self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()
    def test_monitor_accepts_coin_outside_scan_list_without_financial_form(self):
        cfg=service.load(self.f.home);cfg['coins']=['BTC'];service.save(self.f.home,cfg)
        result=service.monitor_preflight(self.f.home,'PUMP')
        self.assertTrue(result['coin_allowed']);self.assertEqual(result['allowed_coins'],['*']);self.assertNotIn('watch_coin_outside_service_policy',result['blockers'])
        self.assertEqual(result['risk_limits']['leverage'],'exchange');self.assertIsNone(result['risk_limits']['loss_limit_usd'])
        state=service.status(self.f.home);self.assertIsNone(state['policy']);self.assertNotIn('max_leverage',state['effective_policy'])
        self.assertEqual(state['effective_policy']['scan_coins'],['BTC'])
    def test_persistent_broker_reuses_key_without_legacy_grant_or_caps(self):
        with h.database(self.f.home) as db:db.execute('DELETE FROM grants');db.commit()
        broker=runtime.LiveBroker(self.f.home,runtime.Store(service.folder(self.f.home)/'live.sqlite'),service.load(self.f.home))
        p,permission=broker.grant();self.assertEqual(permission['mode'],'key_only');self.assertIsNone(permission['expires'])
        self.assertNotIn('max_order_usd',permission);self.assertIsNone(broker.watch_scope()['expires']);self.f.secret.assert_not_called()
    def test_old_context_cannot_arm_watch_after_connection_change(self):
        from work_ledger import load,save
        value=load(self.agent.session);value['financial_request']['created']=0;save(self.agent.session,value)
        self.agent.messages=[{'role':'user','content':'monitora PUMP e attendi il livello di ingresso'}]
        with patch.object(service,'Reader',create=True),patch.object(runtime,'Reader',side_effect=AssertionError('No new market work for old request')):
            with self.assertRaises(h.HLProblem) as caught:service.monitor_action(self.agent,self.f.home,{'action':'watch','plan':json.dumps(self.plan)})
        self.assertEqual(caught.exception.code,'new_request_required');self.f.exchange.bulk_orders.assert_not_called()
    def test_analysis_cannot_arm_a_watch_implicitly(self):
        self.agent.messages=[{'role':'user','content':'analizza PUMP, non inviare ordini'}]
        with self.assertRaises(h.HLProblem) as caught:service.monitor_action(self.agent,self.f.home,{'action':'watch','plan':json.dumps(self.plan)})
        self.assertEqual(caught.exception.code,'execution_not_requested');self.f.exchange.bulk_orders.assert_not_called()
    def test_current_effective_policy_does_not_reintroduce_old_caps_into_memory(self):
        value=service.effective_policy(service.load(self.f.home))
        self.assertNotIn('max_loss_usd',value);self.assertNotIn('max_leverage',value);self.assertNotIn('max_order_usd',value)
        self.assertIsNone(value['loss_limit_usd']);self.assertEqual(value['quantity_limit'],'exchange')
    def test_adapter_ui_has_only_credential_fields_and_preserves_key(self):
        from hyperliquid_panel import Panel
        self.assertFalse(hasattr(Panel,'authorize'));self.assertFalse(hasattr(Panel,'settings'));self.assertFalse(hasattr(Panel,'save_draft'))
        self.assertIsNone(h.profile(self.f.home,'main')['execution_authority']['loss_limit_usd'])

import test_mcc_native_submission as monitor_fixture

class KeyOnlyMonitorTests(unittest.TestCase):
    setUp=monitor_fixture.NativeMonitorTests.setUp
    snapshot=monitor_fixture.NativeMonitorTests.snapshot
    info=monitor_fixture.NativeMonitorTests.info
    sdk=monitor_fixture.NativeMonitorTests.sdk
    engine=monitor_fixture.NativeMonitorTests.engine
    def switch_mode(self):
        authority.enable(self.f.home,'main',loss_limit=None);self.f.cash='1000'
        self.config=service.load(self.f.home);self.config['coins']=['BTC'];service.save(self.f.home,self.config)
        self.broker=runtime.LiveBroker(self.f.home,self.store,self.config)
    def test_explicit_watch_outside_scan_list_triggers_once_without_old_grant(self):
        from entry_monitor import Monitor
        self.switch_mode()
        with h.database(self.f.home) as db:db.execute('DELETE FROM grants');db.commit()
        engine=self.engine();ident=self.store.create(self.config)
        Monitor(engine).arm(ident,self.plan,self.snapshot('PUMP'))
        self.assertEqual(self.store.recent()[0]['phase'],'watching');self.f.exchange.bulk_orders.assert_not_called()
        self.now+=1;self.mid=runtime.dec('.010011');engine.tick()
        self.assertEqual(self.store.recent()[0]['phase'],'done');self.f.exchange.bulk_orders.assert_called_once()
        self.assertEqual(next(r for r in self.f.rows() if r['body']['action']=='order')['response']['protection']['state'],'active')
        for _ in range(5):self.now+=60;engine.tick()
        self.f.exchange.bulk_orders.assert_called_once();self.planner.assert_not_called()
    def test_connection_change_does_not_reactivate_legacy_waiting_watch(self):
        old=self.engine();old.tick();self.assertEqual(self.store.recent()[0]['phase'],'watching')
        self.store.set('context_marker','KEEP-ORIGINAL-CONTEXT');self.switch_mode();self.engine().recover()
        self.assertEqual(self.store.recent()[0]['phase'],'invalidated');self.assertEqual(self.store.get('context_marker'),'KEEP-ORIGINAL-CONTEXT')
        self.f.exchange.bulk_orders.assert_not_called()

if __name__=='__main__':unittest.main()
