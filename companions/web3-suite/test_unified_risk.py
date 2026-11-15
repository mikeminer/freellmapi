import copy,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hyperliquid_risk as r
import hyperliquid_tools as h

class UnifiedRiskTests(unittest.TestCase):
    def setUp(self):
        self.cash={'balances':[{'coin':'USDC','token':0,'total':'20','hold':'0'},{'coin':'OTHER','token':17,'total':'999999','hold':'0'}],'tokenToAvailableAfterMaintenance':[[0,'20']]}
        self.coll={'':0,'xyz':0,'cash':360}
        self.states={dex:{'marginSummary':{'accountValue':'0'},'assetPositions':[],'crossMaintenanceMarginUsed':'0'} for dex in self.coll}
        self.orders={dex:[] for dex in self.coll}
    def position(self,coin='xyz:ABC',pnl='-2',margin='3',notional='30',kind='cross'):
        return {'position':{'coin':coin,'szi':'1','positionValue':notional,'unrealizedPnl':pnl,'marginUsed':margin,'leverage':{'type':kind,'value':10}}}
    def state(self):return r.unified(self.cash,self.coll,self.states,self.orders,'')
    def test_unified_twenty_is_not_zero_or_sum_of_dex_equities(self):
        for s in self.states.values():s['marginSummary']['accountValue']='999'
        x=self.state();self.assertEqual(x['risk_equity_usd'],'20');self.assertEqual(x['available_usd'],'20');self.assertEqual(x['dexs_scanned'],3)
    def test_other_dex_losses_margin_hold_and_open_entry_are_reserved(self):
        self.states['xyz']['assetPositions']=[self.position()];self.cash['balances'][0]['hold']='1'
        self.orders['xyz']=[{'oid':1,'sz':'.1','limitPx':'40','reduceOnly':False}]
        x=self.state();self.assertEqual(x['risk_equity_usd'],'18');self.assertEqual(x['available_usd'],'10');self.assertEqual(x['gross_exposure_usd'],'34')
    def test_positive_pnl_is_not_used_to_fund_another_order(self):
        self.states['xyz']['assetPositions']=[self.position(pnl='200')]
        self.assertEqual(self.state()['risk_equity_usd'],'20')
    def test_short_notional_cannot_cancel_out_long_exposure(self):
        self.states['xyz']['assetPositions']=[self.position(notional='-30',pnl='0')]
        self.assertEqual(self.state()['gross_exposure_usd'],'30')
    def test_protective_exits_not_counted_three_times(self):
        self.orders['']=[{'oid':i,'sz':'.001','limitPx':'70000','reduceOnly':True} for i in (1,2)]
        self.assertEqual(self.state()['gross_exposure_usd'],'0')
    def test_maintenance_ratio_uses_all_usdc_dexs_and_isolated_reserves(self):
        self.states['xyz']['assetPositions']=[self.position(kind='isolated',pnl='0',margin='5')];self.states['']['crossMaintenanceMarginUsed']='1';self.states['xyz']['crossMaintenanceMarginUsed']='2'
        self.assertEqual(self.state()['unified_maintenance_ratio'],'0.2')
    def test_foreign_collateral_and_incomplete_scan_fail_closed(self):
        self.states['cash']['assetPositions']=[self.position(coin='cash:ABC')]
        with self.assertRaises(r.RiskError) as e:self.state()
        self.assertEqual(e.exception.code,'unsupported_collateral_exposure')
        del self.states['cash']
        with self.assertRaises(r.RiskError):self.state()
    def test_missing_nan_duplicate_or_mismatched_usdc_cannot_authorize(self):
        for cash in ({'balances':[]},{'balances':[{'token':0,'coin':'FAKE','total':'20','hold':'0'}]}, {'balances':[{'token':0,'coin':'USDC','total':'NaN','hold':'0'}]}, {'balances':self.cash['balances']+[self.cash['balances'][0]]}):
            with self.assertRaises(r.RiskError):r.unified(cash,self.coll,self.states,self.orders,'')
    def test_capacity_uses_exchange_limits_and_free_margin_with_fee_reserve(self):
        active={'leverage':{'value':1},'maxTradeSzs':['.0003','.0002'],'availableToTrade':['15','12']}
        body={'size':'.00016'};self.assertEqual(r.capacity(self.state(),active,body,r.Decimal('11')),'11.022')
        for changed in (active|{'maxTradeSzs':['.00015','.0002']},active|{'availableToTrade':['2','12']},active|{'availableToTrade':None}):
            with self.assertRaises(r.RiskError):r.capacity(self.state(),changed,body,r.Decimal('11'))
    def test_directory_does_not_silently_relabel_collateral_or_dex(self):
        metas=[{'collateralToken':0,'universe':[{'name':'BTC'}]},{'collateralToken':0,'universe':[{'name':'xyz:ABC'}]}]
        self.assertEqual(r.directory([None,{'name':'xyz'}],metas,''),{'':0,'xyz':0})
        with self.assertRaises(r.RiskError):r.directory([None,{'name':'xyz'}],metas[::-1],'')
        with self.assertRaises(r.RiskError):r.directory([None],metas,'')
    def test_live_style_grant_uses_usdc_and_rejects_new_flow_or_mode_change(self):
        with tempfile.TemporaryDirectory() as t:
            home=Path(t);master='0x'+'1'*40;h.configure(home,'u','mainnet',master)
            conf=h.config(home);conf['profiles']['u'].update(signer=master,signer_kind='master');h.save_config(home,conf);h.secret_path(home,'u').write_bytes(b'NO_KEY')
            settings={'coins':'BTC','max_order_usd':'12','max_exposure_usd':'25','max_loss_usd':'3','max_turnover_usd':'24','max_leverage':'1','max_orders_hour':'3','interval':'60','duration_minutes':'60','strategy_mode':'auto','strategy':''}
            mode=['unifiedAccount'];flows=[]
            def reply(p,b):
                kind=b['type']
                if kind=='userRole':return {'role':'user'}
                if kind=='userAbstraction':return mode[0]
                if kind=='perpDexs':return [None,{'name':'xyz'},{'name':'cash'}]
                if kind=='allPerpMetas':return [{'collateralToken':v,'universe':[{'name':'BTC' if not k else k+':ABC'}]} for k,v in self.coll.items()]
                if kind=='spotClearinghouseState':return self.cash
                if kind=='clearinghouseState':return self.states[b.get('dex','')]
                if kind=='frontendOpenOrders':return self.orders[b.get('dex','')]
                if kind=='meta':return {'universe':[{'name':'BTC','szDecimals':5,'maxLeverage':40}]}
                if kind=='allMids':return {'BTC':'68000'}
                if kind=='activeAssetData':return {'leverage':{'type':'cross','value':1},'maxTradeSzs':['1','1'],'availableToTrade':['20','20']}
                if kind=='userNonFundingLedgerUpdates':return flows
                raise AssertionError(kind)
            a=SimpleNamespace(session=home/'session.json',mode='auto',depth=0,interactive=False,messages=[{'role':'user','content':'paper only'}])
            with patch('hyperliquid_tools.info',side_effect=reply):
                g=h.arm(home,'u',settings,a.session)['grant'];self.assertEqual(g['baseline_equity'],'20');self.assertEqual(g['account_mode'],'unifiedAccount')
                row=h.preview(a,home,{'action':'order','coin':'BTC','side':'buy','size':'.00016','order_kind':'limit','price':'68000','take_profit':'69000','stop_loss':'67000','bracket_slippage_bps':30})['preview']
                with h.database(home) as db:
                    self.assertEqual(h.guard_grant(h.profile(home),g,row['body'],a.session,db),'10.88')
                    flows.append({'time':int(time.time()*1000)+1,'delta':{'type':'deposit','usdc':'5'}})
                    with self.assertRaises(h.HLProblem) as e:h.guard_grant(h.profile(home),g,row['body'],a.session,db)
                    self.assertEqual(e.exception.code,'account_flow_changed');flows.clear();mode[0]='disabled'
                    with self.assertRaises(h.HLProblem) as e:h.guard_grant(h.profile(home),g,row['body'],a.session,db)
                    self.assertEqual(e.exception.code,'account_mode_changed')

if __name__=='__main__':unittest.main()
