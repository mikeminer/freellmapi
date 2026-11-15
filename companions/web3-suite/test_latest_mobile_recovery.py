"""Replay ...457bf: margin-edge sizing followed by expired analysis; no orders."""
import json,unittest
from decimal import Decimal
from types import SimpleNamespace
from unittest.mock import Mock,patch
from loop_guard import LoopGuard
from financial_recovery import pending

class LatestMobileRecoveryTests(unittest.TestCase):
    def test_two_capacity_reads_allow_one_distinct_analysis_refresh_then_stop(self):
        guard=LoopGuard();guard.financial_recovery=[{'name':'hyperliquid_analyze','arguments':{'profile':'main','coin':'BTC'}}]
        agent=SimpleNamespace(loop_guard=guard,financial_read_recovery={'rounds':2},messages=[],save=Mock())
        evidence=[]
        with patch('checkpoint_recovery.native_call',return_value=json.dumps({'profile':'main','coin':'BTC','evidence_id':'new-closed-candles','timeframes':{'15m':{},'1h':{}}})) as read:
            self.assertIsNone(pending(agent,evidence));read.assert_called_once()
            self.assertEqual(read.call_args.args[1],'hyperliquid_analyze')
            guard.financial_recovery=[{'name':'hyperliquid_analyze','arguments':{'profile':'main','coin':'BTC'}}]
            self.assertIn('sospesa',pending(agent,evidence));self.assertEqual(read.call_count,1)
        self.assertEqual(agent.financial_read_recovery['rounds'],3)
    def test_457bf_requested_sizes_fail_the_same_risk_formula(self):
        available=Decimal('41.419673');leverage=Decimal(40)
        for size,price in [('0.0253','65467'),('0.02342','65512')]:
            notional=Decimal(size)*Decimal(price)
            self.assertGreater(notional/leverage+notional*Decimal('.002'),available)
        price=Decimal('65544');maximum=available/(1/leverage+Decimal('.002'))/price
        hint=(maximum*Decimal('.99')).quantize(Decimal('.00001'),rounding='ROUND_DOWN')
        self.assertLess(hint*price*(1/leverage+Decimal('.002')),available*Decimal('.991'))

if __name__=='__main__':unittest.main()
