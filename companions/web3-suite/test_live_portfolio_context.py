"""Regressions from Seeker: Unified funds, stale ETH and wrong error reasons."""
import copy
import io
import json
import tempfile,time
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch

import freellmapi as f
import hyperliquid_tools as h
import market_routing as m
from session_store import Sessions

RISK={'version':'usdc-unified-v1','account_mode':'unifiedAccount','collateral_usdc':'21.37871164',
      'available_usd':'21.37871164','positions':[],'orders':[],'dexs_scanned':11}
SNAP={'profile':'mainnet-master','network':'mainnet','queried_address':'0x'+'1'*40,
      'source':'https://api.hyperliquid.xyz/info','retrieved_ms':123456,'resource':'snapshot',
      'data':{'risk':RISK,'perps':{'marginSummary':{'accountValue':'0.0'}},'orders':[],
              'balance_context':{'account_mode':'unifiedAccount','spot_usdc':'21.37871164'},
              'asset':{'coin':'BTC','availableToTrade':['21.378711','21.378711']}}}


class LivePortfolioTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        h.configure(self.home,'mainnet-master','mainnet','0x'+'1'*40)

    def evidence(self):return [{'tool':'hyperliquid_read','arguments':{'resource':'snapshot','coin':'BTC'},'result':copy.deepcopy(SNAP)}]

    def reply(self,text):return {'choices':[{'message':{'role':'assistant','content':text},'finish_reason':'stop'}]}

    def run_turn(self,prompt,responses,history=()):
        with patch.object(f,'HOME',self.home):
            agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic-only','auto')
            store=Sessions(self.home);store.activate(agent,store.create(self.home));agent.messages.extend(copy.deepcopy(history))
            agent.auto_skills=False;agent.context_limit=200000;agent.streaming=False
            def read(home,args):
                value=copy.deepcopy(SNAP);value['resource']=args['resource']
                if args['resource']=='risk':value['data']=copy.deepcopy(RISK)
                return value
            with patch.object(agent,'api',side_effect=responses) as api,patch.object(h,'read',side_effect=read) as reads, \
                 patch.object(f,'output'),redirect_stdout(io.StringIO()):agent.turn(prompt)
            return agent,api,reads

    def test_balance_request_is_live_without_pair_or_service_name(self):
        for prompt in ('quanti fondi ci sono?','qual è il saldo del wallet?','mostra posizioni aperte e ordini aperti'):
            with self.subTest(prompt=prompt):
                agent,api,reads=self.run_turn(prompt,[self.reply('Collaterale 21.37871164 USDC; nessuna posizione o ordine aperto.')])
                self.assertEqual(reads.call_count,1);self.assertEqual(reads.call_args.args[1]['resource'],'risk')
                self.assertEqual(agent.current_account_state['collateral_usdc'],'21.37871164')
                self.assertEqual(agent.turn_outcome,'completed')
                payload=api.call_args.args[1]
                self.assertIn('LATEST NATIVE ACCOUNT STATE',payload['messages'][-1]['content'])

    def test_followup_inherits_btc_from_user_never_eth_from_model(self):
        history=[{'role':'user','content':'trada ETH/USDC su Hyperliquid'},
                 {'role':'user','content':'ora esegui un trade BTC/USDC'},
                 {'role':'assistant','content':'Memoria: ordine ETH filled, continua su ETH.'}]
        for prompt in ('esegui trade','usa tutti e i 21 usdc'):
            request=m.intent(prompt,history)
            self.assertEqual(request['coin'],'BTC');self.assertTrue(request['trading'])
        self.assertIsNone(m.intent('esegui trade',history+[{'role':'user','content':'Ora scrivimi una ricetta per il pane'}]))
        self.assertIsNone(m.intent('saldo del conto Binance'))
        self.assertEqual(m.intent('voglio tradare sul pair btc')['coin'],'BTC')

    def test_same_session_followup_refreshes_native_account(self):
        history=[{'role':'user','content':'esegui trade BTC/USDC su Hyperliquid'},
                 {'role':'assistant','content':'Non hai abbastanza fondi.'}]
        agent,api,reads=self.run_turn('usa tutti e i 21 usdc',[self.reply('Leggo 21.37871164 USDC; serve chiarire margine o nozionale.')],history)
        self.assertEqual(agent.current_market_request['coin'],'BTC');reads.assert_called_once()
        self.assertEqual(reads.call_args.args[1]['coin'],'BTC')

    def test_zero_perps_does_not_replace_unified_collateral(self):
        state=m.account_state(self.evidence())
        self.assertEqual(state['collateral_usdc'],'21.37871164');self.assertEqual(state['positions'],[])
        self.assertIn('21.37871164',m.render_state(state))
        self.assertIsNotNone(m.response_issue('Il conto non ha fondi.',{},self.evidence()))
        self.assertIsNone(m.response_issue('I fondi sono 21.37871164 USDC, non zero.',{},self.evidence()))

    def test_repeated_false_no_funds_claim_gets_one_repair_then_native_fallback(self):
        denial='Il conto non ha fondi.'
        agent,api,reads=self.run_turn('quanti fondi ci sono?',[self.reply(denial),self.reply(denial)])
        self.assertEqual(api.call_count,2);self.assertEqual(reads.call_count,1)
        self.assertEqual(agent.turn_outcome,'incomplete');self.assertIn('21.37871164',agent.messages[-1]['content'])
        self.assertFalse(any(c['function']['name']=='hyperliquid_submit' for row in agent.messages for c in row.get('tool_calls',[])))

    def test_leverage_error_must_not_be_reported_as_insufficient_funds(self):
        evidence=self.evidence()+[{'tool':'hyperliquid_preview','arguments':{},'result':{'ok':False,'code':'leverage_action_required','message':'Order does not change leverage','sent':False}}]
        self.assertIn('leverage_action_required',m.response_issue('Non posso eseguire: non hai abbastanza fondi.',{},evidence))
        self.assertIsNone(m.response_issue('Ci sono 21.37871164 USDC. Anteprima rifiutata: leverage_action_required.',{},evidence))
        # Genuine insufficient margin is not suppressed just because some USDC exists.
        evidence[-1]['result']['code']='unified_margin_cap'
        self.assertIsNone(m.response_issue('Margine insufficiente per questa quantità.',{},evidence))

    def test_actual_invalid_leverage_preview_is_explained_from_native_error(self):
        # This regression isolates a malformed model proposal after readiness,
        # not missing-signer preflight or live indicator collection.
        self.enterContext(patch('hyperliquid_execution.preflight',return_value={'ready':True,'profile':'mainnet-master'}))
        self.enterContext(patch('hyperliquid_analysis.analyze',return_value={'profile':'mainnet-master','coin':'BTC','data_ready':True,'as_of_ms':int(time.time()*1000),'evidence_id':'synthetic'}))
        self.enterContext(patch('trading_service.monitor_preflight',return_value={'profile':'mainnet-master','network':'mainnet'}))
        args={'action':'order','coin':'BTC','side':'buy','size':'0.0127','order_kind':'limit',
              'price':'67332','leverage':40,'cross':False}
        call={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[
            {'id':'bad-preview','type':'function','function':{'name':'hyperliquid_preview','arguments':json.dumps(args)}}]}}]}
        with patch.object(h,'permissions'),patch.object(h,'market',return_value=({'name':'BTC','szDecimals':5},'')),patch.object(h,'submit') as submit:
            agent,api,reads=self.run_turn('Esegui trade BTC/USDC su Hyperliquid',[call,
                self.reply('Non posso eseguire: non hai abbastanza fondi.'),
                self.reply('Il saldo è 21.37871164 USDC. L’anteprima è stata rifiutata per leverage_action_required; nessun ordine inviato.')])
        self.assertEqual(api.call_count,3);submit.assert_not_called()
        self.assertTrue(any('"code": "leverage_action_required"' in str(row.get('content')) for row in agent.messages if row['role']=='tool'))
        self.assertNotIn('abbastanza fondi',agent.messages[-1]['content'])

    def test_operations_are_historical_even_when_receipt_says_filled(self):
        with h.database(self.home) as db:
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?)',('old-eth','unique','mainnet-master','filled',0,1,json.dumps({'action':'order','coin':'ETH'}),None));db.commit()
        result=h.operations(self.home)
        self.assertEqual(result['temporal_scope'],'historical_receipts')
        self.assertEqual(result['operations'][0]['state'],'filled')
        self.assertIn('already be closed',result['instruction'])

    def test_margin_error_explains_positive_balance_and_preserves_exact_recovery_scope(self):
        asset={'leverage':{'value':40},'maxTradeSzs':['1','1'],'availableToTrade':['21.378711','21.378711']}
        body={'action':'order','coin':'BTC','profile':'mainnet-master','size':'0.012'}
        with self.assertRaises(h.HLProblem) as caught:
            h.check_capacity(RISK,asset,body,__import__('decimal').Decimal('800'))
        error=caught.exception
        self.assertEqual(error.code,'unified_margin_cap')
        self.assertEqual(error.details['required_margin_usdc'],'21.6')
        self.assertEqual(error.details['available_margin_usdc'],'21.378711')
        self.assertEqual(error.details['collateral_usdc'],'21.37871164')
        with patch.object(h,'submit',side_effect=error):
            result=h.dispatch(SimpleNamespace(),'hyperliquid_submit',{'id':'prepared-btc'},self.home)
        self.assertEqual(result['recovery_tools'][0]['arguments'],{'resource':'snapshot','coin':'BTC','profile':'mainnet-master'})
        self.assertFalse(result['sent'])

    def test_failed_submit_requires_fresh_correct_snapshot_before_another_action(self):
        from loop_guard import LoopGuard
        guard=LoopGuard();error={'ok':False,'sent':False,'code':'unified_margin_cap','recovery_tools':[
            {'name':'hyperliquid_read','arguments':{'resource':'snapshot','profile':'mainnet-master','coin':'BTC'}}]}
        guard.observe('hyperliquid_submit',{'id':'bad-size'},json.dumps(error))
        for name in ('hyperliquid_submit','hyperliquid_preview'):self.assertIsNotNone(guard.before(name,{}))
        guard.observe('hyperliquid_read',{'resource':'snapshot','profile':'mainnet-master','coin':'ETH'},json.dumps(SNAP))
        self.assertIsNotNone(guard.before('hyperliquid_submit',{}))
        guard.observe('hyperliquid_read',{'resource':'snapshot','profile':'mainnet-master','coin':'BTC'},json.dumps(SNAP))
        self.assertIsNone(guard.before('hyperliquid_submit',{}))

    def test_closed_eth_cannot_be_reported_as_open(self):
        self.assertIsNotNone(m.response_issue('La posizione ETH è aperta.',{},self.evidence()))
        self.assertIsNone(m.response_issue('La posizione ETH precedente è chiusa; nessun ordine aperto.',{},self.evidence()))
        evidence=self.evidence();evidence[0]['result']['data']['risk']['positions']=[{'coin':'ETH','szi':'0.1'}]
        self.assertIsNone(m.response_issue('La posizione ETH è aperta.',{},evidence))

    def test_wrong_coin_rejected_before_preview_api_or_signer(self):
        agent=SimpleNamespace(current_market_request={'coin':'BTC'})
        with patch.object(h,'normalize_action') as normalize:
            result=h.dispatch(agent,'hyperliquid_preview',{'action':'order','coin':'ETH'},self.home)
        self.assertEqual(result['code'],'request_coin_mismatch');self.assertFalse(result['sent'])
        normalize.assert_not_called();self.assertEqual(result['recovery_tools'][0]['arguments']['coin'],'BTC')

    def test_wrong_coin_existing_preview_rejected_before_permissions_or_signature(self):
        agent=SimpleNamespace(current_market_request={'coin':'BTC'},mode='ask',depth=0)
        p=h.profile(self.home);body={'action':'order','coin':'ETH','scope':h.fingerprint(p)}
        with h.database(self.home) as db:
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?)',('old-eth','unique','mainnet-master','prepared',__import__('time').time(),0,json.dumps(body),None));db.commit()
        with patch.object(h,'permissions') as permissions:
            result=h.dispatch(agent,'hyperliquid_submit',{'id':'old-eth'},self.home)
        self.assertEqual(result['code'],'request_coin_mismatch');permissions.assert_not_called()

    def test_context_inspect_keeps_history_but_exposes_current_native_state(self):
        with patch.object(f,'HOME',self.home):agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic-only','auto')
        historical={'claims':[{'kind':'verified','text':'Ordine ETH filled ieri'}],'recent_evidence':[]}
        agent.memory=Mock();agent.memory.operational.inspect.return_value=copy.deepcopy(historical)
        agent.current_account_state=m.account_state(self.evidence())
        value=json.loads(agent.tool('context_inspect',{}))
        self.assertEqual(value['claims'],historical['claims']);self.assertEqual(value['current_native_account_state']['orders'],[])
        self.assertIn('historical',value['financial_state_notice'])


if __name__=='__main__':unittest.main()
