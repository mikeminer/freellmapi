"""Regression of Seeker's tiny-move rejection and lost affirmative context.

Synthetic account, intercepted signer/SDK, forbidden real network and keys.
"""
import io,json,tempfile,time,unittest,hashlib
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from contextlib import redirect_stdout
from unittest.mock import patch
import hyperliquid_tools as h,hyperliquid_execution as x
from financial_confirmation import resolve
import test_hyperliquid_execution as execution_tests


class EntryBudgetTests(unittest.TestCase):
    setUp=execution_tests.ExecutionTests.setUp
    run_plan=execution_tests.ExecutionTests.run_plan
    def test_exact_eth_short_from_mobile_keeps_remaining_slippage(self):
        args={'action':'order','coin':'ETH','side':'sell','size':'0.76','order_kind':'market',
              'entry_reference':'1817.35','slippage_bps':50,'take_profit':'1793.5','stop_loss':'1838.7',
              'bracket_slippage_bps':100}
        with patch.object(h,'permissions'),patch.object(h,'market',return_value=({'name':'ETH','szDecimals':4},'')),patch.object(h,'info',return_value={'ETH':'1816.45'}):
            b=h.normalize_action(h.profile(self.f.home,'main'),args)
        self.assertEqual(b['price'],'1808.3')
        self.assertGreaterEqual(Decimal(b['price']),Decimal('1817.35')*Decimal('.995'))
        self.assertEqual(b['entry_bound']['reference'],'1817.35')
        self.assertEqual(b['bracket']['take_profit'],'1793.5');self.f.exchange.bulk_orders.assert_not_called()
    def small_move(self,side,entry,tp,sl):
        plan=self.plan|{'side':side,'entry':entry,'take_profit':tp,'stop_loss':sl,'size':'1100'}
        result=self.run_plan(plan=json.dumps(plan),order_kind='market',slippage_bps=50)
        self.assertEqual(result['receipt']['state'],'accepted')
        b=result['receipt']['body'];bound=Decimal(entry)*(Decimal('1.005') if side=='buy' else Decimal('.995'))
        self.assertTrue(Decimal(b['price'])<=bound if side=='buy' else Decimal(b['price'])>=bound)
        self.agent.model='another-model'
        again=self.run_plan(plan=json.dumps(plan),order_kind='market',slippage_bps=50)
        self.assertTrue(again['reused']);self.f.exchange.bulk_orders.assert_called_once()
        legs=self.f.exchange.bulk_orders.call_args.args[0];self.assertEqual(len(legs),3)
    def test_small_adverse_buy_preserves_total_cap_and_sends_once(self):self.small_move('buy','0.009995','0.011','0.009')
    def test_small_adverse_sell_preserves_total_cap_and_sends_once(self):self.small_move('sell','0.010005','0.009','0.011')
    def test_beyond_total_cap_refuses_before_preview_or_signer_both_sides(self):
        self.agent.hyperliquid_controller=None
        for side,entry,tp,sl in [('buy','0.0098','0.011','0.009'),('sell','0.0102','0.009','0.011')]:
            self.agent.financial_request_id='outside-'+side
            result=self.run_plan(plan=json.dumps(self.plan|{'side':side,'entry':entry,'take_profit':tp,'stop_loss':sl}),order_kind='market',slippage_bps=50)
            self.assertEqual(result['code'],'plan_price_moved');self.assertFalse(result['sent'])
        self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()
    def test_signed_price_cannot_escape_original_cap_even_with_valid_bracket(self):
        from hyperliquid_protection import require_bracket
        b={'action':'order','side':'sell','price':'1807.4','mid':'1816.45',
           'bracket':{'take_profit':'1793.5','stop_loss':'1838.7'},
           'entry_bound':{'reference':'1817.35','max_slippage_bps':50,'limit':'1808.3'}}
        with self.assertRaises(h.HLProblem) as exc:require_bracket(b)
        self.assertEqual(exc.exception.code,'plan_price_moved')


