"""The worker count fits the PostgreSQL lock table."""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

from .. import locks


@tagged("odoo_parallel_tests", "post_install", "-at_install", "test_lock_budget")
class TestLockBudget(TransactionCase):
    def test_workers_are_capped_to_the_lock_table(self):
        # Stock PostgreSQL: 64 x 100 = 6,400 slots hold 6 workers.
        self.assertEqual(locks.cap_workers_to_lock_table(18, 6400), (6, True))
        # Tuned server: 1024 x 100 slots hold every worker asked for.
        self.assertEqual(locks.cap_workers_to_lock_table(18, 102400), (18, False))
        # A table smaller than one worker's budget still runs one worker.
        self.assertEqual(locks.cap_workers_to_lock_table(18, 640), (1, True))
        # Unknown size (psql failed): leave the count alone.
        self.assertEqual(locks.cap_workers_to_lock_table(18, None), (18, False))

    def test_lock_table_slots_reads_the_server(self):
        self.env.cr.execute(
            "SELECT current_setting('max_locks_per_transaction')::int"
            " * (current_setting('max_connections')::int"
            " + current_setting('max_prepared_transactions')::int)"
        )
        expected = self.env.cr.fetchone()[0]
        self.assertEqual(locks.lock_table_slots(self.env.cr.dbname), expected)
