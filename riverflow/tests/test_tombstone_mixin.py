"""riverflow.tombstone.mixin: retire inside a compute, delete at the commit.

Driven on riverflow.check.result, the first model on the mixin."""
from unittest.mock import patch

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_tombstone_mixin")
class TestTombstoneMixin(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        workflow = cls.env["riverflow.workflow"].create({"model_id": service_model.id, "name": "Tombstone WF"})
        state = cls.env["riverflow.state"].create({"workflow_id": workflow.id, "name": "A", "sequence": 10})
        cls.service = cls.env["riverflow.service"].create(
            {"name": "Tombstone service", "workflow_id": workflow.id, "state_id": state.id}
        )
        # riverflow defines no check type of its own; an installed app adds them.
        values = cls.env["riverflow.check.result"]._fields["check_type"].get_values(cls.env)
        cls.check_type = values[0] if values else None

    def setUp(self):
        super().setUp()
        if not self.check_type:
            self.skipTest("no check type installed")
        self.CheckResult = self.env["riverflow.check.result"]

    def _result(self, name):
        return self.CheckResult.create(
            {"check_type": self.check_type, "severity": "info", "name": name, "service_id": self.service.id}
        )

    def _purge_steps(self):
        purge = type(self.env["riverflow.tombstone.mixin"])._tombstone_purge
        return [f for f in self.env.cr.precommit._funcs if getattr(f, "__func__", None) is purge]

    def test_retire_hides_at_once_and_the_commit_deletes(self):
        """One write retires the row: the parent's list hides it in the same
        transaction, no cache is cleared, one commit step is registered for
        any number of retires, and that step deletes the rows."""
        first, second = self._result("First"), self._result("Second")
        self.assertEqual(self.service.check_result_ids, first | second)

        Env = type(self.env)
        with patch.object(Env, "invalidate_all", side_effect=AssertionError("cache cleared")):
            first._retire()
            second._retire()
        self.assertTrue(first.to_be_deleted)
        self.assertFalse(first.active)
        self.assertFalse(self.service.check_result_ids, "the cached list hides retired rows at once")
        self.assertFalse(self.CheckResult.search_count([("service_id", "=", self.service.id)]))
        self.assertEqual(len(self._purge_steps()), 1, "one purge per transaction")

        self.env.cr.precommit.run()
        self.assertFalse((first | second).exists())

    def test_purge_runs_again_when_its_flush_retires_more(self):
        """The purge flushes; a compute that retires in that flush registers
        the next step, and the same commit runs it."""
        first, late = self._result("First"), self._result("Late")
        first._retire()

        Env = type(self.env)
        flush_all = Env.flush_all
        calls = []

        def flush_that_retires(env):
            # Stands in for a compute that the purge's delete triggers.
            if not calls:
                calls.append(1)
                late._retire()
            return flush_all(env)

        with patch.object(Env, "flush_all", flush_that_retires):
            self.env.cr.precommit.run()
        self.assertTrue(calls)
        self.assertFalse((first | late).exists())

    def test_a_user_archived_row_survives_the_purge(self):
        """The purge deletes flagged rows only. A row with active False and no
        flag is what a user archive leaves, and it stays; the autovacuum
        fallback leaves it too."""
        archived, retired = self._result("Archived"), self._result("Retired")
        archived.active = False
        retired._retire()

        self.env.cr.precommit.run()
        self.assertTrue(archived.exists())
        self.assertFalse(retired.exists())

        late = self._result("Missed by a commit step")
        late.write({"to_be_deleted": True, "active": False})
        self.CheckResult._gc_tombstones()
        self.assertFalse(late.exists())
        self.assertTrue(archived.exists())