class ConfirmationTests(unittest.TestCase):
    setUp=execution_tests.ExecutionTests.setUp
    def seed(self,agent=None):
        from work_ledger import load,save
        agent=agent or self.agent
        agent.hyperliquid_controller=None;agent.mode='auto';agent.current_market_request={'venue':'hyperliquid','execution_action':'order','coin':'PUMP'}
        text='trada PUMP/USDC su Hyperliquid con entry TP e SL'
        agent.messages=[{'role':'user','content':text}]
        ident=x.bind_request(agent)
        p=h.profile(self.f.home,'main');key=hashlib.sha256((str(agent.session.resolve())+':'+ident+':'+h.fingerprint(p)).encode()).hexdigest()
        proposal={'plan':x.parse_plan(json.dumps(self.plan)).to_dict(),'order_kind':'market','slippage_bps':50,'bracket_slippage_bps':30}
        row=h.preview(agent,self.f.home,{'profile':'main','action':'order','coin':'PUMP','side':'buy','size':'1000','order_kind':'market',
                      'slippage_bps':50,'take_profit':'0.011','stop_loss':'0.009','bracket_slippage_bps':30})['preview']
        result={'ok':False,'code':'plan_price_moved','sent':False,'operation_id':row['id'],'receipt':row}
        with h.database(self.f.home) as db:
            db.execute('CREATE TABLE IF NOT EXISTS chat_executions(id TEXT PRIMARY KEY,proposal TEXT,operation_id TEXT,state TEXT,result TEXT,created REAL)')
            db.execute('INSERT INTO chat_executions VALUES(?,?,?,?,?,?)',(key,json.dumps(proposal,sort_keys=True),row['id'],'blocked',json.dumps(result),time.time()));db.commit()
        ledger=load(agent.session);ledger['financial_request'].update(state='blocked',operation_id=row['id']);save(agent.session,ledger)
        agent.messages.extend([{'role':'assistant','content':'Invio incompleto: plan_price_moved. Nessun ordine inviato.'},{'role':'user','content':'sì'}])
        return agent,key,row,proposal
    def test_confirmation_reads_only_the_native_unsent_binding(self):
        agent,key,row,proposal=self.seed();ctx=resolve(agent,'sì',self.f.home)
        self.assertEqual(ctx['coin'],'PUMP');self.assertEqual(ctx['confirmed_proposal'],proposal)
        self.assertEqual(ctx['profile'],'main');self.f.secret.assert_not_called();self.f.exchange.bulk_orders.assert_not_called()
        resumed=SimpleNamespace(**vars(agent));resumed.model='other-model'
        self.assertEqual(resolve(resumed,'sì',self.f.home)['confirmation_of'],ctx['confirmation_of'])
    def test_no_confirmation_from_prose_topic_change_or_uncertain_receipt(self):
        agent,key,row,proposal=self.seed()
        agent.messages.insert(-1,{'role':'user','content':'scrivi una favola per mio figlio'})
        self.assertIsNone(resolve(agent,'sì',self.f.home));agent.messages.pop(-2)
        for state in ('sending','authorized','uncertain','accepted','filled','partial','rejected'):
            with h.database(self.f.home) as db:db.execute('UPDATE actions SET state=? WHERE id=?',(state,row['id']));db.commit()
            self.assertIsNone(resolve(agent,'sì',self.f.home),state)
        self.f.exchange.bulk_orders.assert_not_called()
    def test_sent_or_other_scope_cannot_become_new_confirmation(self):
        agent,key,row,proposal=self.seed()
        with h.database(self.f.home) as db:
            result=json.loads(db.execute('SELECT result FROM chat_executions WHERE id=?',(key,)).fetchone()[0]);result['sent']=True
            db.execute('UPDATE chat_executions SET result=? WHERE id=?',(json.dumps(result),key));db.commit()
        self.assertIsNone(resolve(agent,'sì',self.f.home))
        self.assertIsNone(resolve(agent,'no, solo analisi',self.f.home))
    def test_exact_confirmation_parameters_cannot_be_replaced(self):
        agent,key,row,proposal=self.seed();ctx=resolve(agent,'sì',self.f.home)
        x.bind_request(agent,confirmation=ctx)
        with self.assertRaises(h.HLProblem) as exc:
            x.execute(agent,self.f.home,{'profile':'main','plan':json.dumps(self.plan|{'size':'1100'}),'order_kind':'market','slippage_bps':50,'bracket_slippage_bps':30})
        self.assertEqual(exc.exception.code,'confirmation_plan_changed');self.f.exchange.bulk_orders.assert_not_called()
    def test_real_turn_yes_after_failed_unsent_plan_completes_once_without_questions(self):
        import freellmapi as f,hyperliquid_authority as authority
        from session_store import Sessions
        authority.enable(self.f.home,'main',loss_limit=None)
        with patch.object(f,'HOME',self.f.home):
            agent=f.Agent(self.f.home,'http://127.0.0.1:9','SYNTHETIC','auto')
            sessions=Sessions(self.f.home);sessions.activate(agent,sessions.create(self.f.home))
            agent,key,row,proposal=self.seed(agent);agent.messages.pop()
            agent.auto_skills=False;agent.streaming=False;agent.context_limit=200000;agent.mode='auto'
            analysis={**self.agent.hyperliquid_analysis[('main','PUMP')],'profile':'main','coin':'PUMP','timeframes':{'15m':{},'1h':{}}}
            agent.hyperliquid_analysis={('main','PUMP'):analysis}
            args={**proposal,'plan':json.dumps(proposal['plan']),'profile':'main'}
            reply={'choices':[{'message':{'role':'assistant','content':None,'tool_calls':[{'id':'confirmed-one','type':'function','function':{'name':'hyperliquid_execute_plan','arguments':json.dumps(args)}}]}}]}
            with patch.object(agent,'api',return_value=reply) as model,patch('hyperliquid_analysis.analyze',return_value=analysis),patch('questions.ask',side_effect=AssertionError('No repeated question')),patch.object(f,'output'),redirect_stdout(io.StringIO()):
                agent.turn('sì')
                self.assertEqual(agent.turn_outcome,'completed');model.assert_called_once();self.f.exchange.bulk_orders.assert_called_once()
                from work_ledger import load
                ledger=load(agent.session)['financial_request'];self.assertEqual(ledger['state'],'finished');self.assertIn('confirmation_of',ledger)
                agent.model='another-model';agent.turn('sì')
            self.f.exchange.bulk_orders.assert_called_once()
            self.assertEqual(agent.turn_outcome,'incomplete')


if __name__=='__main__':unittest.main()
