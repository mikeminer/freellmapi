"""Native panel construction and RPC editing with synthetic profiles only."""
import tempfile,unittest
from pathlib import Path
from unittest.mock import patch
import solana_local as s

class PanelTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.home=Path(t.name)
        s.atomic(s.folder(self.home)/'profiles.json',{'default':'test','profiles':{'test':{'account':'11111111111111111111111111111111','network':'devnet'}}})
        self.enterContext(patch.object(s,'pubkey',side_effect=lambda x:x))
    def panel(self,module):
        import importlib,tkinter
        try:panel=importlib.import_module(module).Panel(self.home)
        except tkinter.TclError as exc:self.skipTest('Tk display unavailable: '+type(exc).__name__)
        panel.root.withdraw();self.addCleanup(panel.root.destroy);return panel
    def test_solana_optional_rpc_is_blank_masked_and_saves_without_key_import(self):
        import tkinter.ttk as ttk
        panel=self.panel('solana_panel');self.assertEqual(panel.rpc.get(),'')
        def widgets(w):return [w]+[x for child in w.winfo_children() for x in widgets(child)]
        matches=[x for x in widgets(panel.root) if isinstance(x,ttk.Entry) and str(x.cget('textvariable'))==str(panel.rpc)]
        self.assertEqual(len(matches),1);self.assertTrue(matches[0].cget('show'))
        panel.run=lambda fn,done=None:fn()
        with patch.object(s,'configure_rpc_local',return_value={'public_rpc':True}) as rpc,patch.object(s,'import_wallet') as key:
            panel.save_rpc()
        rpc.assert_called_once_with(self.home,'test','');key.assert_not_called()
    def test_pump_panel_constructs_without_importing_wallet_or_authorizing(self):
        with patch('hl_credentials.get') as key,patch('pump_local.authorize_local') as grant:
            panel=self.panel('pump_panel')
        key.assert_not_called();grant.assert_not_called();self.assertEqual(panel.text('rpc_url'),'')
        panel.values['slippage_bps'].set('not-an-integer');panel.prepare()
        self.assertIn('intero',panel.message.get())

if __name__=='__main__':unittest.main()
