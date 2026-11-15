from test_support_trading import FakeBroker
"""Typed proposals, trusted strategies and one crash-safe entry manager."""
import copy,json,threading,unittest
from dataclasses import FrozenInstanceError
from types import SimpleNamespace
from unittest.mock import Mock,patch
import test_mcc_monitor as fixture
import test_mcc_native_submission as native_fixture
from trade_plan import TradePlan,PlanError
from order_manager import OrderManager
from trading_strategies import RulesStrategy,StrategyPlanner
from trading_engine import Engine,Store,TradingError
import trading_service as service


class UnifiedPipelineTests(unittest.TestCase):
    setUp=fixture.MonitorTests.setUp
    snapshot=fixture.MonitorTests.snapshot
    engine=fixture.MonitorTests.engine
    arm=fixture.MonitorTests.arm
    fills=fixture.MonitorTests.fills

    def enriched(self,coin='BTC'):
        snapshot=self.snapshot(coin);analysis=snapshot['analysis']
        snapshot['active_leverage']=1
        analysis['fees']={'userCrossRate':'.00045','userAddRate':'.00015'};analysis['derivatives']={'funding':'.00001'}
        for frame in analysis['timeframes'].values():frame.update(atr14_wilder='.5',resistance20='100.2',support20='99.8')
        return snapshot

    def test_typed_plan_is_immutable_versioned_and_normalizes_economic_values(self):
        plan=TradePlan.parse(self.plan)
        self.assertEqual(plan.size,'0.1');self.assertEqual(plan.envelope()['version'],1)
        with self.assertRaises(FrozenInstanceError):plan.size='500'
        with self.assertRaises(FrozenInstanceError):plan.trigger.level='0'
        self.assertEqual(TradePlan.parse(plan.to_dict()),plan)

    def test_rejects_untrusted_fields_invalid_numbers_and_mixed_hold_orders(self):
        for change in ({'private_key':'synthetic-never-allowed'},{'leverage':True},{'size':'NaN'},{'entry':'Infinity'},{'take_profit':'98'},{'side':'both'}):
            with self.subTest(change=list(change)),self.assertRaises(PlanError):TradePlan.parse(self.plan|change)
        with self.assertRaises(PlanError):TradePlan.parse({'decision':'hold','reason':'No setup','size':'1'})
        with self.assertRaises(PlanError):TradePlan.parse(TradePlan('hold','No setup',schema_version=99))

    def test_rule_strategy_arms_and_uses_the_same_manager_without_any_model(self):
        self.config['strategy_source']='rules';planner=StrategyPlanner(self.home,self.config)
        engine=Engine(self.store,self.config,self.enriched,planner,self.broker,lambda:self.now,quote_reader=self.enriched)
        ident=engine.tick();watch=self.store.recent()[0]['data']['watch'];plan=watch['plan']
        self.assertEqual(self.store.recent()[0]['phase'],'watching');self.assertEqual(planner.telemetry['requests'],0)
        self.assertEqual(self.store.recent()[0]['data']['proposal_source']['kind'],'rules')
        planner.impl=Mock(side_effect=AssertionError('Waiting must not replan'))
        self.mid=plan['entry'];self.now+=2
        self.closed={plan['trigger']['interval']:{'T':int((self.now-1)*1000),'c':plan['entry']}}
        engine.tick();self.assertEqual(self.fills(),1)
        with self.store.db() as db:
            row=db.execute('SELECT * FROM order_dispatches WHERE id=?',(ident,)).fetchone()
            self.assertEqual(row['state'],'done');self.assertEqual(json.loads(row['body'])['typed_plan']['version'],1)
        self.key.assert_not_called();self.signer.assert_not_called();self.submit.assert_not_called()

    def test_ai_strategy_uses_the_same_typed_contract_and_order_ledger(self):
        model=Mock(return_value=self.plan);model.telemetry={'model':'synthetic-backend','requests':1}
        with patch('trading_runtime.Planner',return_value=model):planner=StrategyPlanner(self.home,self.config)
        engine=Engine(self.store,self.config,self.snapshot,planner,self.broker,lambda:self.now)
        ident=engine.tick();self.now+=1;self.mid='100.11';engine.tick()
        self.assertEqual(model.call_count,1);self.assertEqual(self.fills(),1)
        body=self.store.recent()[0]['data'];self.assertEqual(body['proposal_source']['kind'],'ai');self.assertEqual(body['typed_plan']['version'],1)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM order_dispatches WHERE id=?',(ident,)).fetchone()[0],1)

    def test_rules_hold_for_missing_data_mixed_regime_or_subminimum_budget(self):
        rules=RulesStrategy();snapshot=self.enriched();account=self.broker.account(snapshot)
        self.assertEqual(rules(snapshot,account,self.config,[]).decision,'watch')
        mixed=copy.deepcopy(snapshot);mixed['analysis']['timeframes']['1h']['regime']='down'
        missing=copy.deepcopy(snapshot);missing['analysis']['fees']={}
        for data,config in ((mixed,self.config),(missing,self.config),(snapshot,self.config|{'max_order_usd':'5'})):
            self.assertEqual(rules(data,account,config,[]).decision,'hold')

    def test_duplicate_dispatch_rejected_before_rewriting_original_journal(self):
        engine,ident=self.arm();self.now+=1;self.mid='100.11';engine.tick();before=self.store.recent()[0]
        plan=before['data']['typed_plan']['plan']
        with self.assertRaisesRegex(TradingError,'entry_already_managed'):OrderManager(engine).dispatch(ident,plan,self.snapshot(),self.broker.account(self.snapshot()))
        self.assertEqual(self.fills(),1);self.assertEqual(self.store.recent()[0],before)

    def test_concurrent_claims_cannot_prepare_twice_or_replace_the_first_plan(self):
        self.config['entry_mode']='immediate';snapshot=self.snapshot();account=self.broker.account(snapshot)
        plan={'decision':'enter','coin':'BTC','side':'buy','size':'.1','entry':'100','take_profit':'101','stop_loss':'99','leverage':1,'reason':'Synthetic'}
        ident=self.store.create(self.config);self.store.phase(ident,'planned')
        entered=threading.Event();release=threading.Event();errors=[]
        broker=Mock(wraps=self.broker,spec=FakeBroker)
        def prepare(*args):entered.set();release.wait(4);return 'synthetic-prepared'
        broker.prepare=Mock(side_effect=prepare)
        engine=self.engine(broker=broker)
        def first():
            try:OrderManager(engine).dispatch(ident,plan,snapshot,account)
            except BaseException as exc:errors.append(exc)
        thread=threading.Thread(target=first);thread.start()
        try:
            self.assertTrue(entered.wait(3))
            with self.assertRaisesRegex(TradingError,'entry_already_managed'):OrderManager(engine).dispatch(ident,plan|{'take_profit':'102'},snapshot,account)
        finally:release.set();thread.join(5)
        self.assertFalse(thread.is_alive());self.assertEqual(errors,[]);broker.prepare.assert_called_once();broker.execute.assert_called_once()
        self.assertEqual(self.store.recent()[0]['data']['plan']['take_profit'],'101');self.assertEqual(self.fills(),1)

    def test_exception_after_effect_uses_receipt_instead_of_invalidating_success(self):
        _,ident=self.arm();self.now+=1;self.mid='100.11'
        def fail(phase):
            if phase=='effect_observed':raise RuntimeError('synthetic UI failure after fill')
        self.engine(hook=fail).tick()
        self.assertEqual(self.fills(),1);self.assertEqual(self.store.recent()[0]['phase'],'done')
        self.assertTrue(self.store.recent()[0]['data']['recovery']['receipt'])

    def test_unknown_strategy_cannot_import_or_run_arbitrary_code(self):
        with self.assertRaisesRegex(TradingError,'unknown_strategy_source'):StrategyPlanner(self.home,self.config|{'strategy_source':'some_plugin.py'})

    def test_strategy_choice_preserves_funds_and_limits_and_never_autostarts(self):
        service.save(self.home,self.config);service.folder(self.home).joinpath('STOP').write_text('stopped')
        before=copy.deepcopy(self.store.get('fixture_account'))
        with patch('subprocess.Popen') as process:
            selected=service.select_strategy(self.home,'rules')
        self.assertFalse(selected['service_started']);self.assertFalse(selected['grant_created']);process.assert_not_called()
        self.assertEqual(self.store.get('fixture_account'),before)
        config=service.load(self.home);self.assertEqual(config['max_order_usd'],self.config['max_order_usd']);self.assertEqual(config['strategy_source'],'rules')
        service.folder(self.home).joinpath('STOP').unlink()
        with self.assertRaisesRegex(TradingError,'stop_service_before'):service.select_strategy(self.home,'ai')


