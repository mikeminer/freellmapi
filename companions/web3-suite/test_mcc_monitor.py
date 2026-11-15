"""Conditional entry replay, virtual broker only; never load keys or /exchange."""
import copy,json,tempfile,unittest
from pathlib import Path
from unittest.mock import Mock,patch
from types import SimpleNamespace
from trading_engine import Engine,Store,TradingError,policy
from test_support_trading import FakeBroker
from trading_service import DEFAULT
from entry_monitor import Monitor,public_status,validate_trigger


class Crash(BaseException):pass


class MonitorTests(unittest.TestCase):
    def setUp(self):
        self.enterContext(patch('order_manager.OrderManager.live_receipt_verified',side_effect=lambda r:r.get('state')=='mock_filled'))
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        self.config=policy(copy.deepcopy(DEFAULT));self.now=1000.;self.mid='100'
        self.store=Store(self.home/'paper.sqlite');self.broker=FakeBroker(self.store,self.config)
        self.plan={'decision':'watch','coin':'BTC','side':'buy','size':'.1','entry':'100.1','take_profit':'101','stop_loss':'99','leverage':1,
                   'reason':'Synthetic conditional setup; no investment recommendation',
                   'trigger':{'kind':'breakout','direction':'above','level':'100.1','confirmation':'touch','ttl_seconds':3600}}
        self.planner=Mock(side_effect=lambda *args:copy.deepcopy(self.plan));self.regime='up';self.closed={}
        self.key=self.enterContext(patch('hl_credentials.get',side_effect=AssertionError('No real keys')))
        self.signer=self.enterContext(patch('hyperliquid_signer.execute',side_effect=AssertionError('No signing')))
        self.submit=self.enterContext(patch('hyperliquid_tools.submit',side_effect=AssertionError('No exchange orders')))
    def snapshot(self,coin='BTC'):
        from trading_engine import dec,number
        mid=dec(self.mid)
        return {'coin':coin,'retrieved_at':self.now,'mid':self.mid,'bid':number(mid-dec('.001')),'ask':number(mid+dec('.001')),
                'sz_decimals':3,'max_leverage':40,'closed_candles':copy.deepcopy(self.closed),
                'analysis':{'coin':coin,'profile':self.config['profile'],'network':self.config['network'],'as_of_ms':int(self.now*1000),'data_ready':True,'timeframes':{'15m':{'regime':self.regime},'1h':{'regime':self.regime}}}}
    def engine(self,hook=lambda phase:None,quote=None,reader=None,broker=None):
        return Engine(self.store,self.config,reader or self.snapshot,self.planner,broker or self.broker,lambda:self.now,hook,quote_reader=quote or self.snapshot)
    def arm(self,**trigger):
        self.plan['trigger'].update(trigger);engine=self.engine();ident=engine.tick()
        self.assertEqual(self.store.recent()[0]['phase'],'watching')
        return engine,ident
    def fills(self):
        with self.store.db() as db:return db.execute('SELECT COUNT(*) FROM fixture_fills').fetchone()[0]

    def test_waits_without_model_calls_then_executes_once_with_tp_sl(self):
        engine,ident=self.arm()
        for n in range(50):self.now+=1;engine.tick()
        self.assertEqual(self.fills(),0);self.assertEqual(self.planner.call_count,1)
        self.now+=1;self.mid='100.11';engine.tick()
        self.assertEqual(self.fills(),1);self.assertEqual(self.store.recent()[0]['phase'],'done')
        position=self.store.get('fixture_account')['positions'][0]
        self.assertEqual(position['intent'],ident);self.assertEqual(position['take_profit'],'101');self.assertEqual(position['stop_loss'],'99')
        for _ in range(10):engine.tick()
        self.assertEqual(self.fills(),1);self.assertEqual(self.planner.call_count,1)

    def test_short_breakout_and_both_pullback_sides(self):
        scenarios=[('sell','breakout','below','99.9','99.89','99','101'),
                   ('buy','pullback','below','99.9','99.89','101','99'),
                   ('sell','pullback','above','100.1','100.11','99','101')]
        for n,(side,kind,direction,level,price,tp,sl) in enumerate(scenarios):
            self.store=Store(self.home/f'scenario{n}.sqlite');self.broker=FakeBroker(self.store,self.config);self.mid='100'
            self.plan.update(side=side,entry=level,take_profit=tp,stop_loss=sl);self.regime='up' if side=='buy' else 'down'
            engine,_=self.arm(kind=kind,direction=direction,level=level)
            self.mid=price;self.now+=1;engine.tick();self.assertEqual(self.fills(),1)

    def test_restart_preserves_waiting_id_scope_levels_expiry_and_no_replanning(self):
        _,ident=self.arm();before=self.store.recent()[0]['data']['watch']
        self.store=Store(self.store.path);self.broker=FakeBroker(self.store,self.config)
        engine=self.engine();self.assertTrue(engine.recover());self.assertEqual(self.store.recent()[0]['data']['watch'],before)
        self.now+=2;engine.tick();self.assertEqual(self.planner.call_count,1);self.assertEqual(self.fills(),0)
        self.mid='100.11';self.now+=2;engine.tick()
        self.assertEqual(self.fills(),1);self.assertEqual(self.store.get('fixture_account')['positions'][0]['intent'],ident)

    def test_h24_virtual_clock_and_model_cooldown_do_not_block_saved_plan(self):
        engine,ident=self.arm(ttl_seconds=86400)
        self.planner.side_effect=AssertionError('LLM unavailable/cooldown; must not be called while waiting')
        for n in range(1,24):self.now=1000+n*3600;engine.tick()
        self.mid='100.11';self.now=1000+86390;engine.tick()
        self.assertEqual(self.fills(),1);self.assertEqual(self.store.get('fixture_account')['positions'][0]['intent'],ident)

    def test_expired_or_invalidated_setup_never_executes(self):
        engine,ident=self.arm(ttl_seconds=60);self.now+=61;self.mid='100.11';engine.tick()
        self.assertEqual(self.fills(),0)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT state FROM entry_watches WHERE id=?',(ident,)).fetchone()[0],'expired')
        self.store=Store(self.home/'invalid.sqlite');self.broker=FakeBroker(self.store,self.config);self.mid='100'
        engine,ident=self.arm();self.mid='98';self.now+=1;engine.tick();self.assertEqual(self.fills(),0)
        with self.store.db() as db:self.assertEqual(db.execute('SELECT state FROM entry_watches WHERE id=?',(ident,)).fetchone()[0],'invalidated')

    def test_gap_price_reversal_or_opposing_trend_invalidates_without_chasing(self):
        for n,kind in enumerate(('gap','reversal','regime')):
            self.store=Store(self.home/f'bad{n}.sqlite');self.broker=FakeBroker(self.store,self.config);self.mid='100';self.regime='up'
            engine,ident=self.arm();self.mid='100.11';self.now+=1
            if kind=='gap':self.mid='100.8'
            elif kind=='reversal':
                hit=self.snapshot();self.mid='100.05';engine=self.engine(quote=lambda c:hit)
            else:self.regime='down'
            engine.tick();self.assertEqual(self.fills(),0)
            with self.store.db() as db:self.assertEqual(db.execute('SELECT state FROM entry_watches WHERE id=?',(ident,)).fetchone()[0],'invalidated')

    def test_close_requires_new_closed_bar_and_current_confirmation(self):
        engine,_=self.arm(confirmation='close',interval='1m');self.mid='100.11';self.now+=1
        self.closed={'1m':{'T':int((self.now+60)*1000),'c':'100.11'}};engine.tick();self.assertEqual(self.fills(),0)
        self.now+=1;self.closed={'1m':{'T':999000,'c':'100.11'}};engine.tick();self.assertEqual(self.fills(),0)
        self.now+=2;self.closed={'1m':{'T':int((self.now-1)*1000),'c':'100.11'}};engine.tick();self.assertEqual(self.fills(),1)

    def test_retest_does_not_enter_on_breakout_and_survives_restart(self):
        engine,_=self.arm(confirmation='retest');self.mid='100.13';self.now+=1;engine.tick();self.assertEqual(self.fills(),0)
        self.assertEqual(public_status(self.store,self.now)['watches'][0]['confirmation_state'],'waiting_retest')
        engine=self.engine();engine.recover();self.mid='100.105';self.now+=1;engine.tick();self.assertEqual(self.fills(),1)

    def test_risk_loss_budget_and_authority_are_rechecked_after_wait(self):
        engine,ident=self.arm();account=self.store.get('fixture_account');account['cash']='980';self.store.set('fixture_account',account)
        self.mid='100.11';self.now+=1;engine.tick();self.assertEqual(self.fills(),0)
        self.assertIn('loss_stop',self.store.recent()[0]['data']['result']['reason'])

    def test_stop_during_trigger_validation_prevents_effect(self):
        engine,_=self.arm();self.mid='100.11';self.now+=1
        def stop(phase):
            if phase=='validated':(self.home/'STOP').write_text('requested')
        self.engine(hook=stop).tick();self.assertEqual(self.fills(),0)

    def test_crash_in_waiting_resumes_but_crossed_effect_boundaries_never_replay(self):
        for n,phase in enumerate(('watching','triggering','prepared','dispatching','effect_observed')):
            self.store=Store(self.home/f'crash{n}.sqlite');self.broker=FakeBroker(self.store,self.config);self.mid='100'
            def crash(name):
                if name==phase:raise Crash()
            engine=self.engine(hook=crash)
            if phase=='watching':
                with self.assertRaises(Crash):engine.tick()
            else:
                self.engine().tick();self.mid='100.11';self.now+=1
                with self.assertRaises(Crash):engine.tick()
            before=self.fills();self.store=Store(self.store.path);self.broker=FakeBroker(self.store,self.config)
            with patch.object(self.broker,'execute',side_effect=AssertionError('No replay on recovery')):
                self.assertTrue(self.engine().recover())
            self.assertEqual(self.fills(),before)
            if phase=='effect_observed':self.assertEqual(before,1)
            elif phase!='watching':self.assertEqual(before,0)

    def test_uncertain_send_pauses_and_does_not_rearm_or_resubmit(self):
        self.arm();self.mid='100.11';self.now+=1
        broker=Mock(wraps=self.broker,spec=FakeBroker);broker.execute.return_value={'state':'uncertain'}
        engine=self.engine(broker=broker);engine.tick()
        self.assertEqual(self.store.get('paused'),'uncertain_execution')
        for _ in range(100):self.now+=60;engine.tick()
        self.assertEqual(broker.execute.call_count,1);self.assertEqual(self.planner.call_count,1)

    def test_no_busy_loop_on_feed_outage_then_recovery_uses_same_watch(self):
        _,ident=self.arm();reader=Mock(side_effect=TimeoutError('fixture'));engine=self.engine(quote=reader)
        for n in range(3):self.now+=60;engine.tick()
        count=reader.call_count
        for _ in range(100):engine.tick()
        self.assertEqual(reader.call_count,count);self.assertEqual(self.fills(),0)
        self.assertEqual(public_status(self.store,self.now)['watches'][0]['confirmation_state'],'waiting_data')
        self.now+=61;self.mid='100.11';self.engine().tick();self.assertEqual(self.fills(),1)
        self.assertEqual(self.store.get('fixture_account')['positions'][0]['intent'],ident)

    def test_duplicate_setup_new_reason_or_ttl_cannot_recreate_consumed_intent(self):
        _,ident=self.arm();self.assertTrue(self.store.transition(ident,'watching','cancelled'))
        self.plan['reason']='A different model rephrased the same request';self.plan['trigger']['ttl_seconds']=7200
        new=self.store.create(self.config)
        with self.assertRaisesRegex(TradingError,'duplicate_setup'):Monitor(self.engine()).arm(new,self.plan,self.snapshot())
        self.assertEqual(self.fills(),0)

    def test_atomic_claim_and_version_one_state_migration(self):
        _,ident=self.arm();self.assertTrue(self.store.transition(ident,'watching','triggering'));self.assertFalse(self.store.transition(ident,'watching','triggering'))
        legacy={k:v for k,v in self.config.items() if k not in ('entry_mode','watch_poll_seconds','max_watch_seconds','max_trigger_gap_bps','require_trend_alignment')}
        upgraded=policy(legacy);self.assertEqual(upgraded['entry_mode'],'conditional');self.assertEqual(upgraded['max_order_usd'],legacy['max_order_usd'])
        reopened=Store(self.store.path);self.assertEqual(reopened.get('fixture_account'),self.store.get('fixture_account'))

    def test_watcher_cannot_open_immediately_with_already_reached_trigger(self):
        for patching in ({'direction':'below'},{'level':'100'},{'ttl_seconds':True},{'confirmation':'close','interval':'10m'}):
            plan=copy.deepcopy(self.plan);plan['trigger'].update(patching)
            with self.assertRaises(TradingError):validate_trigger(plan,self.snapshot(),self.config)
        self.mid='100.2'
        with self.assertRaisesRegex(TradingError,'already_reached'):validate_trigger(self.plan,self.snapshot(),self.config)

    def test_additive_schema_migration_preserves_legacy_balance_and_cycle_ids(self):
        import sqlite3
        path=self.home/'legacy.sqlite';connection=sqlite3.connect(path)
        connection.executescript('CREATE TABLE state(key TEXT PRIMARY KEY,value TEXT);CREATE TABLE cycles(id TEXT PRIMARY KEY,phase TEXT,created REAL,updated REAL,body TEXT);')
        connection.execute('INSERT INTO state VALUES(?,?)',('fixture_account',json.dumps({'cash':'123.45','positions':[]})))
        connection.execute('INSERT INTO cycles VALUES(?,?,?,?,?)',('legacy-id','done',1.,2.,json.dumps({'context_marker':'KEEP-ME'})));connection.commit();connection.close()
        reopened=Store(path)
        self.assertEqual(reopened.get('fixture_account')['cash'],'123.45')
        self.assertEqual(reopened.recent()[0]['id'],'legacy-id');self.assertEqual(reopened.recent()[0]['data']['context_marker'],'KEEP-ME')
        with reopened.db() as db:self.assertEqual(db.execute('SELECT COUNT(*) FROM entry_watches').fetchone()[0],0)

    def test_idle_sleep_prevention_is_removed_on_exit_without_display_lock(self):
        import ctypes,trading_runtime
        api=Mock(return_value=1)
        with patch.object(trading_runtime.os,'name','nt'),patch.object(ctypes.windll.kernel32,'SetThreadExecutionState',api):
            with trading_runtime.keep_awake() as active:self.assertTrue(active)
        self.assertEqual([c.args[0] for c in api.call_args_list],[0x80000001,0x80000000])


if __name__=='__main__':unittest.main()
