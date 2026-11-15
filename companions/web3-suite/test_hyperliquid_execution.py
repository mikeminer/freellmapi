"""Native single-call execution, fake SDK/account only. No network or real orders."""
import io,json,tempfile,time,unittest
from contextlib import redirect_stdout
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch,Mock
import hyperliquid_tools as h
import hyperliquid_execution as x
from test_hyperliquid_leverage import LeverageFixture


class ExecutionTests(unittest.TestCase):
    def setUp(self):
        temp=tempfile.TemporaryDirectory();self.addCleanup(temp.cleanup)
        self.f=LeverageFixture(temp.name);self.addCleanup(self.f.close)
        self.f.leverage=1;self.agent=self.f.agent
        self.agent.financial_turn_started_at=time.time()-1
        self.agent.hyperliquid_analysis={('main','PUMP'):{'data_ready':True,'evidence_id':'a'*24,'as_of_ms':int(time.time()*1000),'scope':h.fingerprint(h.profile(self.f.home,'main'))}}
        self.plan={'decision':'enter','reason':'synthetic signal','coin':'PUMP','side':'buy','size':'1000','entry':'0.01','take_profit':'0.011','stop_loss':'0.009','leverage':1}
        self.args={'profile':'main','plan':json.dumps(self.plan),'order_kind':'limit','bracket_slippage_bps':30}
    def run_plan(self,**extra):return x.execute(self.agent,self.f.home,self.args|extra)
    def test_one_call_sends_three_legs_once_without_model_or_risk_changes(self):
        before=self.f.grant()
        with patch('freellmapi.Agent.api',side_effect=AssertionError('No model allowed')):
            result=self.run_plan()
        self.f.exchange.bulk_orders.assert_called_once()
        self.assertEqual(len(self.f.exchange.bulk_orders.call_args.args[0]),3)
        self.assertEqual(result['receipt']['body']['expected_leverage'],1)
        self.assertEqual(result['receipt']['response']['protection']['state'],'pending_entry')
        self.assertTrue(result['terminal']);self.assertEqual(len(result['phases']),5)
        for k in ('scope','expires','baseline_equity','max_order_usd','max_exposure_usd','max_loss_usd','max_turnover_usd','max_leverage'):
            self.assertEqual(self.f.grant()[k],before[k])
    def test_repeated_tool_id_model_change_and_compaction_reuse_receipt(self):
        first=self.run_plan();self.agent.messages=[];self.agent.model='another-model'
        with patch.object(h,'info',side_effect=AssertionError('No fresh RPC for completed handoff')):
            again=self.run_plan()
        self.assertTrue(again['reused']);self.assertEqual(first['operation_id'],again['operation_id'])
        self.f.exchange.bulk_orders.assert_called_once();self.assertEqual(len(self.f.rows()),1)
    def test_changed_plan_cannot_send_second_order_in_same_request(self):
        self.run_plan()
        with self.assertRaises(h.HLProblem) as error:self.run_plan(plan=json.dumps(self.plan|{'size':'1100'}))
        self.assertEqual(error.exception.code,'execution_plan_bound');self.f.exchange.bulk_orders.assert_called_once()
    def test_crash_after_preview_is_read_only_on_resume(self):
        with patch.object(h,'submit',side_effect=RuntimeError('simulated process interruption')):
            with self.assertRaises(RuntimeError):self.run_plan()
        result=self.run_plan()
        self.assertEqual(result['receipt']['state'],'prepared');self.assertTrue(result['reused'])
        self.f.exchange.bulk_orders.assert_not_called();self.f.secret.assert_not_called()
    def test_crash_after_send_never_replays_even_without_tool_result(self):
        original=h.submit
        def crash(*args):original(*args);raise RuntimeError('interrupted after SDK receipt')
        with patch.object(h,'submit',side_effect=crash):
            with self.assertRaises(RuntimeError):self.run_plan()
        result=self.run_plan()
        self.assertEqual(result['receipt']['state'],'accepted');self.assertTrue(result['reused'])
        self.f.exchange.bulk_orders.assert_called_once()
    def test_existing_uncertain_receipt_blocks_before_analysis_or_new_preview(self):
        first=self.run_plan();ident=first['operation_id']
        with h.database(self.f.home) as db:
            db.execute("UPDATE actions SET state='partial' WHERE id=?",(ident,));db.execute('DELETE FROM chat_executions');db.commit()
        with patch.object(h,'preview',side_effect=AssertionError('No new preview')),patch.object(h,'info',side_effect=AssertionError('No RPC needed')):
            result=self.run_plan()
        self.assertEqual(result['code'],'outcome_unknown');self.assertFalse(result['sent'])
        self.assertEqual(result['blockers'][0]['operations'][0]['id'],ident);self.f.exchange.bulk_orders.assert_called_once()
    def test_leverage_mismatch_does_not_change_leverage_or_send(self):
        self.f.leverage=5;result=self.run_plan()
        self.assertEqual(result['code'],'leverage_action_required');self.assertTrue(result['terminal'])
        self.f.exchange.update_leverage.assert_not_called();self.f.exchange.bulk_orders.assert_not_called();self.f.secret.assert_not_called()
    def test_native_financial_caps_remain_enforced(self):
        result=self.run_plan(plan=json.dumps(self.plan|{'size':'3000'}))
        self.assertEqual(result['code'],'order_cap');self.f.exchange.bulk_orders.assert_not_called();self.f.secret.assert_not_called()
    def test_leverage_changed_inside_sdk_initialization_blocks_before_exchange(self):
        with h.database(self.f.home) as db:
            grant=h.current_grant(db,'main');grant['max_leverage']=10
            db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(grant),'main'));db.commit()
        def initialization(*args,**kwargs):self.f.leverage=2;return self.f.exchange
        self.f.sdk.side_effect=initialization
        result=self.run_plan()
        self.assertEqual(result['code'],'leverage_changed');self.assertEqual(result['receipt']['state'],'blocked')
        self.f.exchange.bulk_orders.assert_not_called()
    def test_concurrent_calls_share_one_durable_claim(self):
        import threading
        from concurrent.futures import ThreadPoolExecutor
        entered=threading.Event();release=threading.Event();original=h.preview
        def waiting(*args,**kwargs):entered.set();self.assertTrue(release.wait(3));return original(*args,**kwargs)
        with patch.object(h,'preview',side_effect=waiting),ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(self.run_plan)
            try:
                self.assertTrue(entered.wait(3))
                with self.assertRaises(h.HLProblem) as error:self.run_plan()
                self.assertEqual(error.exception.code,'execution_interrupted')
            finally:release.set()
            self.assertEqual(first.result()['receipt']['state'],'accepted')
        self.f.exchange.bulk_orders.assert_called_once();self.assertEqual(len(self.f.rows()),1)
    def test_unavailable_analysis_is_terminal_hold_without_repeating(self):
        self.agent.hyperliquid_analysis={}
        with patch.object(h,'dispatch',return_value={'data_ready':False,'errors':{'book':'timeout'}}) as analysis:
            first=self.run_plan();again=self.run_plan()
        self.assertEqual(first['code'],'market_analysis_unavailable');self.assertTrue(again['reused'])
        analysis.assert_called_once();self.f.exchange.bulk_orders.assert_not_called()
    def test_reference_entry_not_replaced_with_a_chased_market_price(self):
        result=self.run_plan(order_kind='market',slippage_bps=30,plan=json.dumps(self.plan|{'entry':'0.0099'}))
        self.assertEqual(result['code'],'plan_price_moved');self.f.exchange.bulk_orders.assert_not_called()
    def test_false_protection_cannot_be_reported_as_execution_success(self):
        result=self.run_plan()
        from financial_completion import assess
        evidence=[{'tool':'hyperliquid_execute_plan','arguments':self.args,'result':result}]
        request={'coin':'PUMP','execution_action':'order'}
        self.assertTrue(assess(self.agent,request,evidence,self.f.home)['verified'])
        ident=result['operation_id']
        with h.database(self.f.home) as db:
            row=h.receipt(db.execute('SELECT * FROM actions WHERE id=?',(ident,)).fetchone())
            row['response']['protection']['state']='unverified'
            db.execute("UPDATE actions SET state='partial',response=? WHERE id=?",(json.dumps(row['response']),ident));db.commit()
        self.assertTrue(assess(self.agent,request,evidence,self.f.home)['halt'])
    def test_future_trigger_plan_cannot_be_sent_as_immediate_entry(self):
        with self.assertRaises(h.HLProblem):self.run_plan(plan=json.dumps(self.plan|{'decision':'watch','trigger':{'kind':'breakout','direction':'above','level':'0.01','confirmation':'touch','ttl_seconds':60}}))
        self.f.exchange.bulk_orders.assert_not_called()


