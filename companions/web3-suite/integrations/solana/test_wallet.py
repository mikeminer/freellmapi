"""Isolated SDK tests. Every wallet is synthetic; every RPC is mocked."""
import hashlib,json,sys,tempfile,unittest
from pathlib import Path
from unittest.mock import patch
sys.path.insert(0,str(Path(__file__).resolve().parents[2]))
import solana_local as s
from solders.keypair import Keypair
from solders.hash import Hash
from solders.transaction import VersionedTransaction

class WalletTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        self.wallet=Keypair();self.recipient=Keypair().pubkey()
        s.import_wallet(self.home,'synthetic','devnet',str(self.wallet),str(self.wallet.pubkey()))

    def rpc(self,p,name,args):
        if name=='getGenesisHash':return s.GENESIS[p['network']]
        if name=='getLatestBlockhash':return {'value':{'blockhash':str(Hash.default()),'lastValidBlockHeight':100}}
        if name=='getFeeForMessage':return {'value':5000}
        if name=='simulateTransaction':return {'value':{'err':None,'logs':[]}}
        if name=='sendTransaction':
            import base64
            return str(VersionedTransaction.from_bytes(base64.b64decode(args[0])).signatures[0])
        raise AssertionError(name)

    def preview(self):
        with patch.object(s,'query',side_effect=self.rpc):
            return s.prepare(self.home,{'profile':'synthetic','recipient':str(self.recipient),'amount_sol':'0.00001'})

    def test_import_never_generates_another_wallet_or_stores_plaintext(self):
        public=json.loads((self.home/'solana/profiles.json').read_text())
        self.assertEqual(public['profiles']['synthetic']['account'],str(self.wallet.pubkey()))
        self.assertNotIn(str(self.wallet),(self.home/'solana/profiles.json').read_text())
        self.assertNotIn(str(self.wallet).encode(),(self.home/'solana/synthetic.dpapi').read_bytes())
        with self.assertRaises(ValueError):s.import_wallet(self.home,'other','devnet',str(self.wallet),str(self.recipient))
        self.assertFalse((self.home/'solana/other.dpapi').exists())

    def test_invalid_secret_error_does_not_echo_secret(self):
        secret='never-echo-this-private-input'
        with self.assertRaises(ValueError) as caught:s.import_wallet(self.home,'other','devnet',secret)
        self.assertNotIn(secret,str(caught.exception))

    def test_preview_simulates_without_signing_or_sending(self):
        with patch.object(s,'send_reviewed') as send:result=self.preview()
        send.assert_not_called();self.assertFalse(result['sent']);self.assertTrue(result['local_review_required'])
        self.assertEqual(result['transfer']['lamports'],10000)

    def test_exact_review_sends_once_and_replay_is_refused(self):
        item=self.preview();review=hashlib.sha256(json.dumps(item['transfer'],sort_keys=True).encode()).hexdigest()
        with patch.object(s,'query',side_effect=self.rpc) as rpc:
            result=s.send_reviewed(self.home,item['id'],review)
            self.assertEqual(result['state'],'submitted');self.assertFalse(result['confirmed'])
            with self.assertRaises(ValueError):s.send_reviewed(self.home,item['id'],review)
        self.assertEqual(len([c for c in rpc.call_args_list if c.args[1]=='sendTransaction']),1)

    def test_changed_preview_rejected_before_secret_access(self):
        item=self.preview()
        with patch('hl_credentials.get') as secret:
            with self.assertRaises(ValueError):s.send_reviewed(self.home,item['id'],'0'*64)
        secret.assert_not_called()

    def test_unknown_send_outcome_persists_signature_and_blocks_replacement(self):
        first=self.preview();second=self.preview()
        review=lambda item:hashlib.sha256(json.dumps(item['transfer'],sort_keys=True).encode()).hexdigest()
        def timeout(p,name,args):
            if name=='getGenesisHash':return s.GENESIS[p['network']]
            raise TimeoutError('synthetic transport timeout')
        with patch.object(s,'query',side_effect=timeout) as rpc:
            result=s.send_reviewed(self.home,first['id'],review(first));self.assertEqual(result['state'],'uncertain')
            with self.assertRaises(ValueError):s.send_reviewed(self.home,second['id'],review(second))
        self.assertEqual(len([c for c in rpc.call_args_list if c.args[1]=='sendTransaction']),1);self.assertTrue(result['signature'])
        self.assertIn('uncertain',[r['state'] for r in s.operations(self.home)['operations']])

    def test_excess_precision_and_nonfinite_amounts_rejected_without_rpc(self):
        for amount in ('NaN','Infinity','-1','0','0.0000000001'):
            with self.subTest(amount=amount),patch.object(s,'query') as rpc:
                with self.assertRaises(ValueError):s.prepare(self.home,{'profile':'synthetic','recipient':str(self.recipient),'amount_sol':amount})
                rpc.assert_not_called()

if __name__=='__main__':unittest.main()
