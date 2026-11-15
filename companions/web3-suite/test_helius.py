"""RPC settings and Helius isolation, synthetic profile/provider tokens only."""
import json,os,subprocess,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import solana_local as s
import helius_local as h
import solana_tools

class HeliusTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.home=Path(t.name)
        self.public='11111111111111111111111111111111'
        s.atomic(s.folder(self.home)/'profiles.json',{'default':'test','profiles':{'test':{'account':self.public,'network':'devnet'}}})
        self.enterContext(patch.object(s,'pubkey',side_effect=lambda x:x))
        self.enterContext(patch.object(s,'version',return_value='test-installed'))
        self.rpc='https://devnet.helius-rpc.com/?api-key=SYNTHETIC_PROVIDER_TOKEN'
    def test_empty_rpc_uses_public_network_without_loading_key(self):
        with patch.object(s,'query',return_value=s.GENESIS['devnet']) as query,patch('hl_credentials.put') as put,patch('hl_credentials.get') as get:
            result=s.configure_rpc_local(self.home,'test','  ')
        self.assertTrue(result['public_rpc']);self.assertEqual(query.call_args.args[0]['rpc_url'],s.NETWORKS['devnet']);put.assert_not_called();get.assert_not_called()
        self.assertEqual(s.profile(self.home)['account'],self.public)
    def test_rpc_provider_credential_dpapi_not_profile_json_or_result(self):
        with patch.object(s,'query',return_value=s.GENESIS['devnet']),patch('hl_credentials.put') as put:
            result=s.configure_rpc_local(self.home,'test',self.rpc)
        self.assertEqual(put.call_args.args[0].name,'test.rpc.dpapi');self.assertEqual(put.call_args.args[1],self.rpc)
        self.assertNotIn('SYNTHETIC_PROVIDER_TOKEN',json.dumps(result)+json.dumps(s.config(self.home)))
        self.assertTrue(s.status(self.home)['profiles'][0]['custom_rpc'])
    def test_blank_resets_private_rpc_without_decrypting_wallet_or_provider_key(self):
        data=s.config(self.home);data['profiles']['test']['rpc_private']=True;s.atomic(s.folder(self.home)/'profiles.json',data)
        with patch('hl_credentials.get',side_effect=AssertionError('Do not decrypt')),patch.object(s,'query',return_value=s.GENESIS['devnet']):s.configure_rpc_local(self.home,'test','')
        self.assertEqual(s.profile(self.home).get('rpc_url'),None);self.assertFalse(s.status(self.home)['profiles'][0]['custom_rpc'])
    def test_dashboard_or_wrong_network_cannot_overwrite_rpc(self):
        initial=s.config(self.home)
        for endpoint in ('https://dashboard.helius.dev/project/rpc','http://devnet.helius-rpc.com/?api-key=SECRET','https://user:pw@devnet.helius-rpc.com'):
            with self.subTest(endpoint=endpoint),patch.object(s,'query') as query,self.assertRaises(ValueError):s.configure_rpc_local(self.home,'test',endpoint)
            query.assert_not_called()
        with patch.object(s,'query',return_value=s.GENESIS['mainnet']),patch('hl_credentials.put') as put,self.assertRaisesRegex(ValueError,'rete diversa'):s.configure_rpc_local(self.home,'test',self.rpc)
        put.assert_not_called();self.assertEqual(initial,s.config(self.home))
    def test_public_rpc_does_not_claim_helius_das_operational(self):
        with patch('hl_credentials.get') as get,patch.object(h.subprocess,'run') as run:
            state=h.status(self.home);result=h.read(self.home,{'resource':'asset','address':self.public})
        self.assertFalse(state['configured']);self.assertEqual(result['code'],'helius_not_configured');get.assert_not_called();run.assert_not_called()
    def test_helius_worker_receives_provider_rpc_but_never_wallet_or_model_secrets(self):
        data=s.config(self.home);data['profiles']['test']['rpc_private']=True;s.atomic(s.folder(self.home)/'profiles.json',data)
        with patch('hl_credentials.get',return_value=self.rpc) as get,patch.dict(os.environ,{'PRIVATE_KEY':'DO_NOT_PASS','NODE_OPTIONS':'DO_NOT_PASS','FREELLMAPI_SESSION_TOKEN':'DO_NOT_PASS'}),patch.object(h.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps({'ok':True,'echo':self.rpc}))) as run:
            result=h.read(self.home,{'resource':'assets_by_owner'})
        self.assertEqual(get.call_count,1);self.assertEqual(get.call_args.args[0].name,'test.rpc.dpapi')
        args=run.call_args;self.assertNotIn('DO_NOT_PASS',json.dumps(args.kwargs));self.assertNotIn(self.rpc,str(args.args))
        self.assertEqual(json.loads(args.kwargs['input'])['address'],self.public);self.assertNotIn('SYNTHETIC_PROVIDER_TOKEN',json.dumps(result))
    def test_helius_timeout_is_one_attempt_no_fallback_or_zero_balance(self):
        with patch.object(s,'profile',return_value={'name':'test','account':self.public,'network':'devnet','rpc_url':self.rpc}),patch.object(h.subprocess,'run',side_effect=subprocess.TimeoutExpired('node',32)) as run:
            result=h.read(self.home,{'resource':'network'})
        self.assertEqual(run.call_count,1);self.assertEqual(result['code'],'helius_timeout');self.assertNotIn('balance',result)
    def test_routing_and_schema_keep_helius_read_only(self):
        from tool_routing import suggested
        from market_routing import intent
        from freellmapi import schema
        self.assertIn('helius_read',suggested('Leggi gli asset del wallet con Helius'))
        self.assertIsNone(intent('Leggi il saldo del mio wallet Helius'))
        spec=next(x for x in solana_tools.SPECS if x[0]=='helius_read');props=schema(*spec)['function']['parameters']['properties']
        self.assertEqual(props['account_keys']['type'],'array');self.assertNotIn('rpc_url',props);self.assertNotIn('private_key',props)

if __name__=='__main__':unittest.main()
