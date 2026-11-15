"""Reproduce actual misplaced execution options without changing financial values."""
import copy,json,unittest
from unittest.mock import patch
import hyperliquid_execution as x,hyperliquid_tools as h
import test_hyperliquid_execution as fixtures

class PlanTransportTests(unittest.TestCase):
    setUp=fixtures.ExecutionTests.setUp
    def nested(self):
        return {'profile':'main','plan':json.dumps({**self.plan,'order_kind':'limit','bracket_slippage_bps':30}),'bracket_slippage_bps':30}
    def test_reported_misplacement_is_repaired_before_native_send_and_only_once(self):
        args=self.nested();before=copy.deepcopy(args)
        first=x.execute(self.agent,self.f.home,args);again=x.execute(self.agent,self.f.home,args)
        self.assertIn('plan.order_kind -> order_kind',first['wire_repairs']);self.assertTrue(again['reused'])
        self.f.exchange.bulk_orders.assert_called_once();self.assertEqual(args,before)
        self.assertEqual(first['receipt']['body']['size'],self.plan['size'])
    def test_conflicting_values_are_not_silently_overridden(self):
        args={**self.nested(),'order_kind':'market'}
        with patch.object(h,'preview') as preview,patch.object(h,'info') as info:
            result=h.dispatch(self.agent,'hyperliquid_execute_plan',args,self.f.home)
        self.assertEqual(result['code'],'invalid_trade_plan');self.assertEqual(result['field'],'order_kind')
        self.assertEqual(result['conflicting_paths'],['order_kind','plan.order_kind']);self.assertFalse(result['sent'])
        preview.assert_not_called();info.assert_not_called();self.f.secret.assert_not_called()
    def test_unknown_fields_explain_how_to_correct_before_any_preparation(self):
        args={**self.args,'plan':json.dumps({**self.plan,'position_side':'long'})}
        result=h.dispatch(self.agent,'hyperliquid_execute_plan',args,self.f.home)
        self.assertEqual(result['unknown_fields'],['position_side']);self.assertIn('side',result['plan_contract']['enter_fields'])
        self.assertTrue(result['terminal']);self.assertFalse(result['sent']);self.f.exchange.bulk_orders.assert_not_called()
    def test_pump_analysis_followup_enters_native_market_path(self):
        from market_routing import intent
        first='fammi una analisi su PUMP';follow='trova un segnale ed eseguilo con 90% del capitale'
        self.assertEqual(intent(first)['coin'],'PUMP')
        value=intent(follow,[{'role':'user','content':first}])
        self.assertEqual(value['coin'],'PUMP');self.assertTrue(value['trading']);self.assertEqual(value['venue'],'hyperliquid')

if __name__=='__main__':unittest.main()
