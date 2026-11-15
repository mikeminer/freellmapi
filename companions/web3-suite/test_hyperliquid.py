import json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import hyperliquid_tools as h
import hyperliquid_signer as signer

MASTER='0x'+'1'*40
AGENT='0x'+'2'*40
VAULT='0x'+'3'*40

class HyperliquidTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup);self.home=Path(self.temp.name)
        self.p=h.configure(self.home,'main','mainnet',MASTER)
        self.a=SimpleNamespace(mode='auto',interactive=False,depth=0,session=self.home/'s.json',messages=[{'role':'user','content':'Trade only per configured scope'}])
        self.info=patch('hyperliquid_tools.info',side_effect=self.response);self.info.start();self.addCleanup(self.info.stop)
    def response(self,p,b):
        t=b['type']
        if t=='userAbstraction':return 'disabled'
        if t=='userRole':return {'role':'agent','data':{'user':MASTER}} if b['user']==AGENT else {'role':'vault' if b['user']==VAULT else 'user'}
        if t=='vaultDetails':return {'leader':MASTER,'isClosed':False,'allowDeposits':True,'maxWithdrawable':'100','followers':[]}
        if t=='meta':return {'universe':[{'name':'BTC','szDecimals':5,'maxLeverage':40}]}
        if t=='allMids':return {'BTC':'68000'}
        if t=='clearinghouseState':return {'marginSummary':{'accountValue':'1000'},'assetPositions':[]}
        if t=='spotClearinghouseState':return {'balances':[]}
        if t=='activeAssetData':return {'leverage':{'type':'cross','value':1}}
        if t in ('openOrders','frontendOpenOrders','extraAgents','userVaultEquities'):return []
        if t=='orderStatus':return {'status':'unknownOid'}
        raise AssertionError(t)
    def key(self,kind='agent'):
        data=h.config(self.home);data['profiles']['main'].update(signer=AGENT if kind=='agent' else MASTER,signer_kind=kind);h.save_config(self.home,data);h.secret_path(self.home,'main').write_bytes(b'not-a-real-key')
        return h.profile(self.home)
    def order(self,**extra):
        defaults={} if extra.get('reduce_only') else dict(take_profit='69000',stop_loss='67000',bracket_slippage_bps=30)
        return h.preview(self.a,self.home,dict(action='order',coin='BTC',side='buy',size='.001',order_kind='limit',price='68000')|defaults|extra)['preview']
    def settings(self):return {'coins':'BTC','max_order_usd':'100','max_exposure_usd':'500','max_loss_usd':'100','max_turnover_usd':'1000','max_leverage':'2','max_orders_hour':'3','interval':'60','duration_minutes':'60','strategy':'Only trade after confirmed signal; otherwise HOLD.'}
    def arm(self):self.key();return h.arm(self.home,'main',self.settings(),self.a.session)['grant']
    def test_profiles_network_vault_and_no_secret_in_public_config(self):
        h.configure(self.home,'vault-test','testnet',MASTER,VAULT,'vault')
        self.assertEqual(h.profile(self.home,'vault-test')['target'],VAULT)
        self.assertEqual(h.profile(self.home,'main')['network'],'mainnet')
        with self.assertRaises(h.HLProblem):h.configure(self.home,'main','testnet',MASTER)
        self.assertNotIn('private_key',json.dumps(h.status(self.home)))
    def test_read_uses_target_not_api_signer(self):
        self.key();h.configure(self.home,'vault','mainnet',MASTER,VAULT,'vault')
        with patch('hyperliquid_tools.info',side_effect=self.response) as api:
            h.read(self.home,{'profile':'vault','resource':'account'})
        self.assertTrue(all(call.args[1].get('user')==VAULT for call in api.call_args_list))
    def test_positions_resource_is_supported_explicitly(self):
        with patch('hyperliquid_tools.info',side_effect=self.response) as api:r=h.read(self.home,{'resource':'positions'})
        self.assertEqual(r['data']['positions'],[]);self.assertEqual(api.call_count,1)
    def test_unified_account_read_does_not_mislabel_spot_as_missing_funds(self):
        def reply(p,b):
            if b['type']=='userAbstraction':return 'unifiedAccount'
            if b['type']=='clearinghouseState':return {'marginSummary':{'accountValue':'0'},'assetPositions':[]}
            if b['type']=='spotClearinghouseState':return {'balances':[{'coin':'USDC','total':'15'}]}
            return self.response(p,b)
        with patch('hyperliquid_tools.info',side_effect=reply):r=h.read(self.home,{'resource':'account'})
        context=r['data']['balance_context'];self.assertEqual(context['account_mode'],'unifiedAccount')
        self.assertEqual(context['balance_source'],'spotClearinghouseState');self.assertEqual(context['spot_usdc'],'15')
        self.assertTrue(context['execution_supported'])
    def test_unsupported_margin_mode_blocks_before_grant_or_wallet_access(self):
        self.key()
        for mode in ('portfolioMargin','dexAbstraction','unexpected'):
            def reply(p,b):return mode if b['type']=='userAbstraction' else self.response(p,b)
            with patch('hyperliquid_tools.info',side_effect=reply),patch('hl_credentials.get') as key,self.assertRaises(h.HLProblem) as exc:
                h.arm(self.home,'main',self.settings(),self.a.session)
            self.assertIn(exc.exception.code,('unsupported_account_mode','account_mode_unverified'));key.assert_not_called()
        with h.database(self.home) as db:self.assertIsNone(h.current_grant(db,'main'))
    def test_prose_tool_call_is_not_completed_and_pauses_automation(self):
        self.arm()
        self.a.turn_outcome='completed';self.a.messages=[{'role':'user','content':'Ciclo trading Hyperliquid autorizzato localmente. Read data.'},{'role':'assistant','content':'I will read now: hyperliquid_read_resource coin=BTC'}]
        result=h.assess_cycle(self.a,self.home,'main');self.assertFalse(result['ok']);self.assertIn('no_verified_live_reads',result['reasons'])
        h.finish_cycle(self.home,'main',result['ok'])
        with h.database(self.home) as db:self.assertFalse(h.current_grant(db,'main')['active'])
    def test_hold_requires_verified_read_from_current_cycle(self):
        self.arm();self.a.turn_outcome='completed'
        self.a.messages=[{'role':'user','content':'Ciclo trading Hyperliquid autorizzato localmente. Inspect.'},{'role':'assistant','tool_calls':[{'id':'r1','function':{'name':'hyperliquid_read'}}]},{'role':'tool','tool_call_id':'r1','content':json.dumps({'resource':'account','data':{'example':True}})},{'role':'assistant','content':'HOLD: no signal.'}]
        self.assertTrue(h.assess_cycle(self.a,self.home,'main')['ok'])
        self.a.messages.append({'role':'user','content':'Ciclo trading Hyperliquid autorizzato localmente. Next.'});self.a.messages.append({'role':'assistant','content':'HOLD'})
        self.assertFalse(h.assess_cycle(self.a,self.home,'main')['ok'])
    def test_local_settings_required_and_decimal_comma_without_hidden_defaults(self):
        values=self.settings();values['max_order_usd']=''
        with self.assertRaises(h.HLProblem) as exc:h.validate_settings(values)
        self.assertEqual(exc.exception.code,'setting_required');self.assertIn('Notional massimo',str(exc.exception))
        values['max_order_usd']='10,50';self.assertEqual(h.validate_settings(values)['max_order_usd'],'10.5')
        values['max_order_usd']='1.000,50'
        with self.assertRaises(h.HLProblem) as exc:h.validate_settings(values)
        self.assertEqual(exc.exception.code,'setting_format')
    def test_auto_strategy_requires_no_user_strategy_text(self):
        settings=self.settings()|{'strategy_mode':'auto','strategy':''}
        clean=h.validate_settings(settings);self.assertEqual(clean['strategy_mode'],'auto');self.assertEqual(clean['strategy'],h.AUTO_STRATEGY)
    def test_preview_reports_multiple_correctable_fields_in_one_response(self):
        args={'action':'order','coin':'BTC','side':'buy','size':'.000144','order_kind':'market','take_profit':'69000','stop_loss':'67000'}
        with self.assertRaises(h.HLProblem) as exc:h.preview(self.a,self.home,args)
        self.assertEqual(exc.exception.code,'order_fields_invalid')
        for key in ('size_precision','slippage_required','bracket_slippage'):self.assertIn(key,str(exc.exception))
    def test_unknown_group_is_reconciled_once_and_freezes_without_model_polling(self):
        self.arm()
        with h.database(self.home) as db:
            g=h.current_grant(db,'main');g['pending']='cycle1';db.execute('UPDATE grants SET body=?',(json.dumps(g),));db.commit()
        self.a.hyperliquid_controller='main'
        row=self.order()
        with h.database(self.home) as db:db.execute("UPDATE actions SET state='uncertain' WHERE id=?",(row['id'],));db.commit()
        self.a.tool=Mock(return_value=json.dumps({'receipt':row,'resent':False}))
        result=h.reconcile_hazard(self.a,self.home,'main');h.reconcile_hazard(self.a,self.home,'main')
        self.assertIn(row['id'],result);self.a.tool.assert_called_once_with('hyperliquid_operations',{'id':row['id']})
        with h.database(self.home) as db:self.assertFalse(h.current_grant(db,'main')['active'])
    def test_accepted_group_reports_native_receipt_without_another_model_request(self):
        self.key();h.arm(self.home,'main',self.settings()|{'strategy_mode':'auto','strategy':''},self.a.session)
        with h.database(self.home) as db:
            g=h.current_grant(db,'main');g['pending']='cycle1';db.execute('UPDATE grants SET body=?',(json.dumps(g),));db.commit()
        self.a.hyperliquid_controller='main'
        h.strategy(self.a,self.home,{'action':'update','plan':'Paper scenario chosen from observed prices; no profit asserted.'})
        row=h.preview(self.a,self.home,{'action':'order','coin':'BTC','side':'buy','size':'.001','order_kind':'limit','price':'68000','take_profit':'69000','stop_loss':'67000','bracket_slippage_bps':30})['preview']
        response={'protection':{'state':'pending_entry'},'grouped_tpsl':True,'sdk_response':{'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':1}},'waitingForFill','waitingForFill']}}}}
        with h.database(self.home) as db:db.execute("UPDATE actions SET state='accepted',response=? WHERE id=?",(json.dumps(response),row['id']));db.commit()
        self.a.tool=Mock(return_value=json.dumps({'receipt':row,'resent':False}))
        result=h.verified_group_result(self.a,self.home,'main');self.assertIn(row['id'],result);self.assertIn(row['body']['bracket']['sl_cloid'],result);self.a.tool.assert_called_once()
    def test_bracket_direction_prices_and_client_ids_persist(self):
        args={'action':'order','coin':'BTC','side':'buy','size':'.001','order_kind':'limit','price':'68000','take_profit':'69000','stop_loss':'67000','bracket_slippage_bps':30}
        row=h.preview(self.a,self.home,args)['preview'];again=h.preview(self.a,self.home,args)['preview']
        self.assertEqual(row['id'],again['id']);self.assertEqual(row['body']['bracket'],again['body']['bracket'])
        orders=signer.order_requests(row['body']);self.assertEqual(len(orders),3)
        self.assertTrue(orders[0]['is_buy']);self.assertFalse(orders[0]['reduce_only'])
        self.assertTrue(all(not o['is_buy'] and o['reduce_only'] for o in orders[1:]));self.assertEqual([o['order_type']['trigger']['tpsl'] for o in orders[1:]],['tp','sl'])
        with self.assertRaises(h.HLProblem):h.preview(self.a,self.home,args|{'stop_loss':'70000'})
    def test_market_bracket_covers_worst_entry_price_not_just_mid(self):
        args={'action':'order','coin':'BTC','side':'buy','size':'.001','order_kind':'market','slippage_bps':100,'take_profit':'68100','stop_loss':'67000','bracket_slippage_bps':30}
        with self.assertRaises(h.HLProblem) as exc:h.preview(self.a,self.home,args)
        self.assertEqual(exc.exception.code,'bracket_direction');self.assertIn('68680',str(exc.exception))
        good=h.preview(self.a,self.home,args|{'take_profit':'69000'})['preview'];self.assertEqual(good['state'],'prepared')
    def test_auto_strategy_blocks_unprotected_new_position(self):
        self.key();g=h.arm(self.home,'main',self.settings()|{'strategy_mode':'auto','strategy':''},self.a.session)['grant'];b=self.order()['body']
        with h.database(self.home) as db,self.assertRaises(h.HLProblem) as exc:h.guard_grant(h.profile(self.home),g,{k:v for k,v in b.items() if k!='bracket'},self.a.session,db)
        self.assertEqual(exc.exception.code,'protection_required')
    def test_partial_bracket_cannot_be_called_rejected_or_successful(self):
        raw={'status':'ok','response':{'data':{'statuses':[{'filled':{'oid':1}}, {'error':'rejected'},'waitingForFill']}}}
        self.assertEqual(signer.response_state(raw,'order',True),'partial')
        ok={'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':1}},'waitingForFill','waitingForFill']}}}
        self.assertEqual(signer.response_state(ok,'order',True),'accepted')
    def test_persistent_strategy_plan_never_changes_grant(self):
        self.key();h.arm(self.home,'main',self.settings()|{'strategy_mode':'auto','strategy':''},self.a.session)
        with h.database(self.home) as db:before=h.current_grant(db,'main')
        h.strategy(self.a,self.home,{'action':'update','plan':'Observe closed candles and use HOLD until a verified setup appears.'})
        other=SimpleNamespace(**self.a.__dict__);r=h.strategy(other,self.home,{'action':'inspect'})
        self.assertEqual(r['strategy']['current_plan']['classification'],'model_proposal_unverified')
        with h.database(self.home) as db:self.assertEqual(h.current_grant(db,'main'),before)
    def test_grouped_entry_tp_sl_uses_one_sdk_submission(self):
        self.key();h.arm(self.home,'main',self.settings()|{'strategy_mode':'auto','strategy':''},self.a.session)
        row=h.preview(self.a,self.home,{'action':'order','coin':'BTC','side':'buy','size':'.001','order_kind':'limit','price':'68000','take_profit':'69000','stop_loss':'67000','bracket_slippage_bps':30})['preview']
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{}')):h.submit(self.a,self.home,row['id'])
        raw={'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':1}},'waitingForFill','waitingForFill']}}}
        with patch('hl_credentials.get',return_value='local-fixture-only'),patch('eth_account.Account.from_key',return_value=SimpleNamespace(address=AGENT)),patch('hyperliquid.exchange.Exchange') as exchange:
            exchange.return_value.bulk_orders.return_value=raw;r=signer.execute(self.home,row['id'])
        self.assertEqual(r['receipt']['state'],'accepted');exchange.return_value.order.assert_not_called()
        exchange.return_value.bulk_orders.assert_called_once();args=exchange.return_value.bulk_orders.call_args
        self.assertEqual(args.kwargs['grouping'],'normalTpsl');self.assertEqual(len(args.args[0]),3)
    def test_offline_sdk_signature_covers_group_network_and_vault(self):
        from eth_account import Account
        from hyperliquid.utils.signing import order_request_to_order_wire,order_wires_to_order_action,sign_l1_action,recover_agent_or_user_from_l1_action
        body=self.order()['body'];body['bracket']={'take_profit':'69000','stop_loss':'67000','tp_limit':'68793','sl_limit':'66799','tp_cloid':'0x'+'a'*32,'sl_cloid':'0x'+'b'*32}
        action=order_wires_to_order_action([order_request_to_order_wire(r,0) for r in signer.order_requests(body)],grouping='normalTpsl')
        wallet=Account.from_key(bytes([19])*32) # synthetic offline fixture; never user's credential or network
        nonce=1700000000000;expiry=nonce+15000
        for mainnet in (False,True):
            signature=sign_l1_action(wallet,action,VAULT,nonce,expiry,mainnet)
            recovered=recover_agent_or_user_from_l1_action(action,signature,VAULT,nonce,expiry,mainnet)
            self.assertEqual(recovered,wallet.address)
            changed=recover_agent_or_user_from_l1_action(action,signature,MASTER,nonce,expiry,mainnet)
            self.assertNotEqual(changed,wallet.address)
    def test_reduce_only_protective_orders_do_not_triple_count_exposure(self):
        g=self.arm();b=self.order()['body']
        def response(p,a):
            if a['type']=='frontendOpenOrders':return [{'oid':1,'coin':'BTC','sz':'.001','limitPx':'69000','reduceOnly':True},{'oid':2,'coin':'BTC','sz':'.001','limitPx':'67000','reduceOnly':True}]
            return self.response(p,a)
        with h.database(self.home) as db,patch('hyperliquid_tools.info',side_effect=response):
            self.assertEqual(h.guard_grant(h.profile(self.home),g|{'max_exposure_usd':'70'},b,self.a.session,db),'68')
    def test_unsigned_preview_deduplicates_canonical_decimal_and_no_sdk_exchange(self):
        with patch('hyperliquid.exchange.Exchange') as exchange:
            one=self.order();two=h.preview(self.a,self.home,{'action':'order','coin':'BTC','side':'buy','size':'0.00100','order_kind':'limit','price':'68000.00','take_profit':'69000','stop_loss':'67000','bracket_slippage_bps':30})['preview']
        self.assertEqual(one['id'],two['id']);self.assertEqual(one['body']['cloid'],two['body']['cloid']);exchange.assert_not_called()
    def test_expired_unsigned_preview_refreshes_same_ids_without_resending(self):
        args={'action':'order','coin':'BTC','side':'buy','size':'.001','order_kind':'limit','price':'68000','take_profit':'69000','stop_loss':'67000','bracket_slippage_bps':30}
        one=h.preview(self.a,self.home,args)['preview']
        with h.database(self.home) as db:db.execute('UPDATE actions SET created=? WHERE id=?',(time.time()-180,one['id']));db.commit()
        with patch('subprocess.run') as child:two=h.preview(self.a,self.home,args)['preview']
        self.assertEqual(one['id'],two['id']);self.assertEqual(one['body']['cloid'],two['body']['cloid']);self.assertEqual(one['body']['bracket'],two['body']['bracket'])
        self.assertEqual(two['state'],'prepared');self.assertTrue(two['response']['never_authorized']);self.assertLess(time.time()-two['created'],5);child.assert_not_called()
    def test_precision_and_market_slippage(self):
        for patch_args,code in [({'size':'.000001'},'size_precision'),({'price':'12345.6'},'price_precision'),({'order_kind':'market'},'slippage_required')]:
            args={'action':'order','coin':'BTC','side':'buy','size':'.001','price':'68000','order_kind':'limit','take_profit':'69000','stop_loss':'67000','bracket_slippage_bps':30}|patch_args
            with self.assertRaises(h.HLProblem) as exc:h.preview(self.a,self.home,args)
            self.assertEqual(exc.exception.code,code)
    def test_new_entry_minimum_notional_correctable_without_exchange_call(self):
        args={'action':'order','coin':'BTC','side':'buy','size':'.00014','price':'68000','order_kind':'limit','take_profit':'69000','stop_loss':'67000','bracket_slippage_bps':30}
        with patch('hyperliquid.exchange.Exchange') as sdk,self.assertRaises(h.HLProblem) as exc:h.preview(self.a,self.home,args)
        self.assertEqual(exc.exception.code,'minimum_notional');sdk.assert_not_called()
        row=h.preview(self.a,self.home,args|{'size':'.00015'})['preview']
        self.assertEqual(row['body']['limit_notional_usd'],'10.2')
        row=h.preview(self.a,self.home,{k:v for k,v in args.items() if k not in ('take_profit','stop_loss','bracket_slippage_bps')}|{'reduce_only':True})['preview']
        self.assertTrue(row['body']['reduce_only'])
    def test_auto_mode_alone_cannot_sign(self):
        self.key();row=self.order()
        with patch('subprocess.run') as child:
            result=h.dispatch(self.a,'hyperliquid_submit',{'id':row['id']},self.home)
        self.assertEqual(result['code'],'local_authorization_required');child.assert_not_called()
    def test_scope_loss_exposure_and_flat_account_high_leverage(self):
        g=self.arm();b=self.order()['body'];p=h.profile(self.home)
        with h.database(self.home) as db:
            for grant,body,code in [(g|{'scope':'wrong'},b,'auto_scope'),(g|{'baseline_equity':'1200'},b,'loss_stop'),(g,b|{'notional_usd':'101'},'order_cap'),(g|{'max_exposure_usd':'60'},b,'exposure_cap')]:
                with self.assertRaises(h.HLProblem) as exc:h.guard_grant(p,grant,body,self.a.session,db)
                self.assertEqual(exc.exception.code,code)
            def high(p,a):return {'leverage':{'value':40}} if a['type']=='activeAssetData' else self.response(p,a)
            with patch('hyperliquid_tools.info',side_effect=high),self.assertRaises(h.HLProblem) as exc:h.guard_grant(p,g,b,self.a.session,db)
            self.assertEqual(exc.exception.code,'existing_leverage_cap')
    def test_auto_submit_ipc_contains_only_id_never_key_and_single_authorization(self):
        self.arm();row=self.order()
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{"state":"accepted"}')) as child:
            result=h.submit(self.a,self.home,row['id']);again=h.submit(self.a,self.home,row['id'])
        self.assertEqual(result['state'],'accepted');self.assertTrue(again['reused']);self.assertEqual(child.call_count,1)
        self.assertEqual(json.loads(child.call_args.kwargs['input']),{'id':row['id']})
        self.assertNotIn('secret',str(child.call_args));self.assertNotIn('not-a-real-key',str(child.call_args))
    def test_pre_signing_guard_is_blocked_not_left_authorized_and_never_resent(self):
        for code in ('read_only_signing_forbidden',):
            with self.subTest(code=code):
                self.arm();self.a.messages.append({'role':'user','content':'synthetic separate intent '+code});row=self.order()
                raw={'ok':False,'code':code,'sent':False}
                with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps(raw))) as child:
                    result=h.submit(self.a,self.home,row['id']);again=h.submit(self.a,self.home,row['id'])
                self.assertEqual(result['receipt']['state'],'blocked');self.assertFalse(result['sent'])
                self.assertTrue(again['reused']);self.assertEqual(child.call_count,1)
    def test_unknown_signer_failure_does_not_falsely_clear_ambiguity(self):
        self.arm();row=self.order()
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout=json.dumps({'ok':False,'code':'unknown_provider_failure','sent':False}))):
            h.submit(self.a,self.home,row['id'])
        with h.database(self.home) as db:self.assertEqual(db.execute('SELECT state FROM actions WHERE id=?',(row['id'],)).fetchone()[0],'authorized')
    def test_timeout_is_uncertain_and_no_replacement_action(self):
        import subprocess
        self.arm();one=self.order()
        with patch('subprocess.run',side_effect=subprocess.TimeoutExpired('worker',45)) as child:
            r=h.submit(self.a,self.home,one['id']);h.submit(self.a,self.home,one['id'])
        self.assertEqual(r['state'],'uncertain');self.assertEqual(child.call_count,1)
        self.a.messages.append({'role':'user','content':'continue'})
        two=self.order()
        with self.assertRaises(h.HLProblem) as exc:h.submit(self.a,self.home,two['id'])
        self.assertEqual(exc.exception.code,'outcome_unknown')
    def test_stop_revocation_blocks_worker_before_decryption(self):
        self.arm();row=self.order()
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{}')):h.submit(self.a,self.home,row['id'])
        h.stop(self.home)
        with patch('hl_credentials.get') as secret:
            result=signer.execute(self.home,row['id'])
        self.assertFalse(result['sent']);secret.assert_not_called()
    def test_worker_places_one_sdk_order_and_duplicate_never_decrypts_again(self):
        self.arm();row=self.order()
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{}')):h.submit(self.a,self.home,row['id'])
        raw={'status':'ok','response':{'type':'order','data':{'statuses':[{'resting':{'oid':77}},'waitingForFill','waitingForFill']}}}
        with patch('hl_credentials.get',return_value='test-only-signer-material') as secret,patch('eth_account.Account.from_key',return_value=SimpleNamespace(address=AGENT)),patch('hyperliquid.exchange.Exchange') as exchange:
            exchange.return_value.bulk_orders.return_value=raw
            r=signer.execute(self.home,row['id']);again=signer.execute(self.home,row['id'])
        self.assertEqual(r['receipt']['state'],'accepted');self.assertFalse(again['sent']);self.assertEqual(secret.call_count,1)
        exchange.return_value.order.assert_not_called();exchange.return_value.bulk_orders.assert_called_once()
        args=exchange.return_value.bulk_orders.call_args
        self.assertEqual(args.kwargs['grouping'],'normalTpsl');self.assertEqual(args.args[0][0]['cloid'].to_raw(),row['body']['cloid'])
        self.assertNotIn('test-only-signer-material',json.dumps(r));self.assertFalse(r['private_key_exposed'])
    def test_actual_signer_process_public_ipc_no_authorization_no_exchange(self):
        import subprocess,sys
        row=self.order()
        child=subprocess.run([sys.executable,'-X','utf8',str(Path(signer.__file__)),'--home',str(self.home)],input=json.dumps({'id':row['id']}),text=True,capture_output=True,timeout=15)
        self.assertEqual(child.returncode,0);r=json.loads(child.stdout)
        self.assertFalse(r['sent']);self.assertEqual(r['receipt']['state'],'prepared');self.assertFalse(h.secret_path(self.home,'main').exists())
    def test_agent_cannot_move_vault_funds(self):
        self.key();row=h.preview(self.a,self.home,{'action':'vault_deposit','vault':VAULT,'amount':'2'})['preview']
        with patch('questions.ask',return_value={'answer':'Confermo invio'}),patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{}')):
            self.a.interactive=True;h.submit(self.a,self.home,row['id'])
        with patch('hl_credentials.get') as secret:r=signer.execute(self.home,row['id'])
        self.assertEqual(r['code'],'owner_signature_required');secret.assert_not_called()
    def test_controller_claim_once_finish_and_expiry(self):
        self.arm()
        with h.database(self.home) as db:
            g=h.current_grant(db,'main');g['next_due']=0;db.execute('UPDATE grants SET body=?',(json.dumps(g),));db.commit()
        self.assertEqual(h.poll(self.home,self.a.session),'/_hyperliquid main');self.assertIsNone(h.poll(self.home,self.a.session))
        text=h.controller_prompt(self.home,'main',self.a.session);self.assertIn('Only trade',text)
        h.finish_cycle(self.home,'main',True)
        with h.database(self.home) as db:self.assertIsNone(h.current_grant(db,'main')['pending'])
    def test_unknown_order_reconciliation_does_not_resubmit(self):
        self.arm();row=self.order()
        with h.database(self.home) as db:db.execute("UPDATE actions SET state='uncertain' WHERE id=?",(row['id'],));db.commit()
        with patch('subprocess.run') as child:r=h.operations(self.home,row['id'])
        self.assertEqual(r['receipt']['state'],'uncertain');self.assertFalse(r['resent']);child.assert_not_called()
    def test_dpapi_roundtrip_local_encrypted_bytes(self):
        from hl_credentials import put,get
        secret='test-only-ephemeral-string';p=self.home/'roundtrip.dpapi';put(p,secret)
        self.assertNotIn(secret.encode(),p.read_bytes());self.assertEqual(get(p),secret)
    def test_model_schema_has_no_credentials_and_disallows_outside_controller_tools(self):
        from freellmapi import Agent,TOOLS
        tools=[t for t in TOOLS if t['function']['name'] in h.NAMES]
        fields=set().union(*(t['function']['parameters']['properties'].keys() for t in tools))
        self.assertFalse(fields&{'key','secret','private_key','seed','mnemonic'})
        a=Agent(self.home,'http://localhost','fake','auto');a.hyperliquid_controller='main'
        with self.assertRaises(PermissionError):a.tool('shell',{'command':'echo forbidden'})
    def test_controller_context_isolated_and_model_loop_bounded(self):
        from freellmapi import Agent
        a=Agent(self.home,'http://localhost','fake','auto',interactive=False);a.mode='auto';a.session=self.a.session;a.hyperliquid_controller='main';a.messages=[{'role':'user','content':'Unrelated older game context should stay outside trading payload'}]
        responses=[{'choices':[{'message':{'role':'assistant','tool_calls':[{'id':str(i),'type':'function','function':{'name':'hyperliquid_read','arguments':json.dumps({'resource':'candles','coin':'BTC','limit':i+1})}}]}}]} for i in range(8)]
        with patch.object(a,'api',side_effect=responses) as api,patch.object(a,'_tool',side_effect=lambda n,args:json.dumps({'observed_limit':args.get('limit')})),patch('freellmapi.output'):
            a.turn('Ciclo trading Hyperliquid autorizzato localmente. Leggi candele; HOLD in assenza di segnale.')
        self.assertEqual(api.call_count,8);self.assertEqual(a.turn_outcome,'incomplete')
        for call in api.call_args_list:
            payload=call.args[1];self.assertNotIn('Unrelated older',json.dumps(payload['messages']))
            self.assertTrue(all(t['function']['name'] in h.NAMES-{'hyperliquid_panel','hyperliquid_select'} for t in payload['tools']))
    def test_trading_strategy_never_forces_general_coordinator_not_in_tools(self):
        from freellmapi import Agent
        a=Agent(self.home,'http://localhost','fake','auto',interactive=False);a.session=self.a.session;a.hyperliquid_controller='main'
        response={'choices':[{'message':{'role':'assistant','content':'HOLD'}}]}
        read={'choices':[{'message':{'role':'assistant','tool_calls':[{'id':'read-1','type':'function','function':{'name':'hyperliquid_read','arguments':'{"resource":"account"}'}}]}}]}
        with patch.object(a,'api',side_effect=[read,response]) as api,patch.object(a,'_tool',return_value='{"resource":"account","data":{}}'),patch('freellmapi.output'):
            a.turn('Ciclo trading Hyperliquid autorizzato localmente. Scegli e salva una strategia con hyperliquid_strategy; HOLD se non hai dati.')
        payload=api.call_args.args[1];choice=payload.get('tool_choice')
        if isinstance(choice,dict):self.assertIn(choice['function']['name'],{t['function']['name'] for t in payload['tools']})
        self.assertNotEqual(choice,{'type':'function','function':{'name':'coordinator_state'}})
    def test_controller_provider_outage_pauses_without_indefinite_recovery(self):
        from freellmapi import Agent
        from model_health import RateLimited
        a=Agent(self.home,'http://localhost','fake','auto',interactive=False);a.session=self.a.session;a.mode='auto';a.hyperliquid_controller='main'
        a.messages=[{'role':'user','content':'Ciclo trading Hyperliquid autorizzato localmente. Preserve this objective.'}]
        with patch.object(a,'_request_attempt',side_effect=RateLimited('cooldown')),patch.object(a,'wait_for_recovery') as wait,self.assertRaises(RuntimeError):
            a._request_with_recovery({'model':'auto','messages':[]})
        wait.assert_not_called();self.assertIn('Preserve this objective',self.a.session.read_text(encoding='utf-8'))
    def test_stop_latch_survives_busy_journal_before_any_network_request(self):
        g=self.arm();b=self.order()['body'];p=h.profile(self.home)
        with h.database(self.home) as lock:
            lock.execute('BEGIN IMMEDIATE');out=h.stop(self.home)
            self.assertTrue(out['automation_stopped']);self.assertFalse(out['db_updated'])
            with patch('hyperliquid_tools.info') as api,self.assertRaises(h.HLProblem) as exc:h.guard_grant(p,g,b,self.a.session,lock)
            self.assertEqual(exc.exception.code,'auto_stopped');api.assert_not_called();lock.rollback()
    def test_order_cap_uses_market_value_when_sell_limit_is_lower(self):
        g=self.arm();b=h.preview(self.a,self.home,{'action':'order','coin':'BTC','side':'sell','size':'.001','order_kind':'limit','price':'67000','take_profit':'66000','stop_loss':'69000','bracket_slippage_bps':30})['preview']['body']
        self.assertEqual(b['notional_usd'],'68');self.assertEqual(b['limit_notional_usd'],'67')
        with h.database(self.home) as db,self.assertRaises(h.HLProblem) as exc:h.guard_grant(h.profile(self.home),g|{'max_order_usd':'67'},b,self.a.session,db)
        self.assertEqual(exc.exception.code,'order_cap')
    def test_stop_during_sdk_initialization_prevents_post(self):
        self.arm();row=self.order()
        with patch('subprocess.run',return_value=SimpleNamespace(returncode=0,stdout='{}')):h.submit(self.a,self.home,row['id'])
        exchange=Mock()
        def create(*args,**kwargs):h.stop(self.home);return exchange
        with patch('hl_credentials.get',return_value='fake-local-only'),patch('eth_account.Account.from_key',return_value=SimpleNamespace(address=AGENT)),patch('hyperliquid.exchange.Exchange',side_effect=create):r=signer.execute(self.home,row['id'])
        self.assertEqual(r['receipt']['state'],'blocked');self.assertFalse(r['receipt']['response']['send_attempted']);exchange.order.assert_not_called()

if __name__=='__main__':unittest.main()