class RequestContextTests(unittest.TestCase):
    def test_complete_entry_with_leverage_is_an_order_not_a_leverage_only_task(self):
        from financial_completion import requirement
        request={'venue':'hyperliquid'}
        self.assertEqual(requirement('Esegui questo ingresso immediato: PUMP long con leva già verificata 1x',request),'order')
        self.assertEqual(requirement('Esegui modifica leva PUMP a 1x',request),'leverage')
    def test_retry_compaction_and_resume_keep_id_and_original_goal(self):
        from work_ledger import load,render,record_request
        with tempfile.TemporaryDirectory() as root:
            agent=SimpleNamespace(session=Path(root)/'session.json',messages=[{'role':'user','content':'apri PUMP con TP e SL'}])
            record_request(agent.session,'vecchia domanda sulle notizie')
            first=x.bind_request(agent)
            agent.messages=[];self.assertEqual(x.bind_request(agent,retry=True),first)
            resumed=SimpleNamespace(session=agent.session,messages=[])
            self.assertEqual(x.bind_request(resumed,retry=True),first)
            self.assertIn('apri PUMP',render(agent.session));self.assertEqual(load(agent.session)['original_request'],'vecchia domanda sulle notizie')
            self.assertNotEqual(x.bind_request(resumed,retry=False),first)


