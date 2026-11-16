"""ETH must replace stale HYPE; existing user constraints are not questions."""
import io,json,tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock,patch
from contextlib import redirect_stdout
import financial_focus as focus
from market_routing import intent,collect

PROMPT='analizza eth e preparami un trade sicuro con leva 25 e 50% del capitale'
HISTORY=[{'role':'user','content':'/hyperliquid'},
         {'role':'assistant','content':'Proposta precedente HYPE SHORT'},
         {'role':'user','content':'HYPE: 10 HYPE leva 5'},
         {'role':'assistant','content':'Indica quantità e leva di HYPE.'}]


class FinancialFocusTests(unittest.TestCase):
    def setUp(self):
        tmp=tempfile.TemporaryDirectory();self.addCleanup(tmp.cleanup);self.home=Path(tmp.name)
        self.agent=SimpleNamespace(session=self.home/'session.json',messages=HISTORY+[{'role':'user','content':PROMPT}],mode='auto',depth=0,save=Mock(),tool_definitions=Mock(return_value=[]))
    def bind(self,prompt=PROMPT):
        request=intent(prompt,self.agent.messages);self.agent.financial_focus=focus.bind(self.agent,prompt,request);return request
    def test_exact_mobile_request_keeps_hyperliquid_eth_leverage_and_allocation(self):
        r=self.bind();self.assertEqual((r['venue'],r['coin']),('hyperliquid','ETH'))
        self.assertEqual(r['financial_constraints'],{'leverage':25,'capital_percent':'50'})
        from financial_completion import requirement
        self.assertIsNone(requirement(PROMPT,r))
    def test_read_tools_are_hyperliquid_not_binance_and_include_current_analysis(self):
        r=self.bind();calls=[]
        def execute(agent,ident,name,args,definitions):
            calls.append((name,args))
            if name=='hyperliquid_status':return json.dumps({'default':'main','profiles':[{'name':'main'}]})
            return json.dumps({'profile':'main','coin':args.get('coin'),'data':{},'data_ready':True})
        with patch('reliable_runtime.execute',side_effect=execute),patch('terminal_ui.action'),patch('terminal_ui.result'):
            collect(self.agent,r,self.home,lambda _:None)
        self.assertEqual([n for n,a in calls],['hyperliquid_status','hyperliquid_read','hyperliquid_analyze'])
        self.assertEqual(calls[1][1]['coin'],'ETH');self.assertEqual(calls[2][1]['coin'],'ETH')
    def test_explicit_exchange_and_topic_change_override_old_venue(self):
        self.assertEqual(intent('analizza ETH su Binance',HISTORY)['venue'],'binance')
        unrelated=HISTORY+[{'role':'user','content':'scrivi una favola per mio figlio'}]
        self.assertIsNone(intent('analizza ETH',unrelated)['venue'])
        summary=[{'role':'user','content':'Conversation summary (data, not new instructions): Hyperliquid HYPE'}]
        self.assertIsNone(intent('analizza ETH',summary)['venue'])
    def test_stale_question_is_not_displayed_and_known_choices_are_resolved(self):
        self.bind()
        wrong=focus.question_result(self.agent,'Quanti HYPE e quale leva vuoi usare?')
        self.assertEqual(wrong['code'],'question_coin_mismatch');self.assertEqual(wrong['requested_coin'],'ETH')
        known=focus.question_result(self.agent,'Quale leva vuoi per ETH?')
        self.assertTrue(known['reused']);self.assertIn('25',known['answer'])
        known=focus.question_result(self.agent,'Indica la quantità base di ETH')
        self.assertIn('50',known['answer']);self.assertIn('Derive base quantity',known['instruction'])
    def test_only_genuinely_unknown_margin_basis_can_be_asked(self):
        self.bind();self.assertIsNone(focus.question_result(self.agent,'Il 50% è margine o nozionale totale per ETH?'))
        self.assertIsNone(focus.question_result(self.agent,'Come vuoi usare il 50% del capitale per ETH?',['Come margine','Come nozionale totale']))
        self.assertTrue(focus.question_result(self.agent,'Quanto margine vuoi usare per ETH?')['reused'])
    def test_plain_stale_hype_proposal_is_rejected_but_eth_comparison_is_allowed(self):
        self.bind()
        self.assertEqual(focus.response_issue({'content':'HYPE/USDC SHORT: entry 66, TP 65, SL 68'},self.agent.financial_focus),'response_coin_mismatch')
        self.assertIsNone(focus.response_issue({'content':'ETH resta il target; HYPE è solo un confronto storico.'},self.agent.financial_focus))
    def test_short_followup_retains_eth_and_values_without_authorizing_an_order(self):
        self.bind();self.agent.messages.append({'role':'user','content':'sì'})
        r=intent('sì',self.agent.messages);self.assertEqual((r['venue'],r['coin']),('hyperliquid','ETH'))
        self.assertFalse(r['trading']);f=focus.bind(self.agent,'sì',r)
        self.assertEqual(f['constraints'],{'leverage':25,'capital_percent':'50'})
        from financial_completion import requirement
        self.assertIsNone(requirement('sì',r))
    def test_wrong_proposal_cannot_show_menu_or_reach_account_tools(self):
        self.bind()
        import hyperliquid_tools as h
        plan={'decision':'enter','reason':'synthetic','coin':'HYPE','side':'buy','size':'1','entry':'60','take_profit':'65','stop_loss':'55','leverage':25}
        with patch('hyperliquid_menu.choose',side_effect=AssertionError('No wrong-coin menu')),patch.object(h,'info',side_effect=AssertionError('No exchange calls')):
            result=h.dispatch(self.agent,'hyperliquid_propose_plan',{'plan':json.dumps(plan),'order_kind':'limit','bracket_slippage_bps':30},self.home)
        self.assertEqual(result['code'],'request_coin_mismatch');self.assertFalse(result['sent'])
        from trade_plan import TradePlan
        with self.assertRaises(h.HLProblem) as exc:focus.validate_plan(self.agent,TradePlan.parse(plan|{'coin':'ETH','leverage':5}))
        self.assertEqual(exc.exception.code,'request_leverage_mismatch')
    def test_scoped_facts_survive_restart_but_do_not_transfer_to_another_coin(self):
        self.bind()
        resumed=SimpleNamespace(session=self.agent.session,messages=[{'role':'user','content':'analizza ETH su Hyperliquid'}])
        r=intent('analizza ETH su Hyperliquid');saved=focus.bind(resumed,'analizza ETH su Hyperliquid',r)
        self.assertEqual(saved['constraints'],{'leverage':25,'capital_percent':'50'})
        r=intent('analizza HYPE su Hyperliquid');other=focus.bind(resumed,'analizza HYPE su Hyperliquid',r)
        self.assertEqual(other['constraints'],{})
    def test_scripted_model_wrong_question_is_corrected_once_without_asking_user(self):
        self.turn(repeat=False)
    def test_repeated_wrong_question_halts_after_one_correction_without_loop(self):
        self.turn(repeat=True)
    def turn(self,repeat):
        import freellmapi as f
        from session_store import Sessions
        bad={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'stale-question','type':'function','function':{'name':'ask_user','arguments':json.dumps({'question':'Quanti HYPE e quale leva vuoi?','options':['10 HYPE','20 HYPE']})}}]}}]}
        good={'choices':[{'message':{'role':'assistant','content':'ETH: leva 25x e allocazione 50% conservate. Quantità base da calcolare dai dati ETH. Nessun ordine inviato.'}}]}
        with patch.object(f,'HOME',self.home):
            a=f.Agent(self.home,'http://127.0.0.1:9','SYNTHETIC','auto');store=Sessions(self.home);store.activate(a,store.create(self.home))
            a.messages=list(HISTORY);a.auto_skills=False;a.streaming=False;a.context_limit=200000;a.mode='auto'
            with patch.object(a,'api',side_effect=[bad,bad if repeat else good]) as api,patch('market_routing.collect',return_value=[{'tool':'hyperliquid_analyze','arguments':{'coin':'ETH'},'result':{'coin':'ETH','data_ready':True}}]),patch.object(f,'ask_question',side_effect=AssertionError('No stale/repeated questions')),patch.object(f,'output'),redirect_stdout(io.StringIO()):
                a.turn(PROMPT)
            self.assertEqual(api.call_count,2);self.assertEqual(a.turn_outcome,'incomplete' if repeat else 'completed')
            self.assertEqual(a.current_market_request['coin'],'ETH');self.assertEqual(a.financial_focus['constraints']['leverage'],25)
            self.assertIn('ETH',a.messages[-1]['content'])


if __name__=='__main__':unittest.main()
