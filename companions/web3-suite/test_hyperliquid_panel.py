"""Credential form regressions using synthetic values, no wallet/network access."""
import unittest
from unittest.mock import Mock,patch
import hyperliquid_tools as hl
from hyperliquid_panel import Panel

class Value:
    def __init__(self,value):self.value=value
    def get(self):return self.value
    def set(self,value):self.value=value

class CredentialFormTests(unittest.TestCase):
    def panel(self):
        p=Panel.__new__(Panel);p.busy=False;p.home='unused-test-home'
        p.values={k:Value(v) for k,v in {'profile':'test-profile','network':'testnet','account':'public-test-address','kind':'account','target':'public-test-address','signer_kind':'agent','signing_secret':'synthetic-non-wallet-input'}.items()}
        p.save_profile=Mock();p.work=lambda fn:fn()
        return p
    def test_key_stays_in_masked_field_after_success_and_not_in_profile_data(self):
        p=self.panel()
        with patch('hyperliquid_signer.connect',return_value={'connected':True}) as connect:p.connect()
        self.assertEqual(p.values['signing_secret'].get(),'synthetic-non-wallet-input')
        self.assertNotIn('signing_secret',p.save_profile.call_args.args[0]);self.assertNotIn('signing_secret',p.capture())
        self.assertEqual(connect.call_args.args[2],'synthetic-non-wallet-input')
    def test_failed_connection_keeps_the_entered_key_for_retry(self):
        p=self.panel()
        with patch('hyperliquid_signer.connect',side_effect=hl.HLProblem('agent_unauthorized','Synthetic rejected agent')):
            with self.assertRaises(hl.HLProblem):p.connect()
        self.assertEqual(p.values['signing_secret'].get(),'synthetic-non-wallet-input')
    def test_only_explicit_clear_empties_field_without_disconnect_or_settings_change(self):
        p=self.panel();before=p.capture();p.clear_signing_secret()
        self.assertEqual(p.values['signing_secret'].get(),'');self.assertEqual(p.capture(),before);p.save_profile.assert_not_called()
    def test_removed_paper_controls_are_not_present(self):
        self.assertFalse(hasattr(Panel,'save_service_markets'))
        import inspect
        self.assertNotIn('Avvia PAPER',inspect.getsource(Panel))

if __name__=='__main__':unittest.main()
