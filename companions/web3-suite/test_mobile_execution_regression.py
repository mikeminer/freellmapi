"""Reproduce mobile session *ec58 with fake exchange/account, never real funds."""
import io,json,tempfile,time,unittest
from pathlib import Path
from contextlib import redirect_stdout
from unittest.mock import patch
import freellmapi as f
import hyperliquid_tools as h
from financial_completion import requirement
from market_routing import intent
from session_store import Sessions
import test_hyperliquid_execution as execution_tests


class ItalianExecutionIntentTests(unittest.TestCase):
    def test_compact_financial_prompt_retains_user_memory_choices_and_history(self):
        from market_routing import execution_payload
        system='GENERIC CODING MANUAL\nCurrent UTC time from runtime: now\nUser choices: no withdrawals\nProject instructions: keep scope\nRetrieved memory: project marker\nInstalled skills (load a relevant one when useful): []\nActive skill analyst: risk marker\nNative capability rules: GENERIC EDITOR MANUAL\nStructured operational context (persistent runtime record): request-123'
        history=[{'role':'user','content':'previous user choice'},{'role':'user','content':'ETH ed eseguilo'}]
        payload={'messages':[{'role':'system','content':system}]+history,'tools':[]}
        execution_payload(payload,{'execution_action':'order'})
        compact=payload['messages'][0]['content']
        for marker in ('no withdrawals','keep scope','project marker','risk marker','request-123'):self.assertIn(marker,compact)
        self.assertEqual(payload['messages'][1:3],history);self.assertNotIn('GENERIC CODING MANUAL',compact)
    def test_actual_user_prompt_and_inflected_commands_are_execution(self):
        for prompt in ('dammi un signal per eth con TP sicuro ed eseguilo','trova un segnale per ETH e invialo','piazzalo su ETH','compra ETH','eseguila su ETH'):
            with self.subTest(prompt=prompt):
                request=intent(prompt)
                self.assertEqual(request['coin'],'ETH');self.assertTrue(request['trading'])
                self.assertEqual(requirement(prompt,request),'order')
    def test_signal_only_and_negative_requests_do_not_authorize_orders(self):
        for prompt in ('dammi un signal per eth','analizza ETH, non inviare ordini','dammi un signal per eth, non eseguirlo','mandami un segnale ETH'):
            self.assertIsNone(requirement(prompt,intent(prompt)))
    def test_inflected_continuation_uses_recent_user_coin(self):
        request=intent('eseguilo',[{'role':'user','content':'segnale ETH/USDC'}])
        self.assertEqual(request['coin'],'ETH');self.assertEqual(requirement('eseguilo',request),'order')


