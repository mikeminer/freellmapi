import tempfile
import time
import threading
import unittest
from pathlib import Path
from unittest.mock import patch
import hyperliquid_tools as h
from trading_view import TradingView


class TradingViewTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        h.configure(self.home,'mainnet-master','mainnet','0x'+'1'*40);self.view=TradingView(self.home);self.addCleanup(self.view.close)

    def settle(self):
        deadline=time.monotonic()+2
        while self.view.refreshing and time.monotonic()<deadline:time.sleep(.01)
        self.assertFalse(self.view.refreshing)

    def result(self):return {'profile':'mainnet-master','network':'mainnet','resource':'risk','retrieved_ms':int(time.time()*1000),
        'queried_address':'0x'+'1'*40,'source':'https://api.hyperliquid.xyz/info','data':{'account_mode':'unifiedAccount',
        'collateral_usdc':'22.37870064','available_usd':'22.37870064','positions':[],'orders':[],'dexs_scanned':11}}

    def test_cached_balance_never_requires_model_or_signer(self):
        with patch.object(h,'read',return_value=self.result()) as read,patch.object(h,'submit') as submit:
            self.view.snapshot();self.settle();value=self.view.snapshot();self.view.snapshot()
        read.assert_called_once_with(self.home,{'resource':'risk','profile':'mainnet-master'});submit.assert_not_called()
        self.assertEqual(value['collateral_usdc'],'22.37870064');self.assertEqual(value['orders'],[])
        self.assertEqual(value['state'],'ready');self.assertTrue(value['read_only'])

    def test_error_keeps_previous_balance_explicitly_stale_not_zero(self):
        with patch.object(h,'read',return_value=self.result()):self.view.snapshot();self.settle()
        with patch.object(h,'read',side_effect=RuntimeError('read timeout')):
            self.view.snapshot(refresh=True);self.settle();value=self.view.snapshot()
        self.assertEqual(value['state'],'stale');self.assertEqual(value['collateral_usdc'],'22.37870064')
        self.assertIn('read timeout',value['error'])

    def test_refreshes_coalesce_and_account_switch_hides_previous_balances(self):
        event=threading.Event()
        def read(*args):event.wait(1);return self.result()
        with patch.object(h,'read',side_effect=read) as native:
            self.view.snapshot()
            for _ in range(5):self.view.snapshot(refresh=True)
            data=h.config(self.home);data['default']=None;h.save_config(self.home,data)
            self.assertNotIn('collateral_usdc',self.view.snapshot())
            event.set();self.settle()
        native.assert_called_once();self.assertNotIn('collateral_usdc',self.view.snapshot())
