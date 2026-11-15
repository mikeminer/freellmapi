import json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import solana_tools as s
from tool_routing import suggested

class SolanaToolsTests(unittest.TestCase):
    def test_natural_request_exposes_native_group_and_no_secret_or_send_fields(self):
        self.assertIn('solana_read',suggested('Leggi il saldo del mio wallet Solana'))
        self.assertNotIn('solana_send',s.NAMES)
        for name,description,properties,required in s.SPECS:
            self.assertFalse(set(properties)&{'private_key','secret','seed','mnemonic'})

    def test_worker_command_is_structured_and_cannot_import_or_sign(self):
        with tempfile.TemporaryDirectory() as temp,patch.object(s.PYTHON.__class__,'is_file',return_value=True),patch.object(s.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"profiles": []}')) as process:
            s.dispatch(SimpleNamespace(),'solana_read',{'resource':'balance','profile':'dedicated'},Path(temp))
        request=json.loads(process.call_args.kwargs['input'])
        self.assertEqual(request,{'command':'read','arguments':{'resource':'balance','profile':'dedicated'}})
        self.assertNotIn('secret',str(process.call_args.args))

    def test_catalog_reports_protocol_limits_instead_of_claiming_all_tools_operational(self):
        result=s.catalog();self.assertIn('Jupiter swaps',result['not_native_execution'])
        self.assertIn('awesome',result['resources'])

    def test_solana_wallet_balance_is_not_routed_to_hyperliquid_account(self):
        from market_routing import intent
        self.assertIsNone(intent('Leggi il saldo del mio wallet Solana'))
        self.assertEqual(intent('Analizza SOL/USDC su Hyperliquid')['coin'],'SOL')
