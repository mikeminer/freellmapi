"""Native menu decisions become one durable request, never an implicit send."""
import io,json,tempfile,unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch,Mock
import freellmapi as f,hyperliquid_tools as h,hyperliquid_menu as menu
from session_store import Sessions

class HyperliquidMenuTests(unittest.TestCase):
    def setUp(self):
        t=tempfile.TemporaryDirectory();self.addCleanup(t.cleanup);self.home=Path(t.name)
        with patch.object(f,'HOME',self.home):self.a=f.Agent(self.home,'http://127.0.0.1:9','synthetic','auto')
        s=Sessions(self.home);s.activate(self.a,s.create(self.home));self.a.mode='auto'
        h.configure(self.home,'main','mainnet','0x'+'1'*40)
    def run_menu(self,answers):
        def ask(q,opts):
            from questions import validate_question
            validate_question(q,opts);value=next(answers)
            return {'question':q,'answer':value,'cancelled':value is None}
        with patch.object(f,'HOME',self.home),patch.object(menu.questions,'ask',side_effect=ask) as ask,patch.object(self.a,'turn') as turn,patch.object(menu,'read') as read,redirect_stdout(io.StringIO()):
            menu.run(self.a,self.home,lambda _:None)
        return ask,turn,read
    def test_execution_menu_records_capital_and_generates_one_explicit_request(self):
        ask,turn,read=self.run_menu(iter(['Analizza ed esegui con TP/SL automatici','90%']))
        self.assertEqual(ask.call_count,2);turn.assert_called_once();read.assert_not_called()
        prompt=turn.call_args.args[0]
        self.assertIn('90% del collaterale USDC disponibile come margine',prompt);self.assertIn('TP e SL',prompt)
        from financial_completion import requirement
        self.assertEqual(requirement(prompt,{'venue':'hyperliquid'}),'order')
        self.assertTrue(any('90%' in a['answer'] for a in self.a.question_answers.values()))
    def test_analysis_needs_one_click_and_explicitly_forbids_execution(self):
        ask,turn,read=self.run_menu(iter(['Miglior segnale long/short']))
        ask.assert_called_once();self.assertIn('Solo analisi: non inviare ordini',turn.call_args.args[0])
    def test_cancel_does_not_infer_an_answer_or_start_a_model(self):
        ask,turn,read=self.run_menu(iter([None]));turn.assert_not_called();read.assert_not_called()
    def test_open_budget_is_parsed_without_inventing_units(self):
        self.assertEqual(menu.capital('35,5%'),'35.5% del collaterale USDC disponibile come margine')
        self.assertEqual(menu.capital('20 USDC'),'20 USDC di margine')
        for invalid in ('1000%','0%','NaN USDC','tutto','20','-2 USDC'):
            with self.assertRaises(ValueError):menu.capital(invalid)
    def test_invalid_budget_stops_before_any_ai_or_financial_action(self):
        ask,turn,read=self.run_menu(iter(['Analizza ed esegui con TP/SL automatici','bad']))
        turn.assert_not_called();read.assert_not_called();self.assertEqual(self.a.command_outcome,'incomplete')
    def test_plain_keyword_and_slash_command_open_the_same_menu(self):
        with patch('hyperliquid_menu.run') as run:
            for text in ('hyperliquid','Hyperliquid','/hyperliquid','/hyperliquid menu'):self.assertTrue(h.command(self.a,text,self.home,lambda _:None))
        self.assertEqual(run.call_count,4)

if __name__=='__main__':unittest.main()
