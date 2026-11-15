"""Native Pump boundaries, synthetic wallet only, network forbidden."""
import base64,copy,json,os,struct,subprocess,sys,tempfile,time,unittest,uuid
from pathlib import Path
from unittest.mock import patch,Mock
try:import solders
except ImportError:raise unittest.SkipTest('Run native Pump tests with integrations/solana/.venv/Scripts/python.exe')
from solders.keypair import Keypair
from solders.pubkey import Pubkey
from solders.hash import Hash
from solders.signature import Signature
from solders.instruction import Instruction,AccountMeta
from solders.message import MessageV0
from solders.transaction import VersionedTransaction
from solders.compute_budget import set_compute_unit_limit,set_compute_unit_price
from solders.system_program import transfer,TransferParams
import pump_local as p
import solana_local as s
import pump_service as service

class PumpTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.home=Path(t.name)
        self.wallet=Keypair.from_seed(bytes(range(32)));self.user=str(self.wallet.pubkey());self.mint=str(Pubkey.new_unique())
        s.atomic(s.folder(self.home)/'profiles.json',{'default':'test','profiles':{'test':{'account':self.user,'network':'devnet'}}})
        (s.folder(self.home)/'test.dpapi').write_bytes(b'not-a-real-key')
        self.profile=p.profile(self.home)
        self.query=self.enterContext(patch.object(s,'query',side_effect=self.rpc))
        self.secret=self.enterContext(patch('hl_credentials.get',return_value=str(self.wallet)))
        self.body=self.fixture()
    def rpc(self,profile,method,params):
        if method=='getGenesisHash':return s.GENESIS['devnet']
        if method=='getBalance':return {'value':1_000_000_000}
        if method=='getBlockHeight':return 100
        if method=='simulateTransaction':return {'value':{'err':None}}
        if method=='sendTransaction':return str(VersionedTransaction.from_bytes(base64.b64decode(params[0])).signatures[0])
        raise AssertionError('Unexpected RPC '+method)
    def fixture(self,side='buy',venue='curve'):
        mint=self.mint;user=self.user;token=s.TOKEN_PROGRAMS[1]
        curve=str(Pubkey.find_program_address([b'bonding-curve',bytes(s.pubkey(mint))],s.pubkey(p.PROGRAMS['curve']))[0])
        authority=Pubkey.find_program_address([b'pool-authority',bytes(s.pubkey(mint))],s.pubkey(p.PROGRAMS['curve']))[0]
        pool=str(Pubkey.find_program_address([b'pool',bytes(2),bytes(authority),bytes(s.pubkey(mint)),bytes(s.pubkey(p.WSOL))],s.pubkey(p.PROGRAMS['amm']))[0])
        name=('buy_exact_quote_in_v3' if side=='buy' else 'sell_v3') if venue=='curve' else ('buy_exact_quote_in_v2' if side=='buy' else 'sell_v2')
        idl=json.loads((p.SDK/'idl'/(venue+'.json')).read_text());definition=next(x for x in idl['instructions'] if x['name']==name)
        values={'user':user,'base_mint':mint,'quote_mint':p.WSOL,'bonding_curve':curve,'pool':pool,
            'base_token_program':token,'quote_token_program':s.TOKEN_PROGRAMS[0],
            'associated_base_user':p.associated(user,mint,token),'associated_quote_user':p.associated(user,p.WSOL,s.TOKEN_PROGRAMS[0]),
            'user_base_token_account':p.associated(user,mint,token),'user_quote_token_account':p.associated(user,p.WSOL,s.TOKEN_PROGRAMS[0])}
        metas=[AccountMeta(s.pubkey(values.get(a['name'],str(Pubkey.new_unique()))),bool(a.get('signer')),bool(a.get('writable'))) for a in definition['accounts']]
        ix=Instruction(s.pubkey(p.PROGRAMS[venue]),bytes(definition['discriminator'])+struct.pack('<QQ',100000,190000),metas)
        body={'quote_id':uuid.uuid4().hex,'mint':mint,'side':side,'venue':venue,'token_program':token,'input_amount':'100000','max_input':'100000','expected_output':'200000','minimum_output':'190000',
            'fee_lamports':5000,'rent_buffer_lamports':6000000,'priority_micro_lamports':0,'blockhash':str(Hash.new_unique()),'last_valid_block_height':1000,
            'profile':'test','account':user,'network':'devnet','scope':p.scope(self.profile),'protocol':'pumpfun','created':time.time(),'created_ms':int(time.time()*1000),
            'take_profit_pct':'50','stop_loss_pct':'25','maximum_debit_lamports':6105000,'token_balance':'1000000','mint_authority':None,'freeze_authority':None,
            'largest_token_accounts':[],'slippage_bps':100,'decimals':6,'impact_including_protocol_cost_bps':10,'supply':'1000000000','real_quote_reserves':'10000000000','pool_quote_reserves':'10000000000'}
        self.encode(body,[set_compute_unit_limit(300000),set_compute_unit_price(0),ix]);return body
    def encode(self,body,ixs):
        m=MessageV0.try_compile(self.wallet.pubkey(),ixs,[],Hash.from_string(body['blockhash']))
        body['unsigned_transaction']=base64.b64encode(bytes(VersionedTransaction.populate(m,[Signature.default()]))).decode();return body
    def seed(self,body=None):
        b=body or self.body
        with s.database(self.home) as d:d.execute('INSERT INTO operations VALUES(?,?,?,?,?)',(b['quote_id'],'prepared',json.dumps(b),None,None))
        return b['quote_id']
    def grant(self,**changes):
        settings={'max_trade_sol':'0.05','max_total_sol':'0.1','fee_reserve_sol':'0.02','max_loss_sol':'0.1','max_positions':3,'max_trades':10,'max_slippage_bps':100,'duration_minutes':60,'mints':[self.mint],**changes}
        return p.authorize_local(self.home,'test',settings)
    def test_unsigned_curve_and_amm_buy_sell_are_validated_without_key_access(self):
        for venue in ('curve','amm'):
            for side in ('buy','sell'):p.validate_transaction(self.profile,self.fixture(side,venue))
        self.secret.assert_not_called();self.query.assert_not_called()
    def test_extra_system_withdrawal_is_rejected(self):
        tx=p.validate_transaction(self.profile,self.body)
        original=[]
        # Append a malicious transfer to the already-valid message by reconstructing its instructions.
        for ix in tx.message.instructions:
            keys=tx.message.account_keys;metas=[AccountMeta(keys[i],i==0,True) for i in ix.accounts]
            original.append(Instruction(keys[ix.program_id_index],bytes(ix.data),metas))
        original.append(transfer(TransferParams(from_pubkey=self.wallet.pubkey(),to_pubkey=Pubkey.new_unique(),lamports=1)))
        self.encode(self.body,original)
        with self.assertRaisesRegex(p.PumpError,'withdrawal'):p.validate_transaction(self.profile,self.body)
        self.secret.assert_not_called()
    def test_mutated_intent_mint_side_amount_and_minimum_are_rejected(self):
        for change in ({'mint':str(Pubkey.new_unique())},{'input_amount':'100001'},{'minimum_output':'190001'},{'side':'sell'},{'venue':'amm'},{'blockhash':str(Hash.new_unique())}):
            with self.subTest(change=change),self.assertRaises(p.PumpError):p.validate_transaction(self.profile,{**self.body,**change})
    def test_simulation_rejection_never_imports_secret(self):
        self.seed();self.grant();self.query.side_effect=lambda *a: {'value':{'err':'rejected'}} if a[1]=='simulateTransaction' else self.rpc(*a)
        with self.assertRaisesRegex(p.PumpError,'simulation_rejected'):p.execute(self.home,self.body['quote_id'])
        self.secret.assert_not_called()
    def test_no_grant_blocks_before_secret_and_send(self):
        ident=self.seed()
        with self.assertRaisesRegex(p.PumpError,'grant_required'):p.execute(self.home,ident)
        self.secret.assert_not_called();self.assertFalse(any(x.args[1]=='sendTransaction' for x in self.query.call_args_list))
    def test_grant_limits_require_explicit_finite_sol_values(self):
        for v in ('NaN','Infinity',True,'0','-1','0.0000000001'):
            with self.subTest(value=v),self.assertRaises(p.PumpError):self.grant(max_trade_sol=v)
        self.secret.assert_not_called()
    def test_paper_environment_blocks_even_exact_gui_review_before_secret(self):
        ident=self.seed()
        with patch.dict(os.environ,{'FREELLMAPI_READ_ONLY':'1'}),self.assertRaisesRegex(p.PumpError,'read_only_signing'):p.execute(self.home,ident,p.digest(self.body))
        self.secret.assert_not_called()
    def test_valid_grant_send_is_once_and_signature_journal_precedes_effect(self):
        ident=self.seed();self.grant();seen=[]
        def rpc(profile,method,args):
            if method=='sendTransaction':
                with s.database(self.home) as d:r=d.execute('SELECT * FROM operations WHERE id=?',(ident,)).fetchone()
                self.assertEqual(r['state'],'sending');self.assertTrue(r['signature']);seen.append(r['signature'])
            return self.rpc(profile,method,args)
        self.query.side_effect=rpc;first=p.execute(self.home,ident);second=p.execute(self.home,ident)
        self.assertEqual(first['state'],'submitted');self.assertTrue(second['reused']);self.assertEqual(len(seen),1)
        with p.db(self.home) as d:g=json.loads(d.execute('SELECT body FROM grants').fetchone()[0])
        self.assertEqual(g['trades'],1);self.assertEqual(g['spent_lamports'],6105000)
    def test_uncertain_send_is_not_replayed_or_replaced(self):
        ident=self.seed();self.grant()
        self.query.side_effect=lambda *a: (_ for _ in ()).throw(TimeoutError()) if a[1]=='sendTransaction' else self.rpc(*a)
        r=p.execute(self.home,ident);self.assertEqual(r['state'],'uncertain');self.assertTrue(p.execute(self.home,ident)['reused'])
        count=sum(c.args[1]=='sendTransaction' for c in self.query.call_args_list);self.assertEqual(count,1)
        other=copy.deepcopy(self.body);other['quote_id']=uuid.uuid4().hex;self.seed(other)
        with self.assertRaisesRegex(p.PumpError,'previous_signature'):p.execute(self.home,other['quote_id'])
        self.assertEqual(sum(c.args[1]=='sendTransaction' for c in self.query.call_args_list),1)
    def test_stop_and_expired_grant_block_without_reading_key(self):
        ident=self.seed();self.grant();p.stop(self.home)
        with self.assertRaisesRegex(p.PumpError,'new_entries_stopped'):p.execute(self.home,ident)
        self.secret.assert_not_called()
    def test_unknown_concentration_prevents_automated_buy(self):
        self.body['largest_token_accounts']=None;ident=self.seed();self.grant()
        with self.assertRaisesRegex(p.PumpError,'concentration_unverified'):p.execute(self.home,ident)
        self.secret.assert_not_called()
    def test_scope_and_rpc_genesis_are_checked_before_key(self):
        ident=self.seed();self.grant();self.query.side_effect=lambda *a:'wrong-chain' if a[1]=='getGenesisHash' else self.rpc(*a)
        with self.assertRaisesRegex(p.PumpError,'genesis'):p.execute(self.home,ident)
        self.secret.assert_not_called()
    def test_reconcile_records_confirmed_delta_exactly_once(self):
        ident=self.seed();self.grant();p.execute(self.home,ident)
        def rpc(profile,method,args):
            if method=='getSignatureStatuses':return {'value':[{'err':None,'confirmationStatus':'confirmed'}]}
            if method=='getTransaction':return {'meta':{'err':None,'fee':5000,'preBalances':[1000000000],'postBalances':[993895000],
                'preTokenBalances':[],'postTokenBalances':[{'mint':self.mint,'owner':self.user,'uiTokenAmount':{'amount':'200000'}}]}}
            return self.rpc(profile,method,args)
        self.query.side_effect=rpc;p.reconcile(self.home,ident);p.reconcile(self.home,ident)
        with p.db(self.home) as d:
            self.assertEqual(d.execute('SELECT COUNT(*) FROM positions').fetchone()[0],1)
            body=json.loads(d.execute('SELECT body FROM positions').fetchone()[0]);self.assertEqual(body['quantity'],'200000')
            self.assertTrue(body['grant_id'])
    def test_no_backtest_without_real_recorded_history(self):
        with self.assertRaisesRegex(p.PumpError,'insufficient_recorded_history'):p.backtest(self.home,{'mint':self.mint})
    def test_service_inference_outage_does_not_block_native_protection(self):
        cfg={**service.DEFAULT,'mode':'live','profile':'test','mints':[self.mint]};e=service.Engine(self.home,cfg,planner=Mock(side_effect=TimeoutError()))
        e.busy=True;e.protect=Mock();e.recover=Mock();e.tick();e.protect.assert_called_once();e.recover.assert_called_once()
    def test_disabled_service_cannot_start_or_reconcile(self):
        cfg={**service.DEFAULT,'profile':'test','mints':[self.mint]}
        with self.assertRaisesRegex(p.PumpError,'service_disabled'):service.Engine(self.home,cfg)
        with patch.object(service.subprocess,'Popen') as child,self.assertRaisesRegex(p.PumpError,'disabled'):service.start(self.home)
        child.assert_not_called()
        with patch.object(service.subprocess,'Popen') as child,self.assertRaises(p.PumpError):service.command(self.home,{'action':'start_paper'})
        child.assert_not_called()
    def test_node_environment_and_parameters_do_not_include_wallet_or_model_secret(self):
        with patch.dict(os.environ,{'PRIVATE_KEY':'DO-NOT-PASS','HOT_WALLET_SECRET':'DO-NOT-PASS','FREELLMAPI_SESSION_TOKEN':'DO-NOT-PASS'}),patch.object(p.subprocess,'run',return_value=Mock(returncode=0,stdout='{}')) as child:p.public_sdk(self.profile,'status')
        self.assertNotIn('DO-NOT-PASS',json.dumps(child.call_args.kwargs));self.secret.assert_not_called()
    def test_gui_exact_review_is_not_a_model_signing_argument(self):
        import pump_tools
        for n,_,fields,_ in pump_tools.SPECS:self.assertFalse(set(fields)&{'secret','private_key','review_hash','transaction','recipient'})

    def test_real_process_crash_after_journal_never_replays_intent(self):
        ident=self.seed();self.grant()
        script="""
import base64,os,sys
from unittest.mock import patch
from solders.keypair import Keypair
import pump_local as p, solana_local as s
def rpc(profile,method,args):
    if method=='getGenesisHash':return s.GENESIS['devnet']
    if method=='getBalance':return {'value':1000000000}
    if method=='getBlockHeight':return 100
    if method=='simulateTransaction':return {'value':{'err':None}}
    if method=='sendTransaction':os._exit(47) # crash before a fake RPC effect, NEVER network
    raise AssertionError(method)
with patch.object(s,'query',side_effect=rpc),patch('hl_credentials.get',return_value=str(Keypair.from_seed(bytes(range(32))))):p.execute(sys.argv[1],sys.argv[2])
"""
        child=subprocess.run([sys.executable,'-c',script,str(self.home),ident],capture_output=True,timeout=20)
        self.assertEqual(child.returncode,47,child.stderr.decode(errors='replace'))
        with s.database(self.home) as d:r=d.execute('SELECT * FROM operations WHERE id=?',(ident,)).fetchone()
        self.assertEqual(r['state'],'sending');self.assertTrue(r['signature'])
        self.assertTrue(p.execute(self.home,ident)['reused']);self.secret.assert_not_called()
        self.assertFalse(any(c.args[1]=='sendTransaction' for c in self.query.call_args_list))

    def test_expiry_and_rpc_scope_change_block_before_wallet_key(self):
        ident=self.seed();self.grant()
        with p.db(self.home) as d:g=json.loads(d.execute('SELECT body FROM grants').fetchone()[0]);g['expires']=time.time()-1;d.execute('UPDATE grants SET body=?',(json.dumps(g),))
        with self.assertRaisesRegex(p.PumpError,'expired'):p.execute(self.home,ident)
        self.secret.assert_not_called()

    def test_fee_impact_liquidity_concentration_and_budget_caps(self):
        self.grant()
        for changes,code in [({'fee_lamports':100001},'network_fee_cap'),({'impact_including_protocol_cost_bps':501},'price_impact'),
            ({'real_quote_reserves':'10'},'liquidity'),({'largest_token_accounts':[{'amount':'200000000'}]},'concentration'),
            ({'maximum_debit_lamports':100000001},'total_spend'),({'slippage_bps':101},'slippage')]:
            with self.subTest(code=code),self.assertRaisesRegex(p.PumpError,code):p.checked_grant(self.home,self.profile,{**self.body,**changes})
        self.secret.assert_not_called()

    def test_automatic_sell_does_not_adopt_other_grant_position(self):
        self.grant();body=self.fixture('sell');body['expected_output']='1000000'
        position={'id':'old','grant_id':'old-grant','quantity':body['input_amount']}
        with p.db(self.home) as d:d.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',('old','test',self.mint,'live','open',json.dumps(position)))
        with self.assertRaisesRegex(p.PumpError,'not_owned'):p.checked_grant(self.home,self.profile,body)
        self.secret.assert_not_called()

    def test_configuration_failure_rolls_back_new_grant(self):
        cfg={**service.DEFAULT,'mode':'live','profile':'test','mode':'live','mints':[self.mint]}
        settings={'max_trade_sol':'0.05','max_total_sol':'0.1','fee_reserve_sol':'0.02','max_loss_sol':'0.1','max_positions':3,'max_trades':10,'max_slippage_bps':100,'duration_minutes':60,'mints':[self.mint]}
        previous={**cfg,'mode':'disabled'};s.atomic(p.directory(self.home)/'service.json',previous)
        with patch.object(p,'atomic',side_effect=OSError('synthetic disk full')),self.assertRaises(OSError):service.authorize_config_local(self.home,cfg,settings)
        with p.db(self.home) as d:self.assertEqual(d.execute('SELECT count(*) FROM grants').fetchone()[0],0)
        self.assertEqual(service.load(self.home),previous);self.secret.assert_not_called()

    def test_production_has_no_virtual_pump_ledger_or_broker(self):
        self.assertFalse(hasattr(service.Engine,'record_paper'))
        with p.db(self.home) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='paper_trades'").fetchone())

    def test_entry_stop_does_not_skip_existing_exit_checks(self):
        cfg={**service.DEFAULT,'mode':'live','profile':'test','mints':[self.mint]};e=service.Engine(self.home,cfg)
        p.stop(self.home);e.protect=Mock();e.recover=Mock();e.launch_plan=Mock();e.tick()
        e.protect.assert_called_once();e.launch_plan.assert_not_called();self.secret.assert_not_called()

    def test_stale_heartbeat_is_not_reported_as_running_without_worker(self):
        p.set_state(self.home,'service',{'state':'running','heartbeat':time.time(),'phase':'old'})
        self.assertEqual(service.service_status(self.home)['state'],'stopped')

    def test_prepare_reuses_same_native_id_and_rejects_protection_changes(self):
        with p.db(self.home) as d:d.execute('INSERT INTO quotes VALUES(?,?,?)',(self.body['quote_id'],self.body['created'],json.dumps(self.body)))
        with self.assertRaisesRegex(p.PumpError,'explicit_take_profit'):p.prepare(self.home,{'quote_id':self.body['quote_id']})
        args={'quote_id':self.body['quote_id'],'take_profit_pct':'50','stop_loss_pct':'25'}
        first=p.prepare(self.home,args);second=p.prepare(self.home,args)
        self.assertEqual(first['id'],second['id']);self.assertTrue(second['reused'])
        with self.assertRaisesRegex(p.PumpError,'different_protection'):p.prepare(self.home,{**args,'take_profit_pct':'100'})
        with s.database(self.home) as d:self.assertEqual(d.execute('SELECT count(*) FROM operations').fetchone()[0],1)
        self.secret.assert_not_called()

    def test_migration_wait_preserves_managed_position_until_exit_receipt(self):
        cfg={**service.DEFAULT,'mode':'live','profile':'test','mints':[self.mint]};e=service.Engine(self.home,cfg)
        position={'id':'native-position','profile':'test','network':'devnet','mint':self.mint,'mode':'live','state':'open','quantity':'100000',
                  'cost_lamports':6105000,'take_profit_pct':'50','stop_loss_pct':'25'}
        with p.db(self.home) as db:db.execute('INSERT INTO positions VALUES(?,?,?,?,?,?)',('native-position','test',self.mint,'live','open',json.dumps(position)))
        e.quote_body=Mock(side_effect=p.PumpError('migration_pending'));e.prepare_execute=Mock()
        e.protect();self.assertEqual(e.positions()[0]['id'],'native-position');e.prepare_execute.assert_not_called()
        e.quote_body=Mock(return_value=({**self.body,'expected_output':'15000000'},'same-position-exit'))
        e.protect();e.prepare_execute.assert_called_once_with('same-position-exit')
        self.assertEqual(e.positions()[0]['id'],'native-position');self.secret.assert_not_called()

    def test_amm_spot_matches_sdk_virtual_reserve_without_double_counting_fees(self):
        snapshot={**self.body,'venue':'amm','pool_base_reserves':'1000','pool_quote_reserves':'10000','pool_virtual_quote_reserves':'-1000','pool_fee_buckets':'1000','decimals':9}
        with patch.object(p,'public_sdk',return_value=snapshot):value=p.inspect(self.home,{'mint':self.mint})
        self.assertEqual(value['price_sol'],'9');self.secret.assert_not_called();self.query.assert_not_called()

    def test_rpc_rotation_keeps_receipt_readable_but_invalidates_signing_scope(self):
        ident=self.seed();self.grant();p.execute(self.home,ident);self.secret.reset_mock()
        data=s.config(self.home);data['profiles']['test']['rpc_url']='https://devnet.helius-rpc.com/?api-key=SYNTHETIC_RPC';s.atomic(s.folder(self.home)/'profiles.json',data)
        def rpc(profile,method,args):
            if method=='getSignatureStatuses':return {'value':[{'err':None,'confirmationStatus':'confirmed'}]}
            if method=='getTransaction':return {'meta':{'err':None,'fee':5000,'preBalances':[1000000000],'postBalances':[993895000],
                'preTokenBalances':[],'postTokenBalances':[{'mint':self.mint,'owner':self.user,'uiTokenAmount':{'amount':'200000'}}]}}
            return self.rpc(profile,method,args)
        self.query.reset_mock();self.query.side_effect=rpc
        self.assertEqual(p.reconcile(self.home,ident)['state'],'confirmed');self.secret.assert_not_called()
        self.assertFalse(any(c.args[1]=='sendTransaction' for c in self.query.call_args_list))
        with self.assertRaisesRegex(p.PumpError,'grant_required'):p.checked_grant(self.home,p.profile(self.home),self.body)

if __name__=='__main__':unittest.main()
