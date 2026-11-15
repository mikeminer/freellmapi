"""Deterministic analyst inputs and universe coverage; no financial execution."""
import copy,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import hyperliquid_analysis as analysis,hyperliquid_tools as h


class AnalystTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        self.p=h.configure(self.home,'main','mainnet','0x'+'1'*40);self.now=int(time.time()*1000)
        self.market={'name':'BTC','szDecimals':5,'maxLeverage':40}
        self.context={'markPx':'68000','oraclePx':'67999','funding':'0.0000125','openInterest':'12','dayNtlVlm':'100000','prevDayPx':'69000'}

    def candles(self,interval='15m',step=1):
        duration=analysis.INTERVALS[interval]*1000;end=self.now//duration*duration
        return [{'t':end-(120-i)*duration,'T':end-(119-i)*duration-1,'i':interval,
                 'o':str(68000+i*step),'c':str(68000+(i+1)*step),'h':str(68000+max(i,i+1)*step+abs(step)*2+2),
                 'l':str(68000+min(i,i+1)*step-abs(step)*2-2),'v':'10'} for i in range(120)]

    def read(self,p,args):
        typ=args['type']
        if typ=='meta':return {'universe':[self.market]}
        if typ=='perpDexs':return [None,{'name':'xyz'}]
        if typ=='metaAndAssetCtxs':
            coin='xyz:ABC' if args.get('dex') else 'BTC'
            return [{'universe':[self.market|{'name':coin}],'collateralToken':0},[self.context]]
        if typ=='candleSnapshot':return self.candles(args['req']['interval'])
        if typ=='l2Book':return {'time':self.now,'levels':[[{'px':'68000','sz':'1'}],[{'px':'68001','sz':'2'}]]}
        if typ=='userFees':return {'userCrossRate':'0.00045','userAddRate':'0.00015'}
        raise AssertionError(typ)

    def test_closed_candles_indicators_flat_and_trending_are_finite(self):
        for step,regime,rsi in ((1,'up',100),(-1,'down',0),(0,'mixed',50)):
            bars=self.candles(step=step)
            if step<0:
                for b in bars:b['h']=str(max(float(b['o']),float(b['c']))+3);b['l']=str(min(float(b['o']),float(b['c']))-3)
            result=analysis.diagnostics(bars,'15m',self.now)
            self.assertEqual(result['regime'],regime);self.assertEqual(result['rsi14_wilder'],rsi)
            self.assertGreater(result['atr14_wilder'],0);self.assertEqual(result['volume_ratio20'],1)

    def test_open_candle_is_excluded_from_indicators(self):
        bars=self.candles();before=analysis.diagnostics(bars,'15m',self.now)
        bars.append({'T':self.now+900000,'t':self.now,'c':'999999999'})
        self.assertEqual(analysis.diagnostics(bars,'15m',self.now),before)

    def test_nan_gaps_duplicates_stale_short_history_fail_closed(self):
        base=self.candles()
        cases=[base[:50],base[:-3],base+[base[-1]],base[:60]+base[61:]]
        bad=copy.deepcopy(base);bad[-1]['c']='NaN';cases.append(bad)
        for bars in cases:
            with self.subTest(length=len(bars)),self.assertRaises(ValueError):analysis.diagnostics(bars,'15m',self.now)

    def test_real_tool_uses_only_info_returns_data_and_records_scope(self):
        agent=SimpleNamespace()
        with patch.object(h,'info',side_effect=self.read) as reader,patch('hl_credentials.get') as key,patch('subprocess.run') as signer:
            result=h.dispatch(agent,'hyperliquid_analyze',{'coin':'BTC'},self.home)
        self.assertTrue(result['data_ready']);self.assertEqual(set(result['timeframes']),{'15m','1h'})
        self.assertEqual(reader.call_count,6);self.assertEqual(agent.hyperliquid_analysis[('main','BTC')]['scope'],h.fingerprint(self.p))
        key.assert_not_called();signer.assert_not_called();self.assertFalse((self.home/'hyperliquid/operations.db').exists())

    def test_missing_data_marks_not_ready_instead_of_fabricating_a_signal(self):
        def fail(p,args):
            if args['type']=='userFees':raise TimeoutError('fixture')
            return self.read(p,args)
        with patch.object(h,'info',side_effect=fail):result=analysis.analyze(self.home,{'coin':'BTC'})
        self.assertFalse(result['data_ready']);self.assertIn('fees',result['errors']);self.assertEqual(result['fees'],{})
        self.assertNotIn('win_probability',result);self.assertNotIn('safe_tp',result)

    def test_stale_orderbook_and_wrong_interval_rejected(self):
        def stale(p,args):
            value=self.read(p,args)
            if args['type']=='l2Book':value['time']-=60000
            return value
        with patch.object(h,'info',side_effect=stale):result=analysis.analyze(self.home,{'coin':'BTC'})
        self.assertFalse(result['data_ready']);self.assertIn('book',result['errors'])
        with patch.object(h,'info',side_effect=self.read),self.assertRaises(h.HLProblem):analysis.analyze(self.home,{'coin':'BTC','interval':'1h','higher_interval':'15m'})

    def test_scan_covers_all_dexs_and_pages_same_snapshot_without_api_calls(self):
        with patch('hl_credentials.get') as key,patch('subprocess.run') as worker:
            reader=Mock(side_effect=self.read);result=analysis.scan(self.home,{'limit':1},reader)
            self.assertTrue(result['complete']);self.assertEqual(result['markets_scanned'],2);self.assertEqual(result['dexs_requested'],2)
            self.assertEqual(reader.call_count,3);self.assertEqual(result['next_offset'],1)
            second=analysis.scan(self.home,{'limit':1,'offset':1,'scan_id':result['scan_id']},Mock(side_effect=AssertionError('No rescan')))
        self.assertEqual(second['markets'][0]['coin'],'xyz:ABC');self.assertIsNone(second['next_offset']);self.assertTrue(Path(result['evidence_file']).is_file())
        key.assert_not_called();worker.assert_not_called()

    def test_failed_dex_is_disclosed_and_never_called_complete(self):
        def fail(p,args):
            if args.get('dex')=='xyz':raise TimeoutError('fixture')
            return self.read(p,args)
        result=analysis.scan(self.home,{},fail)
        self.assertFalse(result['complete']);self.assertEqual(result['markets_scanned'],1);self.assertIn('xyz',result['errors'])

    def test_market_context_alignment_and_invalid_numbers_not_silently_skipped(self):
        def invalid(p,args):
            value=self.read(p,args)
            if args['type']=='metaAndAssetCtxs' and args.get('dex')=='xyz':value[1][0]=value[1][0]|{'markPx':'NaN'}
            return value
        result=analysis.scan(self.home,{},invalid)
        self.assertFalse(result['complete']);self.assertIn('xyz:xyz:ABC',result['errors']);self.assertEqual(result['markets_scanned'],1)

    def test_new_entry_needs_fresh_scope_matching_analyst_evidence(self):
        agent=SimpleNamespace(financial_turn_started_at=time.time(),messages=[],session=self.home/'s.json')
        args={'action':'order','coin':'BTC','side':'buy','size':'.001','order_kind':'limit','price':'68000',
              'take_profit':'69000','stop_loss':'67000','bracket_slippage_bps':30}
        def native(p,a):
            if a['type']=='userRole':return {'role':'user'}
            if a['type']=='allMids':return {'BTC':'68000'}
            return self.read(p,a)
        with patch.object(h,'info',side_effect=native),patch('hl_credentials.get') as key:
            result=h.dispatch(agent,'hyperliquid_preview',args,self.home)
            self.assertEqual(result['code'],'market_analysis_required');self.assertEqual(result['recovery_tools'][0]['name'],'hyperliquid_analyze')
            h.dispatch(agent,'hyperliquid_analyze',{'coin':'BTC'},self.home)
            row=h.preview(agent,self.home,args)['preview'];self.assertEqual(row['body']['analysis_evidence']['scope'],h.fingerprint(self.p))
            agent.hyperliquid_analysis[('main','BTC')]['scope']='old-profile'
            self.assertEqual(h.dispatch(agent,'hyperliquid_preview',args,self.home)['code'],'market_analysis_required')
        key.assert_not_called()

    def test_read_clock_and_evidence_id_cannot_keep_an_analyst_loop_alive(self):
        from loop_guard import LoopGuard,LoopDetected
        guard=LoopGuard()
        for number in range(4):
            result={'as_of_ms':number,'evidence_id':str(number),'book':{'time_ms':number,'mid':68000},'data_ready':False,'errors':{'fees':'unavailable'}}
            guard.observe('hyperliquid_analyze',{'coin':'BTC'},json.dumps(result))
        with self.assertRaises(LoopDetected):guard.observe('hyperliquid_analyze',{'coin':'BTC'},json.dumps(result|{'as_of_ms':999}))


if __name__=='__main__':unittest.main()
