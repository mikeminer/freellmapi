"""Offline leverage gate: synthetic account, intercepted SDK and no network/real keys."""
import json,socket,tempfile,time,unittest
from contextlib import ExitStack
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
import hyperliquid_tools as h
import hyperliquid_signer as signer

MASTER='0x'+'1'*40
SIGNER='0x'+'2'*40

class LeverageFixture:
    def __init__(self,root):
        self.home=Path(root);self.stack=ExitStack();self.calls=[];self.messages=[]
        self.leverage=10;self.kind='cross';self.cash='20';self.apply=True;self.reject=False;self.timeout=False;self.fail_read=False
        self.raw={'status':'ok','response':{'type':'default'}}
        self.exchange=Mock();self.exchange.update_leverage.side_effect=self.update
        self.exchange.bulk_orders.side_effect=self.order
        self.stack.enter_context(patch('socket.create_connection',side_effect=AssertionError('Network forbidden in offline QA')))
        self.stack.enter_context(patch('hyperliquid_tools.info',side_effect=self.info))
        self.secret=self.stack.enter_context(patch('hl_credentials.get',return_value='synthetic-local-test-only'))
        self.stack.enter_context(patch('eth_account.Account.from_key',return_value=SimpleNamespace(address=SIGNER)))
        self.sdk=self.stack.enter_context(patch('hyperliquid.exchange.Exchange',return_value=self.exchange))
        self.worker=self.stack.enter_context(patch('subprocess.run',side_effect=self.run_worker))
        h.configure(self.home,'main','mainnet',MASTER)
        data=h.config(self.home);data['profiles']['main'].update(signer=SIGNER,signer_kind='agent');h.save_config(self.home,data)
        h.secret_path(self.home,'main').write_bytes(b'NO_REAL_KEY')
        self.agent=SimpleNamespace(mode='ask',interactive=False,depth=0,session=self.home/'session.json',messages=[{'role':'user','content':'PUMP / 20 USDC / 1x / stop 10'}],hyperliquid_controller='main')
        self.settings={'coins':'PUMP','max_order_usd':'20','max_exposure_usd':'20','max_loss_usd':'10','max_turnover_usd':'20','max_leverage':'1','max_orders_hour':'3','interval':'60','duration_minutes':'60','strategy_mode':'auto','strategy':''}
        self.arm()
    def close(self):self.stack.close()
    def grant(self):
        with h.database(self.home) as db:return h.current_grant(db,'main')
    def arm(self,**changes):
        h.arm(self.home,'main',self.settings|changes,self.agent.session)
        with h.database(self.home) as db:
            g=h.current_grant(db,'main');g['next_due']=0;db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(g),'main'));db.commit()
        assert h.poll(self.home,self.agent.session)=='/_hyperliquid main'
    def info(self,p,b):
        t=b['type'];self.calls.append(('read',t,b.get('coin')))
        if t=='userRole':return {'role':'agent','data':{'user':MASTER}} if b['user']==SIGNER else {'role':'user'}
        if t=='orderStatus':
            row=next(r for r in self.rows() if r['body']['action']=='order');body=row['body']
            if b['oid']==body['cloid']:return {'status':'order','order':{'status':'open','order':{'sz':body['size'],'origSz':body['size']}}}
            return {'status':'unknownOid'}
        if t=='userAbstraction':return 'unifiedAccount'
        if t=='perpDexs':return [None]
        if t in ('meta','allPerpMetas'):
            meta={'collateralToken':0,'universe':[{'name':'PUMP','szDecimals':0,'maxLeverage':40}]}
            return [meta] if t=='allPerpMetas' else meta
        if t=='spotClearinghouseState':return {'balances':[{'token':0,'coin':'USDC','total':self.cash,'hold':'0'}],'tokenToAvailableAfterMaintenance':[[0,self.cash]]}
        if t=='clearinghouseState':return {'marginSummary':{'accountValue':'0'},'assetPositions':[],'crossMaintenanceMarginUsed':'0'}
        if t in ('frontendOpenOrders','openOrders','userNonFundingLedgerUpdates'):return []
        if t=='activeAssetData':
            if self.fail_read:raise h.HLProblem('info_unavailable','Synthetic read failure')
            return {'leverage':{'type':self.kind,'value':self.leverage},'maxTradeSzs':['100000','100000'],'availableToTrade':[self.cash,self.cash]}
        if t=='allMids':return {'PUMP':'0.01'}
        if t=='metaAndAssetCtxs':return [self.info(p,{'type':'meta'}),[{'markPx':'0.01','midPx':'0.01'}]]
        if t=='candleSnapshot':return [{'T':int(time.time()*1000)-1000,'o':'0.01','h':'0.012','l':'0.009','c':'0.011','v':'1000'}]
        raise AssertionError('Unexpected info: '+t)
    def update(self,lev,coin,cross):
        self.calls.append(('update_leverage',lev,coin,cross))
        if self.timeout:raise TimeoutError('synthetic SDK timeout')
        if self.reject:return {'status':'err','response':'synthetic rejection'}
        if self.apply:self.leverage=lev
        return self.raw
    def order(self,*args,**kwargs):
        assert self.leverage==1,'Entry sent before verified 1x'
        self.calls.append(('bulk_orders',len(args[0]),kwargs.get('grouping')))
        return {'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':7}},'waitingForFill','waitingForFill']}}}
    def run_worker(self,cmd,**kw):
        request=json.loads(kw['input']);assert set(request)=={'id'}
        return SimpleNamespace(returncode=0,stdout=json.dumps(signer.execute(self.home,request['id'])))
    def preflight(self):return h.preflight_leverage(self.agent,self.home,'main',self.messages.append)
    def entry(self):
        return h.preview(self.agent,self.home,{'profile':'main','action':'order','coin':'PUMP','side':'buy','size':'1000','order_kind':'limit','price':'0.01','take_profit':'0.011','stop_loss':'0.009','bracket_slippage_bps':30})['preview']
    def rows(self):return h.operations(self.home,name='main')['operations']

class LeverageTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.f=LeverageFixture(self.temp.name);self.addCleanup(self.f.close)
    def test_native_reduces_once_verifies_then_sends_group_without_changing_limits(self):
        f=self.f;before=f.grant();result=f.preflight();row=f.entry();out=h.submit(f.agent,f.home,row['id'])
        self.assertEqual(result['PUMP']['observed'],{'type':'cross','value':1})
        self.assertEqual(out['receipt']['state'],'accepted');f.exchange.update_leverage.assert_called_once_with(1,'PUMP',True);f.exchange.bulk_orders.assert_called_once()
        events=[x[0] for x in f.calls];self.assertLess(events.index('update_leverage'),events.index('bulk_orders'))
        between=f.calls[events.index('update_leverage')+1:events.index('bulk_orders')]
        self.assertGreaterEqual(sum(x[:2]==('read','activeAssetData') for x in between),3)
        leverage=next(r for r in f.rows() if r['body']['action']=='leverage');self.assertTrue(leverage['response']['leverage_verification']['verified'])
        after=f.grant();self.assertEqual({k:v for k,v in after.items() if k!='leverage_preflight'},before)
        self.assertEqual(after['max_loss_usd'],'10');self.assertEqual(after['max_turnover_usd'],'20')
    def test_already_one_requires_no_signature_and_never_raises_lower_leverage(self):
        f=self.f;f.leverage=1;f.arm(max_leverage='2');self.assertEqual(f.preflight()['PUMP']['observed']['value'],1)
        f.sdk.assert_not_called();f.secret.assert_not_called();self.assertEqual(f.rows(),[])
    def test_preserves_isolated_mode(self):
        f=self.f;f.kind='isolated';f.preflight();f.exchange.update_leverage.assert_called_once_with(1,'PUMP',False)
        self.assertEqual(f.grant()['leverage_preflight']['PUMP']['observed']['type'],'isolated')
    def test_acknowledged_but_not_applied_stops_without_resending(self):
        f=self.f;f.apply=False
        with self.assertRaises(h.HLProblem) as e:f.preflight()
        self.assertEqual(e.exception.code,'leverage_not_applied');self.assertFalse(f.grant()['active']);self.assertEqual(f.rows()[0]['state'],'uncertain')
        self.assertFalse(f.rows()[0]['response']['leverage_verification']['verified'])
        with self.assertRaises(h.HLProblem):f.preflight()
        f.exchange.update_leverage.assert_called_once();f.exchange.bulk_orders.assert_not_called();self.assertIsNone(h.poll(f.home,f.agent.session))
    def test_sdk_timeout_or_rejection_never_repeats_or_opens_entry(self):
        f=self.f;f.timeout=True
        with self.assertRaises(h.HLProblem) as e:f.preflight()
        self.assertEqual(e.exception.code,'leverage_preflight_failed');self.assertEqual(f.rows()[0]['state'],'uncertain');self.assertFalse(f.grant()['active'])
        with self.assertRaises(h.HLProblem):f.preflight()
        f.exchange.update_leverage.assert_called_once();f.exchange.bulk_orders.assert_not_called()
    def test_sdk_rejection_stops_without_model_retry(self):
        f=self.f;f.reject=True
        with self.assertRaises(h.HLProblem):f.preflight()
        self.assertEqual(f.rows()[0]['state'],'rejected');self.assertFalse(f.grant()['active']);f.exchange.update_leverage.assert_called_once()
    def test_failed_post_read_does_not_treat_ack_as_verified(self):
        f=self.f;update=f.update
        def apply(*args):
            result=update(*args);f.fail_read=True;return result
        f.exchange.update_leverage.side_effect=apply
        with self.assertRaises(h.HLProblem):f.preflight()
        self.assertEqual(f.rows()[0]['state'],'uncertain');self.assertFalse(f.grant()['active']);f.exchange.bulk_orders.assert_not_called()
    def test_malformed_leverage_cannot_be_truncated_to_one(self):
        f=self.f
        for value in (None,True,'NaN','Infinity','1.9',0,-1):
            with self.subTest(value=value):
                f.leverage=value;f.arm()
                with self.assertRaises(h.HLProblem) as e:f.preflight()
                self.assertEqual(e.exception.code,'leverage_unverified');self.assertFalse(f.grant()['active'])
        f.sdk.assert_not_called();f.secret.assert_not_called()
    def test_unknown_margin_type_is_not_changed_to_cross(self):
        f=self.f;f.kind='unknown'
        with self.assertRaises(h.HLProblem):f.preflight()
        f.sdk.assert_not_called()
    def test_same_cycle_repeat_and_new_process_preserve_ids_context_and_limits(self):
        f=self.f;history=list(f.agent.messages);first=f.preflight();ident=first['PUMP']['operation_id']
        f.agent=SimpleNamespace(**f.agent.__dict__);second=f.preflight()
        self.assertEqual(second['PUMP']['operation_id'],ident);self.assertEqual(len(f.rows()),1)
        self.assertEqual(f.agent.messages,history);f.exchange.update_leverage.assert_called_once()
    def test_external_change_after_verified_preflight_does_not_trigger_second_update(self):
        f=self.f;f.preflight();f.leverage=10
        with self.assertRaises(h.HLProblem) as e:f.preflight()
        self.assertEqual(e.exception.code,'leverage_changed');self.assertFalse(f.grant()['active']);f.exchange.update_leverage.assert_called_once()
    def test_order_attempt_without_preflight_is_blocked_and_native_model_loop_stops(self):
        f=self.f;row=f.entry();result=h.dispatch(f.agent,'hyperliquid_submit',{'id':row['id']},f.home)
        self.assertEqual(result['code'],'existing_leverage_cap');self.assertFalse(f.grant()['active']);f.sdk.assert_not_called()
        self.assertIn('HOLD',h.reconcile_hazard(f.agent,f.home,'main'))
    def test_leverage_change_during_sdk_initialization_blocks_before_order_post(self):
        f=self.f;f.preflight();row=f.entry()
        def construct(*args,**kw):f.leverage=10;return f.exchange
        f.sdk.side_effect=construct;result=h.submit(f.agent,f.home,row['id'])
        self.assertEqual(result['receipt']['state'],'blocked');self.assertFalse(result['receipt']['response']['send_attempted'])
        self.assertEqual(result['receipt']['response']['code'],'existing_leverage_cap');self.assertFalse(f.grant()['active']);f.exchange.bulk_orders.assert_not_called()
    def test_loss_threshold_blocks_even_leverage_update_and_preserves_baseline(self):
        f=self.f;baseline=f.grant()['baseline_equity'];f.cash='10'
        with self.assertRaises(h.HLProblem) as e:f.preflight()
        self.assertEqual(e.exception.code,'loss_stop');self.assertFalse(f.grant()['active']);f.sdk.assert_not_called()
        self.assertEqual(f.grant()['baseline_equity'],baseline);self.assertEqual(f.grant()['max_loss_usd'],'10')
    def test_wrong_session_has_no_signing_or_change_to_other_sessions_grant(self):
        f=self.f;before=f.grant();f.agent.session=f.home/'other.json'
        with self.assertRaises(h.HLProblem) as e:f.preflight()
        self.assertEqual(e.exception.code,'auto_scope');f.sdk.assert_not_called();self.assertEqual(f.grant(),before)
    def test_stop_during_update_blocks_entry_despite_correct_server_leverage(self):
        f=self.f;update=f.update
        def apply(*args):
            result=update(*args);h.stop(f.home,'main');return result
        f.exchange.update_leverage.side_effect=apply
        with self.assertRaises(h.HLProblem) as e:f.preflight()
        self.assertEqual(e.exception.code,'auto_stopped');self.assertFalse(f.grant()['active']);f.exchange.bulk_orders.assert_not_called()
    def test_model_cannot_toggle_margin_type_or_repeat_a_verified_update(self):
        f=self.f;f.preflight()
        row=h.preview(f.agent,f.home,{'action':'leverage','coin':'PUMP','leverage':1,'cross':False})['preview']
        result=h.dispatch(f.agent,'hyperliquid_submit',{'id':row['id']},f.home)
        self.assertEqual(result['code'],'leverage_preflight_locked');self.assertFalse(f.grant()['active']);f.exchange.update_leverage.assert_called_once()
    def test_read_only_reconciliation_preserves_stopped_grant_and_never_resends(self):
        f=self.f;f.apply=False
        with self.assertRaises(h.HLProblem):f.preflight()
        ident=f.rows()[0]['id'];r=h.operations(f.home,ident)
        self.assertEqual(r['receipt']['state'],'uncertain');self.assertFalse(r['resent'])
        f.leverage=1;r=h.operations(f.home,ident)
        self.assertEqual(r['receipt']['state'],'acknowledged');self.assertTrue(r['receipt']['response']['leverage_verification']['verified'])
        self.assertFalse(f.grant()['active']);f.exchange.update_leverage.assert_called_once();f.exchange.bulk_orders.assert_not_called()
    def test_ctrl_c_stops_grant_and_preserves_attempt_before_worker_execution(self):
        f=self.f;f.worker.side_effect=KeyboardInterrupt
        with self.assertRaises(KeyboardInterrupt):f.preflight()
        self.assertFalse(f.grant()['active']);self.assertEqual(f.rows()[0]['state'],'authorized');f.sdk.assert_not_called()
    def test_incomplete_attempt_survives_restart_without_worker_retry(self):
        f=self.f
        with patch('hyperliquid_tools.submit',side_effect=RuntimeError('synthetic interruption before send')):
            with self.assertRaises(RuntimeError):f.preflight()
        self.assertEqual(f.rows()[0]['state'],'prepared')
        # Simulate process failure before the durable stop, retaining the same cycle.
        with h.database(f.home) as db:
            g=h.current_grant(db,'main');g.update(active=True,stop_epoch=h.stop_epoch(f.home,'main'));db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(g),'main'));db.commit()
        with self.assertRaises(h.HLProblem) as e:f.preflight()
        self.assertEqual(e.exception.code,'leverage_preflight_incomplete');f.worker.assert_not_called();f.sdk.assert_not_called()

if __name__=='__main__':unittest.main()
