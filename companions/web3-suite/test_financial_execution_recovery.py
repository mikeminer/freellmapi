"""Replay Seeker ID, timestamp-loop and false-completion failures without RPC/orders."""
import copy,io,json,tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from contextlib import redirect_stdout
import freellmapi as f,hyperliquid_tools as h
from financial_completion import assess,requirement
from financial_recovery import pending
from loop_guard import LoopGuard,LoopDetected
from session_store import Sessions

class FinancialExecutionRecoveryTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup);self.home=Path(temp.name)
        self.p=h.configure(self.home,'main','mainnet','0x'+'1'*40)
        self.ident='a'*24;self.cloid='0x'+'2'*32;self.tp='0x'+'3'*32;self.sl='0x'+'4'*32
        self.body={'action':'order','coin':'ETH','side':'buy','size':'1','price':'2000','scope':h.fingerprint(self.p),'target':self.p['target'],'cloid':self.cloid,'bracket':{'take_profit':'2100','stop_loss':'1900','tp_cloid':self.tp,'sl_cloid':self.sl}}
        self.row(self.ident,'blocked',self.body)
        self.request={'coin':'ETH','pair':'ETH/USDC','venue':'hyperliquid','trading':True,'execution_action':'order'}
        self.agent=SimpleNamespace(financial_turn_started_at=time.time()-1,current_account_state={'profile':'main'})
        self.snapshot={'resource':'snapshot','profile':'main','network':'mainnet','queried_address':self.p['target'],'retrieved_ms':int(time.time()*1000),
            'data':{'risk':{'account_mode':'unifiedAccount','collateral_usdc':'22.38','available_usd':'22.38','positions':[],'orders':[]},'asset':{'coin':'ETH'}}}
    def row(self,ident,state,body,stamp=None):
        stamp=time.time() if stamp is None else stamp
        with h.database(self.home) as db:
            db.execute('INSERT INTO actions VALUES(?,?,?,?,?,?,?,?)',(ident,'intent-'+ident,'main',state,stamp,stamp,json.dumps(body),json.dumps({'grouped_tpsl':True,'protection':{'state':'pending_entry'},'sdk_response':{'status':'ok','response':{'data':{'statuses':[{'resting':{'oid':11}},'waitingForFill','waitingForFill']}}}})));db.commit()
    def evidence(self,ident=None,**extra):
        ident=ident or self.ident
        return [{'tool':'hyperliquid_submit','arguments':{'id':ident},'result':{'receipt':{'id':ident},**extra}}]

    def test_recorded_client_ids_resolve_all_three_legs_without_network_or_submission(self):
        with patch.object(h,'info',side_effect=AssertionError('No RPC needed for blocked receipt')),patch.object(h,'submit') as send:
            for cloid,leg in ((self.cloid,'entry'),(self.tp,'tp'),(self.sl,'sl')):
                result=h.operations(self.home,cloid,'main')
                self.assertEqual(result['receipt']['id'],self.ident);self.assertEqual(result['receipt']['state'],'blocked')
                self.assertIn(leg,result['lookup']['legs']);self.assertFalse(result['resent'])
        send.assert_not_called()
    def test_submit_never_auto_translates_a_client_id_or_calls_signer(self):
        agent=SimpleNamespace(mode='auto',depth=0)
        with patch('subprocess.run',side_effect=AssertionError('Signer forbidden')):
            result=h.dispatch(agent,'hyperliquid_submit',{'id':self.cloid},self.home)
        self.assertEqual(result['code'],'preview_id_namespace')
        self.assertEqual(result['recovery_tools'][0]['arguments']['id'],self.cloid)
    def test_unknown_or_ambiguous_id_returns_valid_local_ids_and_read_only_recovery(self):
        result=h.dispatch(SimpleNamespace(),'hyperliquid_operations',{'id':'wrong'},self.home)
        self.assertEqual(result['code'],'operation_missing');self.assertEqual(result['valid_operations'][0]['id'],self.ident)
        self.row('b'*24,'prepared',copy.deepcopy(self.body))
        result=h.dispatch(SimpleNamespace(),'hyperliquid_operations',{'id':self.cloid},self.home)
        self.assertEqual(result['code'],'operation_id_ambiguous');self.assertEqual(len(result['valid_operation_ids']),2)
    def test_id_resolution_does_not_cross_explicit_profile_scope(self):
        result=h.dispatch(SimpleNamespace(),'hyperliquid_operations',{'id':self.cloid,'profile':'other'},self.home)
        self.assertEqual(result['code'],'operation_missing');self.assertEqual(result['valid_operations'],[])
    def test_orders_empty_with_new_retrieval_times_hit_same_read_circuit(self):
        guard=LoopGuard()
        for number in range(4):
            result={'resource':'orders','profile':'main','retrieved_ms':1000+number,'data':[]}
            guard.observe('hyperliquid_read',{'resource':'orders','profile':'main','limit':number+1},json.dumps(result))
        with self.assertRaises(LoopDetected):guard.observe('hyperliquid_read',{'resource':'orders','profile':'main'},json.dumps(dict(result,retrieved_ms=9999)))
    def test_real_status_price_size_and_market_time_changes_are_preserved(self):
        changes={'status':['open','partiallyFilled','filled','canceled','triggered','rejected','unknown'],
                 'sz':[str(n+1) for n in range(7)],'limitPx':[str(100+n) for n in range(7)],'timestamp':[100+n for n in range(7)]}
        for field,values in changes.items():
            with self.subTest(field=field):
                guard=LoopGuard()
                for n,value in enumerate(values):
                    order={'oid':1,'sz':'1','limitPx':'100','status':'open','timestamp':100};order[field]=value
                    result={'resource':'orders','retrieved_ms':n,'data':[order]}
                    guard.observe('hyperliquid_read',{'resource':'orders'},json.dumps(result))
    def test_native_id_listing_is_bounded_even_when_model_keeps_ignoring_ids(self):
        guard=LoopGuard();agent=SimpleNamespace(loop_guard=guard,messages=[],save=Mock());evidence=[]
        def native(agent,name,args):return json.dumps(h.operations(self.home,name=args.get('profile')))
        with patch('checkpoint_recovery.native_call',side_effect=native) as read:
            for n in range(2):
                error=h.dispatch(SimpleNamespace(),'hyperliquid_operations',{'id':'missing-'+str(n)},self.home)
                guard.observe('hyperliquid_operations',{'id':'missing-'+str(n)},json.dumps(error))
                self.assertIsNone(pending(agent,evidence))
            error=h.dispatch(SimpleNamespace(),'hyperliquid_operations',{'id':'missing-again'},self.home)
            guard.observe('hyperliquid_operations',{'id':'missing-again'},json.dumps(error))
            self.assertIn('sospesa',pending(agent,evidence))
        self.assertEqual(read.call_count,2);self.assertEqual(agent.turn_outcome,'incomplete')
    def test_read_only_request_is_not_confused_with_execution_due_to_word_leverage(self):
        from market_routing import intent
        prompt='Analizza ETH/USDC su Hyperliquid. Solo analisi: non inviare ordini, non modificare leva o posizioni.'
        request=intent(prompt);self.assertFalse(request['trading']);self.assertIsNone(requirement(prompt,request))
        self.assertEqual(requirement('esegui ordine',self.request),'order')
    def test_model_claim_or_read_only_results_cannot_prove_execution(self):
        with patch.object(h,'submit',side_effect=AssertionError('Gate must not submit')):
            result=assess(self.agent,self.request,[{'tool':'hyperliquid_read','arguments':{'resource':'snapshot'},'result':self.snapshot}],self.home)
        self.assertFalse(result['verified'])
    def test_blocked_uncertain_partial_and_prepared_receipts_are_incomplete(self):
        for state in ('blocked','authorized','uncertain','partial','prepared','rejected'):
            with h.database(self.home) as db:db.execute('UPDATE actions SET state=? WHERE id=?',(state,self.ident));db.commit()
            self.assertFalse(assess(self.agent,self.request,self.evidence(),self.home)['verified'])
    def test_current_native_accepted_submission_can_complete_but_history_and_wrong_coin_cannot(self):
        with h.database(self.home) as db:db.execute("UPDATE actions SET state='accepted' WHERE id=?",(self.ident,));db.commit()
        self.assertTrue(assess(self.agent,self.request,self.evidence(),self.home)['verified'])
        self.assertFalse(assess(self.agent,self.request,self.evidence(reused=True),self.home)['verified'])
        self.assertFalse(assess(self.agent,dict(self.request,coin='BTC'),self.evidence(),self.home)['verified'])
        with h.database(self.home) as db:db.execute('UPDATE actions SET updated=? WHERE id=?',(time.time()-3600,self.ident));db.commit()
        self.assertFalse(assess(self.agent,self.request,self.evidence(),self.home)['verified'])
    def test_forged_tool_receipt_without_journal_proof_is_rejected(self):
        fake=[{'tool':'hyperliquid_submit','arguments':{'id':'f'*24},'result':{'receipt':{'id':'f'*24,'state':'filled'}}}]
        self.assertFalse(assess(self.agent,self.request,fake,self.home)['verified'])

    def test_unsigned_preview_is_verified_as_preview_and_not_as_execution(self):
        with h.database(self.home) as db:db.execute("UPDATE actions SET state='prepared' WHERE id=?",(self.ident,));db.commit()
        evidence=[{'tool':'hyperliquid_preview','arguments':{'action':'order'},'result':{'preview':{'id':self.ident},'sent':False}}]
        self.assertTrue(assess(self.agent,dict(self.request,execution_action='preview'),evidence,self.home)['verified'])
        self.assertFalse(assess(self.agent,self.request,evidence,self.home)['verified'])

    def run_turn(self,prompt,replies,old_messages=(),on_mutation=None):
        with patch.object(f,'HOME',self.home):
            agent=f.Agent(self.home,'http://127.0.0.1:9','synthetic','auto');store=Sessions(self.home);store.activate(agent,store.create(self.home))
            agent.messages.extend(copy.deepcopy(old_messages));agent.auto_skills=False;agent.mode='auto';agent.context_limit=200000
            def tool(name,args):
                if name=='hyperliquid_status':return json.dumps({'default':'main','profiles':[{'name':'main'}]})
                if name=='hyperliquid_read':
                    if args['resource']=='orders':return json.dumps({'resource':'orders','profile':'main','retrieved_ms':time.time()*1000,'data':[]})
                    return json.dumps(self.snapshot)
                if on_mutation:return on_mutation(name,args)
                raise AssertionError('No mutation allowed in this replay: '+name)
            responses=[{'choices':[{'message':m}]} for m in replies]
            with patch.object(agent,'api',side_effect=responses) as api,patch.object(agent,'tool',side_effect=tool),patch.object(f,'output'),redirect_stdout(io.StringIO()):agent.turn(prompt)
            return agent,api
    def test_seeker_execute_followup_cannot_finish_as_analysis(self):
        old=[{'role':'user','content':'Analizza ETH/USDC su Hyperliquid. Solo analisi.'},{'role':'assistant','content':'Historical context marker; preserve me.'}]
        agent,api=self.run_turn('esegui ordine',[{'role':'assistant','content':'Here is an ETH analysis. No orders.'}],old)
        self.assertEqual(agent.turn_outcome,'incomplete');self.assertIn('incompleta',agent.messages[-1]['content'])
        self.assertEqual(agent.messages[:2],old);self.assertEqual(api.call_count,1)
    def test_eight_timestamp_only_reads_are_stopped_before_sixth_request(self):
        replies=[{'role':'assistant','content':None,'tool_calls':[{'id':'read-'+str(n),'type':'function','function':{'name':'hyperliquid_read','arguments':json.dumps({'resource':'orders','profile':'main'})}}]} for n in range(8)]
        agent,api=self.run_turn('esegui ordine ETH/USDC su Hyperliquid',replies)
        self.assertEqual(agent.turn_outcome,'loop_detected');self.assertEqual(api.call_count,5)

    def test_execution_with_unknown_coin_still_cannot_be_completed_with_prose(self):
        agent,api=self.run_turn('trada su Hyperliquid', [{'role':'assistant','content':'Here is a market analysis.'}])
        self.assertEqual(agent.turn_outcome,'incomplete');self.assertEqual(api.call_count,1)

    def test_current_mocked_native_submission_overrides_analysis_with_verified_receipt(self):
        ident='c'*24;self.row(ident,'prepared',self.body)
        sent=[]
        def synthetic_send(name,args):
            self.assertEqual(name,'hyperliquid_submit');sent.append(args['id'])
            with h.database(self.home) as db:
                db.execute("UPDATE actions SET state='accepted',updated=? WHERE id=?",(time.time(),ident));db.commit()
                row=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
            return json.dumps({'receipt':row})
        replies=[{'role':'assistant','content':None,'tool_calls':[{'id':'simulated-submit','type':'function','function':{'name':'hyperliquid_submit','arguments':json.dumps({'id':ident})}}]},
                 {'role':'assistant','content':'Here is another technical analysis.'}]
        with patch.object(h,'submit',side_effect=AssertionError('Never use real submit')),patch('hyperliquid_signer.execute',side_effect=AssertionError('Never sign')):
            agent,api=self.run_turn('esegui ordine ETH/USDC su Hyperliquid',replies,on_mutation=synthetic_send)
        self.assertEqual(sent,[ident]);self.assertEqual(agent.turn_outcome,'completed')
        self.assertEqual(api.call_count,1) # Do not ask the model to act again after native success.
        self.assertIn('Invio verificato',agent.messages[-1]['content']);self.assertIn(ident,agent.messages[-1]['content'])

    def test_old_accepted_receipt_read_in_new_turn_does_not_prove_a_new_submission(self):
        with h.database(self.home) as db:db.execute("UPDATE actions SET state='accepted',updated=? WHERE id=?",(time.time()-3600,self.ident));db.commit()
        replies=[{'role':'assistant','content':None,'tool_calls':[{'id':'old-receipt-read','type':'function','function':{'name':'hyperliquid_operations','arguments':json.dumps({'id':self.cloid})}}]},
                 {'role':'assistant','content':'Order has been filled.'}]
        def read_only(name,args):
            self.assertEqual(name,'hyperliquid_operations');return json.dumps(h.operations(self.home,args['id']))
        with patch.object(h,'submit',side_effect=AssertionError('No old order replay')):agent,_=self.run_turn('esegui ordine ETH/USDC su Hyperliquid',replies,on_mutation=read_only)
        self.assertEqual(agent.turn_outcome,'incomplete');self.assertIn('incompleta',agent.messages[-1]['content'])

    def test_naked_entry_success_halts_turn_without_second_model_call_or_entry(self):
        ident='d'*24;body={k:v for k,v in self.body.items() if k!='bracket'};self.row(ident,'prepared',body)
        sends=[]
        def synthetic_send(name,args):
            self.assertEqual(name,'hyperliquid_submit');sends.append(args['id'])
            with h.database(self.home) as db:
                db.execute("UPDATE actions SET state='accepted',updated=?,response=? WHERE id=?",(time.time(),json.dumps({'sent_once':True,'grouped_tpsl':False}),ident));db.commit()
                row=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
            return json.dumps({'receipt':row})
        replies=[{'role':'assistant','content':None,'tool_calls':[{'id':'fixture','type':'function','function':{'name':'hyperliquid_submit','arguments':json.dumps({'id':ident})}}]},
                 {'role':'assistant','content':'Now I will enter again with a TP.'}]
        agent,api=self.run_turn('esegui ordine ETH/USDC su Hyperliquid',replies,on_mutation=synthetic_send)
        self.assertEqual(sends,[ident]);self.assertEqual(api.call_count,1);self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertIn('protection_not_verified',agent.messages[-1]['content'])

if __name__=='__main__':unittest.main()