class MobileExecutionRegressionTests(unittest.TestCase):
    setUp=execution_tests.ExecutionTests.setUp
    run_plan=execution_tests.ExecutionTests.run_plan

    def replay(self,prompt,calls,responses=None):
        with patch.object(f,'HOME',self.f.home):
            agent=f.Agent(self.f.home,'http://127.0.0.1:9','synthetic-only','auto')
            store=Sessions(self.f.home);store.activate(agent,store.create(self.f.home))
            agent.auto_skills=True;agent.streaming=False;agent.context_limit=200000;agent.mode='auto';agent.enabled_tools.update(h.NAMES)
            with h.database(self.f.home) as db:
                grant=h.current_grant(db,'main');grant['session']=str(agent.session.resolve())
                db.execute('UPDATE grants SET body=? WHERE profile=?',(json.dumps(grant),'main'));db.commit()
            agent.hyperliquid_analysis={('main','PUMP'):{**self.agent.hyperliquid_analysis[('main','PUMP')],
                'profile':'main','coin':'PUMP','timeframes':{'15m':{},'1h':{}}}}
            reply={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':calls}}]}
            with patch.object(agent,'api',side_effect=(responses or [reply])+[AssertionError('Must not request another inference')]) as model, \
                 patch.object(agent.skill_router,'run',side_effect=AssertionError('No skill search')),patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn(prompt)
        return agent,model

    def call(self,ident,**change):
        return {'id':ident,'type':'function','function':{'name':'hyperliquid_execute_plan','arguments':json.dumps(self.args|change)}}

    def test_actual_sentence_selects_native_preflight_and_completes_once(self):
        agent,model=self.replay('dammi un signal per PUMP con TP sicuro ed eseguilo',[self.call('entry')])
        model.assert_called_once();self.assertEqual(agent.turn_outcome,'completed');self.f.exchange.bulk_orders.assert_called_once()
        names=[t['function']['name'] for t in model.call_args.args[1]['tools']]
        self.assertIn('hyperliquid_execute_plan',names);self.assertNotIn('shell',names);self.assertNotIn('hyperliquid_preview',names)
        self.assertEqual(agent.current_market_request['execution_action'],'order')

    def test_actual_leverage_failure_finishes_without_polling_or_replacement(self):
        args=json.dumps(self.plan|{'leverage':20})
        agent,model=self.replay('dammi un signal per PUMP con TP sicuro ed eseguilo',[self.call('wrong-leverage',plan=args)])
        model.assert_called_once();self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertIn('leverage_action_required',agent.messages[-1]['content'])
        self.f.exchange.bulk_orders.assert_not_called();self.f.exchange.update_leverage.assert_not_called();self.f.secret.assert_not_called()
        self.assertEqual(self.f.rows(),[])

    def test_terminal_result_closes_batch_before_second_plan_or_reads(self):
        calls=[self.call('wrong',plan=json.dumps(self.plan|{'leverage':20})),self.call('replacement'),
               {'id':'poll','type':'function','function':{'name':'hyperliquid_read','arguments':json.dumps({'resource':'positions'})}}]
        agent,model=self.replay('dammi un signal per PUMP ed eseguilo',calls)
        model.assert_called_once();self.f.exchange.bulk_orders.assert_not_called()
        results={m['tool_call_id']:m['content'] for m in agent.messages if m.get('role')=='tool'}
        self.assertIn('batch_closed_after_execution_result',results['replacement']);self.assertIn('batch_closed_after_execution_result',results['poll'])
        self.assertTrue(all(c['id'] in results for c in calls))

    def test_analysis_only_cannot_execute_and_terminal_error_ends_turn(self):
        agent,model=self.replay('analizza PUMP su Hyperliquid, non inviare ordini',[self.call('unexpected-entry')])
        model.assert_called_once();self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertIn('execution_not_requested',agent.messages[-1]['content']);self.f.exchange.bulk_orders.assert_not_called()

    def test_invalid_plan_no_longer_locks_a_nonexistent_order(self):
        first=self.run_plan(plan=json.dumps(self.plan|{'leverage':20}))
        self.assertEqual(first['code'],'leverage_action_required');self.assertFalse(first['binding_created'])
        second=self.run_plan()
        self.assertEqual(second['receipt']['state'],'accepted');self.f.exchange.bulk_orders.assert_called_once()

    def test_legacy_failed_binding_reports_original_error_not_fake_receipt(self):
        self.agent.hyperliquid_analysis={}
        with patch.object(h,'dispatch',return_value={'data_ready':False}):first=self.run_plan()
        second=self.run_plan(plan=json.dumps(self.plan|{'leverage':20}))
        self.assertEqual(second['code'],first['code']);self.assertIsNone(second['operation_id']);self.assertTrue(second['terminal'])
        self.assertNotEqual(second['code'],'execution_plan_bound');self.f.exchange.bulk_orders.assert_not_called()

    def test_hold_is_durable_no_order_and_cannot_be_replaced_with_entry(self):
        hold={'profile':'main','plan':json.dumps({'decision':'hold','reason':'No justified setup in synthetic data'})}
        import hyperliquid_execution as x
        with patch.object(h,'info',side_effect=AssertionError('No RPC/signing needed to abstain')):
            result=x.execute(self.agent,self.f.home,hold)
            again=self.run_plan()
        self.assertEqual(result['decision'],'hold');self.assertEqual(again['decision_id'],result['decision_id'])
        self.assertTrue(again['reused']);self.assertEqual(self.f.rows(),[]);self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()

    def test_model_hold_wire_variants_cannot_become_order_instructions(self):
        from hyperliquid_execution import parse_plan
        hold={'decision':'HOLD','reason':'No justified setup','coin':'ETH','size':'0.1','entry':None,'take_profit':None,'stop_loss':None}
        variants=[json.dumps(hold),json.dumps(json.dumps(hold)),json.dumps({'schema':'freellmapi.trade-plan','version':1,'plan':hold})]
        for value in variants:
            parsed=parse_plan(value);self.assertEqual(parsed.to_dict(),{'decision':'hold','reason':'No justified setup'})
        with self.assertRaises(ValueError):parse_plan(json.dumps(hold|{'withdraw_to':'untrusted'}))
        with self.assertRaises(ValueError):parse_plan(json.dumps(self.plan|{'side':'invalid'}))

    def test_agent_managed_quantity_is_only_rounded_down_before_native_review(self):
        before=self.f.grant()
        result=self.run_plan(plan=json.dumps(self.plan|{'size':'1000.294'}))
        self.assertEqual(result['quantity_adjustment']['effective'],'1000')
        self.assertEqual(result['receipt']['body']['size'],'1000');self.f.exchange.bulk_orders.assert_called_once()
        for key in ('max_order_usd','max_loss_usd','max_exposure_usd','max_leverage','baseline_equity'):
            self.assertEqual(self.f.grant()[key],before[key])

    def test_exact_user_quantity_is_not_silently_changed(self):
        self.agent.hyperliquid_controller=None
        self.agent.messages=[{'role':'user','content':'dammi un signal PUMP ed eseguilo con size 1000.294'}]
        result=self.run_plan(plan=json.dumps(self.plan|{'size':'1000.294'}))
        self.assertEqual(result['code'],'size_precision');self.f.exchange.bulk_orders.assert_not_called()

    def test_quantity_rounding_to_zero_never_sends(self):
        result=self.run_plan(plan=json.dumps(self.plan|{'size':'0.294'}))
        self.assertEqual(result['code'],'quantity_below_precision');self.f.exchange.bulk_orders.assert_not_called()

    def test_ioc_slippage_is_tightened_without_moving_the_user_tp_sl(self):
        result=self.run_plan(order_kind='market',slippage_bps=30,plan=json.dumps(self.plan|{'take_profit':'0.01001'}))
        body=result['receipt']['body']
        self.assertEqual(body['bracket']['take_profit'],'0.01001');self.assertEqual(body['bracket']['stop_loss'],'0.009')
        self.assertLess(float(body['price']),.01001);self.assertLess(float(body['price']),.01003)
        self.assertIn('entry_slippage_tightened',body);self.f.exchange.bulk_orders.assert_called_once()

    def test_tp_already_behind_current_market_is_not_moved_to_force_trade(self):
        result=self.run_plan(order_kind='market',slippage_bps=30,plan=json.dumps(self.plan|{'entry':'0.00998','take_profit':'0.00999'}))
        self.assertEqual(result['code'],'bracket_direction');self.f.exchange.bulk_orders.assert_not_called()

    def test_prose_only_strategy_gets_one_recovery_into_explicit_hold(self):
        prose={'choices':[{'message':{'role':'assistant','content':'Nessun ingresso giustificato in questi dati sintetici.'}}]}
        hold=self.call('hold',plan=json.dumps({'decision':'hold','reason':'Synthetic setup lacks confirmation'}))
        structured={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[hold]}}]}
        agent,model=self.replay('dammi un signal per PUMP ed eseguilo',[],responses=[prose,structured])
        self.assertEqual(model.call_count,2);self.assertEqual(agent.turn_outcome,'completed');self.assertIn('HOLD',agent.messages[-1]['content'])
        self.assertIn('Nessun ordine inviato',agent.messages[-1]['content']);self.f.exchange.bulk_orders.assert_not_called()
        repair=model.call_args.args[1];self.assertEqual(repair['tool_choice'],'required')
        self.assertEqual({t['function']['name'] for t in repair['tools']},{'hyperliquid_execute_plan','ask_user'})

    def test_invalid_model_bracket_is_corrected_once_before_preparation(self):
        bad=self.call('bad-plan',plan=json.dumps(self.plan|{'stop_loss':'0.0105'}))
        good=self.call('corrected-plan')
        responses=[{'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[call]}}]} for call in (bad,good)]
        agent,model=self.replay('dammi un signal per PUMP ed eseguilo',[],responses=responses)
        self.assertEqual(model.call_count,2);self.assertEqual(agent.turn_outcome,'completed');self.f.exchange.bulk_orders.assert_called_once()
        self.assertEqual(len(self.f.rows()),1)

    def test_bad_bracket_after_single_correction_never_loops_or_sends(self):
        calls=[self.call('bad-'+str(i),plan=json.dumps(self.plan|{'stop_loss':'0.0105'})) for i in range(2)]
        responses=[{'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[call]}}]} for call in calls]
        agent,model=self.replay('dammi un signal per PUMP ed eseguilo',[],responses=responses)
        self.assertEqual(model.call_count,2);self.assertEqual(agent.turn_outcome,'incomplete');self.f.exchange.bulk_orders.assert_not_called();self.assertEqual(self.f.rows(),[])

    def test_repeated_prose_stops_after_the_single_format_recovery(self):
        prose={'choices':[{'message':{'role':'assistant','content':'Here is an analysis, not an actual submission.'}}]}
        agent,model=self.replay('dammi un signal per PUMP ed eseguilo',[],responses=[prose,prose])
        self.assertEqual(model.call_count,2);self.assertEqual(agent.turn_outcome,'incomplete')
        self.f.exchange.bulk_orders.assert_not_called();self.assertIn('incompleta',agent.messages[-1]['content'])

    def test_hold_does_not_claim_explicit_buy_was_executed(self):
        hold=self.call('hold',plan=json.dumps({'decision':'hold','reason':'Abstain without execution'}))
        agent,model=self.replay('compra PUMP su Hyperliquid',[hold])
        model.assert_called_once();self.assertEqual(agent.turn_outcome,'incomplete')
        self.assertIn('Nessun ordine inviato',agent.messages[-1]['content']);self.f.exchange.bulk_orders.assert_not_called()


if __name__=='__main__':unittest.main()
