"""Replay the b0629 margin/prerequisite loop with no real orders or RPC."""
import copy,io,json,tempfile,time,unittest
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from contextlib import redirect_stdout
import freellmapi as f
import hyperliquid_tools as h
from session_store import Sessions


class FinancialNativeRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        self.profile=h.configure(self.home,'mainnet-master','mainnet','0x'+'1'*40)
        self.snapshot={'resource':'snapshot','profile':'mainnet-master','network':'mainnet','queried_address':'0x'+'1'*40,
            'retrieved_ms':int(time.time()*1000),'source':'https://api.hyperliquid.xyz/info','data':{
            'asset':{'coin':'BTC','leverage':{'type':'isolated','value':40},'maxTradeSzs':['0.01334','0.01334'],'availableToTrade':['22.3787','22.3787']},
            'risk':{'account_mode':'unifiedAccount','collateral_usdc':'22.37870064','available_usd':'22.37870064','positions':[],'orders':[],'dexs_scanned':11},
            'market':{'markets':[{'meta':{'name':'BTC','szDecimals':5}}]}}}
        self.messages=[{'role':'assistant','content':None,'tool_calls':[{'id':'read-proof','type':'function','function':{'name':'hyperliquid_read','arguments':json.dumps({'resource':'snapshot','profile':'mainnet-master','coin':'BTC'})}}]},
                       {'role':'tool','tool_call_id':'read-proof','content':json.dumps(self.snapshot)}]
        self.args={'action':'order','profile':'mainnet-master','coin':'BTC','side':'buy','size':'0.0132','price':'67135','order_kind':'limit'}

    def body(self,p,args):
        return dict(args,network='mainnet',account=p['account'],target=p['target'],scope=h.fingerprint(p),reduce_only=False,
                    notional_usd=str(Decimal(args['size'])*Decimal(args['price'])),limit_notional_usd=str(Decimal(args['size'])*Decimal(args['price'])))

    def test_invalid_capacity_rejected_at_preview_before_creating_receipt(self):
        agent=SimpleNamespace(messages=self.messages,session=self.home/'s.json')
        with patch.object(h,'normalize_action',side_effect=self.body),patch.object(h,'info') as rpc:
            result=h.dispatch(agent,'hyperliquid_preview',self.args,self.home)
        self.assertEqual(result['code'],'unified_margin_cap')
        self.assertEqual(result['required_margin_usdc'],'23.926914')
        self.assertEqual(result['available_margin_usdc'],'22.3787')
        self.assertEqual(result['mathematical_max_size'],'0.01234')
        self.assertEqual(result['max_size_hint'],'0.01222')
        self.assertEqual(result['planning_headroom_bps'],100)
        self.assertFalse((self.home/'hyperliquid/operations.db').exists());rpc.assert_not_called()

    def turn(self,sizes,read_error=False):
        with patch.object(f,'HOME',self.home):
            agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic','auto')
            store=Sessions(self.home);store.activate(agent,store.create(self.home));agent.messages.extend(copy.deepcopy(self.messages))
            agent.auto_skills=False;agent.context_limit=200000;agent.streaming=False;agent.mode='auto'
            # This replay isolates sizing recovery; analyst behavior has its own tests.
            agent.hyperliquid_analysis={('mainnet-master','BTC'):{'scope':h.fingerprint(self.profile),'as_of_ms':int(time.time()*1000),'data_ready':True,'evidence_id':'synthetic-analysis'}}
            replies=[]
            for index,size in enumerate(sizes):
                if size is None:message={'role':'assistant','content':'Anteprima preparata; nessun invio eseguito.'}
                else:message={'role':'assistant','content':None,'tool_calls':[{'id':'proposal-'+str(index),'type':'function','function':{'name':'hyperliquid_preview','arguments':json.dumps(dict(self.args,size=size))}}]}
                replies.append({'choices':[{'message':message}]})
            with patch.object(agent,'api',side_effect=replies) as api,patch.object(h,'normalize_action',side_effect=self.body), \
                 patch('market_routing.collect',return_value=[]), \
                 patch.object(h,'read',return_value=copy.deepcopy(self.snapshot),side_effect=RuntimeError('synthetic read unavailable') if read_error else None) as read, \
                 patch.object(h,'submit') as submit,patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn('trada su hyperliquid con leva massima col miglior signal e TP sicuro')
            submit.assert_not_called();return agent,api,read

    def test_model_ignoring_recovery_reads_cannot_recreate_actual_loop(self):
        agent,api,read=self.turn(['0.0132','0.01334','0.0132'])
        self.assertEqual(api.call_count,3);self.assertEqual(read.call_count,2)
        self.assertEqual(agent.turn_outcome,'incomplete');self.assertIn('Fase finanziaria sospesa',agent.messages[-1]['content'])
        self.assertNotEqual(agent.turn_outcome,'loop_detected')
        self.assertEqual(agent.messages[0],self.messages[0])
        self.assertFalse((self.home/'hyperliquid/operations.db').exists())

    def test_valid_changed_plan_can_follow_native_read_without_automatic_submission(self):
        agent,api,read=self.turn(['0.0132','0.0123',None])
        self.assertEqual(read.call_count,1);self.assertEqual(api.call_count,3)
        rows=h.operations(self.home)['operations'];self.assertEqual(len(rows),1)
        self.assertEqual(rows[0]['body']['size'],'0.0123');self.assertEqual(rows[0]['state'],'prepared')

    def test_unavailable_native_read_is_bounded_and_preserves_context(self):
        agent,api,read=self.turn(['0.0132','0.0132'],read_error=True)
        self.assertEqual(read.call_count,2);self.assertEqual(api.call_count,2)
        self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertTrue(any('synthetic read unavailable' in str(m.get('content')) for m in agent.messages))

    def test_runtime_does_not_answer_financial_user_choices(self):
        from loop_guard import LoopGuard
        from financial_recovery import pending
        guard=LoopGuard();guard.financial_recovery=[{'name':'ask_user','arguments':{'question':'Margine o nozionale?'}}]
        agent=SimpleNamespace(loop_guard=guard)
        with patch('checkpoint_recovery.native_call') as call:self.assertIsNone(pending(agent,[]))
        call.assert_not_called()

if __name__=='__main__':unittest.main()
