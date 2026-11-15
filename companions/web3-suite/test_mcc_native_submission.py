"""Monitor -> real adapter -> isolated signer/SDK boundary, ALL RPC/keys mocked."""
import copy,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import hyperliquid_tools as h
import trading_service as service
from trading_runtime import LiveBroker
from trading_engine import Engine,Store,dec,number,TradingError,policy
from test_hyperliquid_leverage import LeverageFixture
from financial_completion import assess,render,requirement


class NativeMonitorTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.f=LeverageFixture(temp.name);self.addCleanup(self.f.close);self.f.leverage=1
        self.now=time.time();self.mid=dec('.01');self.outcome='filled'
        self.config=policy(copy.deepcopy(service.DEFAULT)|{'mode':'live','profile':'main','coins':['PUMP'],'max_turnover_usd':'20'})
        self.store=Store(Path(temp.name)/'trading-service/live.sqlite');self.broker=LiveBroker(self.f.home,self.store,self.config)
        h.arm(self.f.home,'main',self.f.settings,self.broker.session);service.save(self.f.home,self.config)
        self.plan={'decision':'watch','coin':'PUMP','side':'buy','size':'1000','entry':'.01001','take_profit':'.011','stop_loss':'.009','leverage':1,'reason':'Synthetic watch, no real funds',
                   'trigger':{'kind':'breakout','direction':'above','level':'.01001','confirmation':'touch','ttl_seconds':3600}}
        self.enterContext(patch.object(h,'info',side_effect=self.info))
        self.f.exchange.bulk_orders.side_effect=self.sdk
        self.planner=Mock(return_value=self.plan)
    def snapshot(self,coin):
        return {'coin':coin,'retrieved_at':self.now,'mid':number(self.mid),'bid':number(self.mid-dec('.000001')),'ask':number(self.mid+dec('.000001')),'sz_decimals':0,'max_leverage':40,
            'analysis':{'coin':coin,'profile':'main','network':'mainnet','as_of_ms':int(self.now*1000),'data_ready':True,'timeframes':{'15m':{'regime':'up'},'1h':{'regime':'up'}}}}
    def info(self,p,args):
        if args['type']=='allMids':return {'PUMP':number(self.mid)}
        if args['type']=='orderStatus':
            row=next(r for r in self.f.rows() if r['body']['action']=='order');body=row['body'];bracket=body['bracket']
            leg='entry' if args['oid']==body['cloid'] else 'tp' if args['oid']==bracket['tp_cloid'] else 'sl'
            order={'coin':'PUMP','side':'B' if leg=='entry' else 'A','sz':'0' if leg=='entry' else body['size'],'origSz':body['size'],'reduceOnly':leg!='entry'}
            if leg!='entry':order['triggerPx']=bracket['take_profit' if leg=='tp' else 'stop_loss']
            return {'status':'order','order':{'status':'filled' if leg=='entry' else 'open','order':order}}
        return self.f.info(p,args)
    def sdk(self,requests,**kwargs):
        if self.outcome=='uncertain':raise TimeoutError('synthetic upstream interruption')
        return {'status':'ok','response':{'type':'order','data':{'statuses':[{'filled':{'oid':100,'totalSz':'1000','avgPx':number(self.mid)}},{'error':'synthetic TP rejected'} if self.outcome=='tp_rejected' else 'waitingForTrigger','waitingForTrigger']}}}
    def engine(self):return Engine(self.store,self.config,self.snapshot,self.planner,self.broker,lambda:self.now)
    def arm_and_touch(self):
        engine=self.engine();ident=engine.tick();self.assertEqual(self.store.recent()[0]['phase'],'watching')
        self.f.exchange.bulk_orders.assert_not_called();self.now+=1;self.mid=dec('.010011');engine.tick()
        return engine,ident

    def test_trigger_sends_one_native_group_and_confirms_tp_sl(self):
        engine,ident=self.arm_and_touch()
        self.assertEqual(self.store.recent()[0]['phase'],'done');self.f.exchange.bulk_orders.assert_called_once();self.f.exchange.order.assert_not_called()
        args=self.f.exchange.bulk_orders.call_args;self.assertEqual(args.kwargs['grouping'],'normalTpsl');self.assertEqual(len(args.args[0]),3)
        self.assertTrue(all(x['reduce_only'] for x in args.args[0][1:]));self.assertTrue(all(not x['is_buy'] for x in args.args[0][1:]))
        receipt=next(r for r in self.f.rows() if r['body']['action']=='order')
        self.assertEqual(receipt['state'],'filled');self.assertEqual(receipt['response']['protection']['state'],'active')
        self.assertEqual(receipt['body']['trigger_bound']['reference'],'0.01001')
        self.assertLessEqual(dec(receipt['body']['price']),dec(receipt['body']['trigger_bound']['limit']))
        self.assertIsNone(self.broker.grant()[1]['pending']) # Regression: clear/prepare commits must survive DB reopen.
        before=self.f.exchange.bulk_orders.call_count
        self.assertTrue(self.engine().recover());self.engine().tick();self.assertEqual(self.f.exchange.bulk_orders.call_count,before)
        self.assertEqual(self.planner.call_count,1)

    def test_market_reversal_during_sdk_initialization_blocks_before_send(self):
        def initialization(*args,**kw):self.mid=dec('.01');return self.f.exchange
        with patch('hyperliquid.exchange.Exchange',side_effect=initialization):engine,ident=self.arm_and_touch()
        self.f.exchange.bulk_orders.assert_not_called()
        row=next(r for r in self.f.rows() if r['body']['action']=='order')
        self.assertEqual(row['state'],'blocked');self.assertEqual(row['response']['code'],'trigger_lost')
        self.assertEqual(self.store.recent()[0]['phase'],'invalidated');self.assertIsNone(self.broker.grant()[1]['pending'])

    def test_ambiguous_send_never_restarts_or_recreates_entry(self):
        self.outcome='uncertain';engine,ident=self.arm_and_touch()
        self.assertEqual(self.store.get('paused'),'uncertain_execution')
        for _ in range(30):self.now+=60;engine.tick()
        self.assertEqual(self.f.exchange.bulk_orders.call_count,1)
        row=next(r for r in self.f.rows() if r['body']['action']=='order');self.assertEqual(row['state'],'uncertain')

    def test_rejected_tp_pauses_new_entries_and_preserves_original_receipt(self):
        self.outcome='tp_rejected';engine,ident=self.arm_and_touch()
        self.assertEqual(self.store.get('paused'),'uncertain_execution')
        for _ in range(20):self.now+=60;engine.tick()
        self.assertEqual(self.f.exchange.bulk_orders.call_count,1)
        row=next(r for r in self.f.rows() if r['body']['action']=='order');self.assertEqual(row['state'],'partial')
        self.assertEqual(self.broker.config['max_order_usd'],'20')

    def test_reauthorizing_does_not_reuse_the_old_watch(self):
        engine=self.engine();ident=engine.tick()
        h.arm(self.f.home,'main',self.f.settings,self.broker.session)
        self.now+=1;self.mid=dec('.010011');engine.tick()
        self.f.exchange.bulk_orders.assert_not_called()
        with self.store.db() as db:self.assertEqual(db.execute('SELECT state FROM entry_watches WHERE id=?',(ident,)).fetchone()[0],'invalidated')

    def test_cancel_waiting_intent_does_not_cancel_existing_exchange_orders(self):
        ident=self.engine().tick()
        result=service.monitor_action(SimpleNamespace(mode='auto',depth=0),self.f.home,{'action':'cancel','id':ident})
        self.assertTrue(result['cancelled']);self.assertFalse(result['orders_cancelled'])
        self.f.exchange.cancel.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()

    def test_monitor_start_preserves_the_shared_chat_grant_without_creating_consent(self):
        h.arm(self.f.home,'main',self.f.settings,self.f.home/'chat-session.json')
        with h.database(self.f.home) as db:before=h.current_grant(db,'main')
        with patch('subprocess.Popen',return_value=Mock(pid=123)) as process,patch('hl_credentials.get') as key:
            started=service.start(self.f.home,'live')
        self.assertTrue(started['started']);self.assertFalse(started['real_orders_enabled_by_start'])
        with h.database(self.f.home) as db:self.assertEqual(before,h.current_grant(db,'main'))
        process.assert_called_once();key.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()

    def test_native_watch_completion_is_distinct_from_an_immediate_order(self):
        engine=self.engine();ident=engine.tick()
        agent=SimpleNamespace(financial_turn_started_at=self.now-5)
        request={'coin':'PUMP','venue':'hyperliquid','execution_action':'watch'}
        evidence=[{'tool':'hyperliquid_monitor','arguments':{'action':'watch'},'result':{'id':ident,'state':'watching'}}]
        result=assess(agent,request,evidence,self.f.home)
        self.assertTrue(result['verified']);self.assertEqual(result['state'],'watching');self.assertIn('non è un ordine già eseguito',render(result))
        self.assertEqual(requirement('Trada PUMP su Hyperliquid H24 aspettando il breakout',request),'watch')
        self.assertIsNone(requirement('Solo analisi del breakout PUMP, non inviare ordini',request))
        self.f.exchange.bulk_orders.assert_not_called()


if __name__=='__main__':unittest.main()
