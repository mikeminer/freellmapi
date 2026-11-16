import tempfile,time,unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
import hyperliquid_tools as h
from trade_plan import TradePlan
from financial_allocation import size_plan,label,BASIS_OPTIONS
from work_ledger import load,save

class AllocationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.home=Path(self.tmp.name)
        self.profile=h.configure(self.home,'main','mainnet','0x'+'1'*40)
        self.agent=SimpleNamespace(session=self.home/'session.json',depth=0,
            financial_focus={'coin':'ETH','constraints':{'leverage':25,'capital_percent':'50'}},
            current_account_state={'profile':'main','network':'mainnet','available_usdc':'100','retrieved_ms':int(time.time()*1000)})
        data=load(self.agent.session);data.update(financial_request={'id':'qa-request'},financial_focus=self.agent.financial_focus);save(self.agent.session,data)
        self.plan=TradePlan.parse({'decision':'enter','reason':'test evidence','coin':'ETH','side':'buy','size':'0.4','entry':'2000','take_profit':'2100','stop_loss':'1900','leverage':25})
    def test_margin_basis_asked_once_size_is_native_and_cached_across_model_change(self):
        with patch('hyperliquid_menu.choose',return_value=BASIS_OPTIONS[0]) as choose,patch.object(h,'market',return_value=({'szDecimals':4},'')) as market,patch.object(h,'read',side_effect=AssertionError('Fresh state reused')):
            result,proof=size_plan(self.agent,self.home,self.profile,self.plan)
            self.agent.model='new-model';self.agent.financial_focus=load(self.agent.session)['financial_focus']
            again,same=size_plan(self.agent,self.home,self.profile,self.plan)
        choose.assert_called_once();market.assert_called_once()
        self.assertEqual(result.size,'0.6250');self.assertEqual(again,result);self.assertEqual(proof,same)
        self.assertEqual(proof['allocated_usdc'],'50');self.assertIn('50%',label(proof))
        self.assertEqual(self.agent.financial_focus['constraints']['budget_basis'],'margin')
        self.assertEqual(self.agent.financial_focus['choice_sources']['budget_basis']['kind'],'native_user_selection')
    def test_explicit_notional_basis_never_reasks_and_rounds_down(self):
        self.agent.financial_focus['constraints']['budget_basis']='notional'
        self.agent.current_account_state['available_usdc']='100.123'
        with patch('hyperliquid_menu.choose',side_effect=AssertionError('No repeated basis question')),patch.object(h,'market',return_value=({'szDecimals':4},'')):
            result,proof=size_plan(self.agent,self.home,self.profile,self.plan)
        self.assertEqual(result.size,'0.0250')
        self.assertLessEqual(float(result.size)*2000,float(proof['allocated_usdc']))
        self.assertLess(float(proof['allocated_usdc'])-float(result.size)*2000,0.0001*2000)
    def test_cancel_basis_does_not_read_funds_or_create_a_size(self):
        with patch('hyperliquid_menu.choose',return_value=BASIS_OPTIONS[2]),patch.object(h,'read',side_effect=AssertionError('No read after cancel')),patch.object(h,'market',side_effect=AssertionError('No market after cancel')):
            with self.assertRaises(h.HLProblem) as exc:size_plan(self.agent,self.home,self.profile,self.plan)
        self.assertEqual(exc.exception.code,'allocation_cancelled')
    def test_missing_verified_funds_never_invent_a_balance(self):
        self.agent.financial_focus['constraints']['budget_basis']='margin';self.agent.current_account_state=None
        with patch.object(h,'read',return_value={'ok':False}),patch.object(h,'market',side_effect=AssertionError('No guessed size')):
            with self.assertRaises(h.HLProblem) as exc:size_plan(self.agent,self.home,self.profile,self.plan)
        self.assertEqual(exc.exception.code,'allocation_snapshot_required')

if __name__=='__main__':unittest.main()
