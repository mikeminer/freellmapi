import tempfile
import unittest
from unittest.mock import patch
from autonomy import Budget,BudgetExceeded
from freellmapi import Agent
from model_health import RateLimited


class ContinuousTests(unittest.TestCase):
    def test_zero_limits_keep_accounting_and_loop_protection(self):
        budget=Budget(seconds=0,tokens=0,steps=0)
        budget.tokens=10**9; budget.steps=10**6; budget.started-=10**8
        budget.check(); budget.request()
        self.assertEqual(budget.steps,10**6+1)
        self.assertEqual(budget.remaining_seconds(),float('inf'))
        for _ in range(3): budget.action('write_file',{'path':'x'})
        with self.assertRaises(BudgetExceeded): budget.action('write_file',{'path':'x'})

    def test_explicit_finite_limits_still_apply(self):
        for key in ('seconds','tokens','steps'):
            budget=Budget(**{key:1})
            if key=='seconds': budget.started-=2
            elif key=='tokens': budget.tokens=1
            else: budget.steps=1
            with self.assertRaises(BudgetExceeded): budget.check()

    def test_quota_fallback_preserves_payload(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent=Agent(tmp,'http://localhost','test','chosen')
            payload={'model':'chosen','messages':[{'role':'tool','tool_call_id':'done','content':'written'}],'tools':[]}
            with patch.object(agent,'api',side_effect=[RateLimited('quota'),{'ok':True}]) as api:
                self.assertEqual(agent.request_turn(payload),{'ok':True})
            retry=api.call_args_list[1].args[1]
            self.assertEqual(retry['model'],'auto'); self.assertIs(retry['messages'],payload['messages'])
            self.assertEqual(agent.model,'chosen')

    def test_exhausted_auto_does_not_loop(self):
        with tempfile.TemporaryDirectory() as tmp:
            agent=Agent(tmp,'http://localhost','test','auto')
            with patch.object(agent,'api',side_effect=RateLimited('quota')) as api:
                with self.assertRaises(RateLimited): agent.request_turn({'model':'auto'})
            api.assert_called_once()
            self.assertEqual(agent.budget_settings['tokens'],0)


if __name__=='__main__': unittest.main()
