"""Typed proposal menus on the shared CMD/Remote path; no real account or I/O."""
import io,json,time,unittest,threading
from contextlib import redirect_stdout
from types import SimpleNamespace
from unittest.mock import patch
import hyperliquid_tools as h,hyperliquid_execution as x,hyperliquid_authority as authority
import test_hyperliquid_execution as execution_tests
from trade_proposal import propose,OPTIONS


class ProposalTests(unittest.TestCase):
    setUp=execution_tests.ExecutionTests.setUp
    def prepare(self):
        self.agent.hyperliquid_controller=None;self.agent.mode='auto';self.agent.current_market_request={'venue':'hyperliquid','coin':'PUMP','trading':False}
        self.agent.messages=[{'role':'user','content':'Proponi un trade PUMP su Hyperliquid'}]
        authority.enable(self.f.home,'main',loss_limit=None)
        return self.args|{'order_kind':'market','slippage_bps':50}
    def test_exact_plan_menu_confirms_once_and_survives_model_change(self):
        args=self.prepare()
        with patch('hyperliquid_menu.choose',return_value=OPTIONS[0]) as choose,redirect_stdout(io.StringIO()):
            result=propose(self.agent,self.f.home,args)
            self.agent.model='different-model'
            again=propose(SimpleNamespace(**vars(self.agent)),self.f.home,args)
        choose.assert_called_once();self.assertEqual(choose.call_args.args[2],OPTIONS)
        label=choose.call_args.args[1]
        for s in ('PUMP','LONG','1000','0.01','0.011','0.009','1x'):self.assertIn(s,label)
        self.assertEqual(result['receipt']['state'],'accepted');self.assertTrue(again['reused'])
        self.f.exchange.bulk_orders.assert_called_once();self.assertEqual(len(self.f.exchange.bulk_orders.call_args.args[0]),3)
    def test_cancel_is_durable_and_never_reads_key_or_changes_leverage(self):
        args=self.prepare()
        with patch('hyperliquid_menu.choose',return_value=OPTIONS[2]) as choose:
            self.assertEqual(propose(self.agent,self.f.home,args)['choice'],'cancelled')
            self.assertTrue(propose(self.agent,self.f.home,args)['reused'])
        choose.assert_called_once();self.f.secret.assert_not_called();self.f.exchange.update_leverage.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()
    def test_refresh_blocks_old_execution_until_a_new_actual_choice(self):
        args=self.prepare()
        with patch('hyperliquid_menu.choose',return_value=OPTIONS[1]),patch('hyperliquid_analysis.analyze',return_value={'profile':'main','coin':'PUMP','data_ready':True,'evidence_id':'fresh','as_of_ms':int(time.time()*1000),'scope':h.fingerprint(h.profile(self.f.home,'main'))}) as analysis:
            result=propose(self.agent,self.f.home,args)
        analysis.assert_called_once();self.assertEqual(result['analysis']['evidence_id'],'fresh')
        self.assertEqual(result['choice'],'refresh');self.assertFalse(result['terminal'])
        with self.assertRaises(h.HLProblem) as exc:x.execute(self.agent,self.f.home,args)
        self.assertEqual(exc.exception.code,'proposal_confirmation_required')
        self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()
    def test_refresh_reoffers_same_levels_with_fresh_data_and_a_new_actual_choice(self):
        args=self.prepare()
        with patch('hyperliquid_menu.choose',side_effect=[OPTIONS[1],OPTIONS[2]]) as choose,patch('hyperliquid_analysis.analyze',return_value={'profile':'main','coin':'PUMP','data_ready':True,'evidence_id':'fresh','as_of_ms':int(time.time()*1000),'scope':h.fingerprint(h.profile(self.f.home,'main'))}):
            first=propose(self.agent,self.f.home,args)
            self.assertEqual(self.agent.hyperliquid_analysis[('main','PUMP')]['evidence_id'],'fresh')
            self.assertIsNone(self.agent.current_market_request['execution_action'])
            second=propose(self.agent,self.f.home,args)
        self.assertEqual(choose.call_count,2);self.assertNotEqual(first['proposal_id'],second['proposal_id'])
        self.assertEqual(second['choice'],'cancelled');self.f.exchange.bulk_orders.assert_not_called()
    def test_open_answer_is_not_execution_permission(self):
        args=self.prepare()
        with patch('hyperliquid_menu.choose',return_value='Vorrei una leva diversa'):result=propose(self.agent,self.f.home,args)
        self.assertEqual(result['choice'],'custom');self.assertFalse(result['sent']);self.f.secret.assert_not_called()
    def test_read_only_request_does_not_offer_send_or_bind_an_order(self):
        args=self.prepare();self.agent.messages=[{'role':'user','content':'PUMP solo analisi, non inviare ordini'}]
        with patch('hyperliquid_menu.choose',side_effect=AssertionError('No execution menu for analysis only')):
            self.assertEqual(propose(self.agent,self.f.home,args)['choice'],'read_only')
        self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()
    def test_proposal_request_does_not_itself_authorize_execution(self):
        from financial_completion import requirement
        self.assertIsNone(requirement('trovami il migliore trade su hyperliquid. usa metà del capitale',{'venue':'hyperliquid'}))
    def test_invalid_typed_proposal_returns_structured_terminal_error(self):
        self.prepare()
        result=h.dispatch(self.agent,'hyperliquid_propose_plan',self.args|{'plan':'{"decision":"enter","size":"bad"}'},self.f.home)
        self.assertEqual(result['code'],'invalid_trade_plan');self.assertTrue(result['terminal']);self.assertFalse(result['sent'])
        self.assertIn('enter_fields',result['plan_contract'])
    def test_proposal_and_execution_both_describe_json_not_a_prose_summary(self):
        import freellmapi as f
        for name in ('hyperliquid_propose_plan','hyperliquid_execute_plan'):
            schema=next(t['function']['parameters'] for t in f.TOOLS if t['function']['name']==name)
            self.assertIn('NEVER a prose summary',schema['properties']['plan']['description'])
            self.assertEqual(schema['required'],['plan'])
    def test_hold_proposal_never_asks_or_reaches_a_signer(self):
        self.prepare()
        with patch('hyperliquid_menu.choose',side_effect=AssertionError('HOLD has no execution menu')):
            result=propose(self.agent,self.f.home,{'plan':json.dumps({'decision':'hold','reason':'No justified setup'})})
        self.assertEqual(result['choice'],'hold');self.assertFalse(result['sent']);self.f.secret.assert_not_called()
    def test_real_model_prose_plan_is_repaired_once_across_model_change(self):
        self.prose_repair(False)
    def test_repeated_prose_plan_stops_after_one_repair_without_a_menu_or_order(self):
        self.prose_repair(True)
    def test_unoffered_old_preview_tool_is_repaired_before_dispatch(self):
        self.prose_repair(False,unoffered=True)
    def test_flattened_arguments_are_repaired_once_before_dispatch_and_menu_discovery_is_bounded(self):
        self.prose_repair(False,flattened=True)
    def prose_repair(self,repeat,unoffered=False,flattened=False):
        import freellmapi as f
        from session_store import Sessions
        args=self.prepare()
        with patch.object(f,'HOME',self.f.home):
            agent=f.Agent(self.f.home,'http://127.0.0.1:9','SYNTHETIC','model-a')
            sessions=Sessions(self.f.home);sessions.activate(agent,sessions.create(self.f.home))
            agent.auto_skills=False;agent.streaming=False;agent.context_limit=200000;agent.mode='auto'
            captured=[]
            def respond(route,payload):
                captured.append(json.loads(json.dumps(payload)))
                invalid=len(captured)==1 or repeat
                agent.model='model-b'
                arguments={**args,'plan':'PUMP LONG, size 1000, entry 0.01, TP 0.011, SL 0.009, leva 1x'} if invalid else args
                if flattened and len(captured)==1:arguments={**json.loads(args['plan']),'leverage':'1','order_kind':'market','slippage_bps':50,'bracket_slippage_bps':50}
                name='hyperliquid_preview' if unoffered and len(captured)==1 else 'hyperliquid_propose_plan'
                return {'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'proposal-'+str(len(captured)),'type':'function','function':{'name':name,'arguments':json.dumps(arguments)}}]}}]}
            with patch.object(agent,'api',side_effect=respond) as api,patch('market_routing.collect',return_value=[]),patch('hyperliquid_menu.choose',return_value=OPTIONS[2]) as choose,patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn('Proponi un trade PUMP su Hyperliquid con leva 1'+(', mostra anteprima con pulsanti' if flattened else ''))
            self.assertEqual(api.call_count,2)
            self.assertEqual(captured[1]['model'],'model-b')
            if not unoffered:self.assertEqual(captured[1]['tool_choice']['function']['name'],'hyperliquid_propose_plan')
            names={t['function']['name'] for t in captured[1]['tools']}
            self.assertNotIn('hyperliquid_execute_plan',names);self.assertNotIn('hyperliquid_submit',names)
            self.assertIn('financial_tool_not_offered' if unoffered else 'plan_contract',str(captured[1]['messages']))
            self.assertEqual(agent.turn_outcome,'incomplete' if repeat else 'completed')
            self.assertEqual(choose.call_count,0 if repeat else 1)
            if flattened:self.assertEqual({t['function']['name'] for t in captured[0]['tools']},{'hyperliquid_propose_plan','ask_user'})
        self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called();self.f.exchange.update_leverage.assert_not_called()
    def test_crash_after_click_does_not_replay_the_click_or_execute_again(self):
        args=self.prepare()
        with patch('hyperliquid_menu.choose',return_value=OPTIONS[0]) as choose,patch.object(x,'execute',side_effect=RuntimeError('synthetic interruption')) as execute:
            with self.assertRaises(RuntimeError):propose(self.agent,self.f.home,args)
            result=propose(SimpleNamespace(**vars(self.agent)),self.f.home,args)
        self.assertEqual(result['code'],'proposal_confirmation_interrupted');choose.assert_called_once();execute.assert_called_once()
        self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()
    def test_real_turn_converts_prose_confirmation_to_menu_and_cancels_siblings(self):
        import freellmapi as f
        from session_store import Sessions
        args=self.prepare()
        with patch.object(f,'HOME',self.f.home):
            agent=f.Agent(self.f.home,'http://127.0.0.1:9','SYNTHETIC','auto')
            sessions=Sessions(self.f.home);sessions.activate(agent,sessions.create(self.f.home))
            agent.auto_skills=False;agent.streaming=False;agent.context_limit=200000;agent.mode='auto'
            analysis={**self.agent.hyperliquid_analysis[('main','PUMP')],'profile':'main','coin':'PUMP','timeframes':{'15m':{},'1h':{}}}
            agent.hyperliquid_analysis={('main','PUMP'):analysis}
            calls=[{'id':'proposal-'+str(i),'type':'function','function':{'name':'hyperliquid_execute_plan','arguments':json.dumps(args)}} for i in range(2)]
            reply={'choices':[{'message':{'role':'assistant','content':'PUMP LONG: Entry 0.01, TP 0.011, SL 0.009. Eseguo ora?','tool_calls':calls}}]}
            with patch.object(agent,'api',return_value=reply) as model,patch('hyperliquid_analysis.analyze',return_value=analysis),patch('hyperliquid_menu.choose',return_value=OPTIONS[2]) as choose,patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn('trada PUMP/USDC su Hyperliquid con entry TP e SL')
            self.assertEqual(agent.turn_outcome,'completed');model.assert_called_once();choose.assert_called_once()
            self.assertIn('annullata',agent.messages[-1]['content'])
        self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()
    def test_prose_only_draft_is_preserved_and_converted_once_to_typed_menu(self):
        import freellmapi as f
        from session_store import Sessions
        args=self.prepare()
        with patch.object(f,'HOME',self.f.home):
            agent=f.Agent(self.f.home,'http://127.0.0.1:9','SYNTHETIC','auto')
            sessions=Sessions(self.f.home);sessions.activate(agent,sessions.create(self.f.home))
            agent.auto_skills=False;agent.streaming=False;agent.context_limit=200000;agent.mode='auto'
            draft='PUMP LONG: Entry 0.01, TP 0.011, SL 0.009. Vuoi eseguire?'
            first={'choices':[{'message':{'role':'assistant','content':draft}}]}
            second={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'typed-proposal','type':'function','function':{'name':'hyperliquid_propose_plan','arguments':json.dumps(args)}}]}}]}
            with patch.object(agent,'api',side_effect=[first,second]) as api,patch('market_routing.collect',return_value=[]),patch('hyperliquid_menu.choose',return_value=OPTIONS[2]) as choose,patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn('Proponi un trade PUMP su Hyperliquid')
            self.assertEqual(api.call_count,2);choose.assert_called_once();self.assertEqual(agent.turn_outcome,'completed')
            self.assertIn(draft,[m.get('content') for m in agent.messages])
            self.assertEqual(api.call_args_list[1].args[1]['tool_choice']['function']['name'],'hyperliquid_propose_plan')
        self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()
    def test_native_cmd_arrows_select_refresh(self):
        import questions
        from unittest.mock import MagicMock
        with patch('remote_access.active',return_value=None),patch.object(questions.ui,'fancy',return_value=True),patch.object(questions.sys.stdin,'isatty',return_value=True),patch('msvcrt.getwch',side_effect=['\xe0','P','\r']),patch('rich.live.Live',return_value=MagicMock()),patch.object(questions.ui,'write'):
            result=questions.ask('Come vuoi procedere con il piano PUMP?',OPTIONS)
        self.assertEqual(result['answer'],'Aggiorna analisi');self.assertFalse(result['cancelled'])
    def test_same_choices_reach_remote_question_and_accept_open_answer(self):
        import remote_access,questions
        remote=remote_access.Remote(self.f.home,lambda:{})
        self.addCleanup(remote.trading.close);remote.owner='synthetic-phone'
        result=[];errors=[]
        def run():
            try:
                with patch('remote_access.active',return_value=remote):result.append(questions.ask('Come vuoi procedere con il piano PUMP?',OPTIONS))
            except Exception as exc:errors.append(exc)
        worker=threading.Thread(target=run);worker.start()
        try:
            deadline=time.monotonic()+3
            while not remote.question and time.monotonic()<deadline:time.sleep(.01)
            self.assertIsNotNone(remote.question);self.assertEqual(remote.question['options'],OPTIONS)
            remote.answer_question('synthetic-phone',{'id':remote.question['id'],'answer':'Vorrei modificare il prezzo'})
            worker.join(3);self.assertFalse(worker.is_alive());self.assertFalse(errors)
            self.assertEqual(result[0]['answer'],'Vorrei modificare il prezzo')
        finally:
            if worker.is_alive():
                with remote.cv:remote.closed=True;remote.cv.notify_all()
                worker.join(3)


if __name__=='__main__':unittest.main()