class RealTurnTests(unittest.TestCase):
    """Run Agent.turn with a scripted model and intercepted real signer boundary."""
    setUp=ExecutionTests.setUp
    run_plan=ExecutionTests.run_plan
    def test_complete_chat_execution_needs_only_one_model_response(self):
        import freellmapi as f
        from session_store import Sessions
        with patch.object(f,'HOME',self.f.home):
            agent=f.Agent(self.f.home,'http://127.0.0.1:9','synthetic-only','auto')
            store=Sessions(self.f.home);store.activate(agent,store.create(self.f.home))
            agent.auto_skills=False;agent.streaming=False;agent.context_limit=200000;agent.mode='auto'
            with h.database(self.f.home) as db:
                grant=h.current_grant(db,'main');grant['session']=str(agent.session.resolve())
                db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(grant),'main'));db.commit()
            analysis={**self.agent.hyperliquid_analysis[('main','PUMP')],'profile':'main','coin':'PUMP','timeframes':{'15m':{},'1h':{}}}
            agent.hyperliquid_analysis={('main','PUMP'):analysis}
            reply={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'signal-one','type':'function','function':{'name':'hyperliquid_execute_plan','arguments':json.dumps(self.args)}}]}}]}
            with patch.object(agent,'api',return_value=reply) as model,patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn('trada PUMP/USDC su Hyperliquid con entry, TP e SL')
        self.assertEqual(agent.turn_outcome,'completed');model.assert_called_once();self.f.exchange.bulk_orders.assert_called_once()
        self.assertEqual(sum('Invio verificato' in str(m.get('content')) for m in agent.messages if m.get('role')=='assistant'),1)
    def test_known_local_blocker_finishes_before_any_model_or_market_query(self):
        import freellmapi as f
        from session_store import Sessions
        ident=self.run_plan()['operation_id']
        with h.database(self.f.home) as db:db.execute("UPDATE actions SET state='partial' WHERE id=?",(ident,));db.commit()
        with patch.object(f,'HOME',self.f.home):
            agent=f.Agent(self.f.home,'http://127.0.0.1:9','synthetic-only','auto')
            store=Sessions(self.f.home);store.activate(agent,store.create(self.f.home))
            agent.auto_skills=True;agent.streaming=False;agent.mode='auto'
            with patch.object(agent,'api',side_effect=AssertionError('No model needed')) as model,patch.object(agent.skill_router,'run',side_effect=AssertionError('No skill search')),patch.object(h,'info',side_effect=AssertionError('No market calls')),patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn('trada PUMP/USDC su Hyperliquid con entry, TP e SL')
        model.assert_not_called();self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertIn(ident,agent.messages[-1]['content']);self.f.exchange.bulk_orders.assert_called_once()


if __name__=='__main__':unittest.main()