class NativeRecoveryTests(unittest.TestCase):
    setUp=native_fixture.NativeMonitorTests.setUp
    snapshot=native_fixture.NativeMonitorTests.snapshot
    info=native_fixture.NativeMonitorTests.info
    sdk=native_fixture.NativeMonitorTests.sdk
    engine=native_fixture.NativeMonitorTests.engine
    arm_and_touch=native_fixture.NativeMonitorTests.arm_and_touch

    def test_crash_before_parent_leg_verification_reconciles_without_resigning(self):
        import hyperliquid_tools as h
        engine,ident=self.arm_and_touch();row=next(r for r in self.f.rows() if r['body']['action']=='order')
        response=copy.deepcopy(row['response']);response.pop('protection')
        with h.database(self.f.home) as db:db.execute('UPDATE actions SET response=? WHERE id=?',(json.dumps(response),row['id']));db.commit()
        self.store.phase(ident,'dispatching');before=self.f.exchange.bulk_orders.call_count
        self.assertTrue(self.engine().recover());self.assertEqual(self.f.exchange.bulk_orders.call_count,before)
        native=next(r for r in self.f.rows() if r['id']==row['id'])
        self.assertEqual(native['response']['protection']['state'],'active');self.assertEqual(self.store.recent()[0]['phase'],'done')

    def test_recovery_does_not_certify_an_entry_missing_its_exit_receipts(self):
        import hyperliquid_tools as h
        engine,ident=self.arm_and_touch();row=next(r for r in self.f.rows() if r['body']['action']=='order')
        response=copy.deepcopy(row['response']);response.pop('protection');response['sdk_response']['response']['data']['statuses']=response['sdk_response']['response']['data']['statuses'][:1]
        with h.database(self.f.home) as db:db.execute('UPDATE actions SET response=? WHERE id=?',(json.dumps(response),row['id']));db.commit()
        self.store.phase(ident,'dispatching');self.assertFalse(self.engine().recover())
        self.assertEqual(self.store.recent()[0]['phase'],'paused');self.assertEqual(self.f.exchange.bulk_orders.call_count,1)

    def test_busy_native_manager_does_not_authorize_or_launch_signer(self):
        import hashlib,hyperliquid_tools as h
        from trading_runtime import singleton
        agent=SimpleNamespace(mode='auto',interactive=False,depth=0,session=self.broker.session,messages=[])
        row=h.preview(agent,self.f.home,{'profile':'main','action':'order','coin':'PUMP','side':'buy','size':'1000','order_kind':'limit','price':'.01','take_profit':'.011','stop_loss':'.009','bracket_slippage_bps':20})['preview']
        body=row['body'];key=hashlib.sha256((body['network']+body['account'].lower()).encode()).hexdigest()[:24]
        with singleton(h.folder(self.f.home)/('oms-'+key+'.lock')),patch('subprocess.run') as child:
            result=h.dispatch(agent,'hyperliquid_submit',{'id':row['id']},self.f.home)
        self.assertEqual(result['code'],'oms_busy');child.assert_not_called()
        with h.database(self.f.home) as db:self.assertEqual(db.execute('SELECT state FROM actions WHERE id=?',(row['id'],)).fetchone()[0],'prepared')


if __name__=='__main__':unittest.main()
