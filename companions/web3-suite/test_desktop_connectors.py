import tempfile,unittest
from pathlib import Path
from unittest.mock import patch,Mock
from desktop_backend import DesktopBackend

class DesktopConnectorTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.home=Path(self.temp.name);self.backend=DesktopBackend(self.home)
    def test_hyperliquid_uses_current_shared_session_and_existing_home_without_ai(self):
        sid='a'*32
        with patch.object(self.backend.client,'state',return_value={'session':sid}),\
             patch.object(self.backend.client,'send',side_effect=AssertionError('No AI request')),\
             patch('hyperliquid_tools.panel',return_value={'panel_opened':True,'pid':12,'private_keys':'Never return this field'}) as panel:
            result=self.backend.open_connector('hyperliquid')
        args=panel.call_args.args
        self.assertEqual(args[1],self.home);self.assertEqual(args[0].session.stem,sid)
        self.assertEqual(result,{'panel_opened':True,'connector':'hyperliquid','pid':12})
    def test_solana_uses_existing_home_and_never_sends_a_chat_prompt(self):
        with patch.object(self.backend.client,'send',side_effect=AssertionError('No AI request')),\
             patch('solana_tools.panel',return_value={'panel_opened':True,'pid':13}) as panel:
            result=self.backend.open_connector('solana')
        panel.assert_called_once_with(self.home);self.assertEqual(result['connector'],'solana')
    def test_missing_connector_sdk_is_reported_and_unknown_names_are_rejected(self):
        with patch('solana_tools.panel',return_value={'ok':False,'code':'solana_sdk_missing'}),self.assertRaisesRegex(RuntimeError,'solana_sdk_missing'):
            self.backend.open_connector('solana')
        with self.assertRaises(ValueError):self.backend.open_connector('arbitrary-command')
    def test_settings_have_both_native_connector_buttons(self):
        js=Path('desktop/app.js').read_text(encoding='utf-8')
        self.assertIn('Connettore Hyperliquid',js);self.assertIn('Connettore Solana',js)
        self.assertIn("rpc('connector'",js)

if __name__=='__main__':unittest.main()
