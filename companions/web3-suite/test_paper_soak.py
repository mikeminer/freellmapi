"""Retired entrypoint; active regression coverage is in test_paper_removed."""
import unittest
import paper_soak

class RetiredSoakTests(unittest.TestCase):
    def test_old_entrypoint_cannot_create_tasks_or_trades(self):
        for fn in (paper_soak.start,paper_soak.run,paper_soak.install):
            with self.assertRaisesRegex(ValueError,'paper_trading_removed'):fn('synthetic-home')
