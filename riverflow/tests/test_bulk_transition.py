"""Run Transition on many records (radar plan credential-end-follow-up, D9a).

The chooser lists the transitions with "Run on Many Records" that leave the state of
at least one selected record, with counts. A choice opens the transition's own
screen for the records that can take it, names the others as warnings, and OK runs
it on every record or on none. The workflow is made here, so the tests hold for
every riverflow app.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_bulk_transition")
class TestBulkTransition(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        State = cls.env["riverflow.state"]
        Transition = cls.env["riverflow.transition"]
        default_action = cls.env.ref("riverflow.transition_action_default")
        service_model = cls.env["ir.model"]._get("riverflow.service").id
        cls.workflow = cls.env["riverflow.workflow"].create({"name": "Bulk", "model_id": service_model})
        cls.waiting = State.create({"name": "Waiting", "workflow_id": cls.workflow.id, "sequence": 10})
        cls.done = State.create({"name": "Done", "workflow_id": cls.workflow.id, "sequence": 20, "is_end_state": True})

        def step(name, from_state, to_state, bulk):
            return Transition.create(
                {
                    "name": name,
                    "from_state_id": from_state.id,
                    "to_state_id": to_state.id,
                    "action_id": default_action.id,
                    "workflow_id": cls.workflow.id,
                    "allow_bulk": bulk,
                }
            )

        cls.close = step("Close", cls.waiting, cls.done, True)
        cls.reopen = step("Reopen", cls.done, cls.waiting, True)
        cls.single = step("One Only", cls.waiting, cls.done, False)
        cls.Chooser = cls.env["riverflow.bulk.transition.wizard"]

    def _task(self, name, state):
        return self.env["riverflow.service"].create({"name": name, "workflow_id": self.workflow.id, "state_id": state.id})

    def test_chooser_lists_flagged_transitions_with_counts(self):
        tasks = self._task("A", self.waiting) | self._task("B", self.waiting) | self._task("C", self.done)
        action = self.Chooser._open_for(tasks)
        wizard = self.Chooser.browse(action["res_id"])
        lines = {line.transition_id: line.eligible_count for line in wizard.line_ids}
        self.assertEqual(lines, {self.close: 2, self.reopen: 1}, "One Only has no flag")
        self.assertIn("2 of 3", wizard.line_ids.filtered(lambda l: l.transition_id == self.close).label)

    def test_choice_opens_the_screen_with_the_left_out_records_as_warnings(self):
        a, b, c = self._task("A", self.waiting), self._task("B", self.waiting), self._task("C", self.done)
        wizard = self.Chooser.browse(self.Chooser._open_for(a | b | c)["res_id"])
        action = wizard.line_ids.filtered(lambda l: l.transition_id == self.close).action_choose()
        self.assertEqual(sorted(action["context"]["active_ids"]), sorted((a | b).ids))
        self.assertEqual(
            [s["subject"] for s in action["context"]["riverflow_bulk_skipped"]],
            [self.Chooser._subject_label(c)],
        )
        screen = self.env[action["res_model"]].with_context(**action["context"]).create({})
        findings = screen._save_check()
        self.assertEqual([f["level"] for f in findings if f.get("kind") == "bulk_skipped"], ["warning"])

    def test_ok_runs_on_every_record_and_returns_their_list(self):
        a, b = self._task("A", self.waiting), self._task("B", self.waiting)
        action = self.Chooser._open_for(a | b)
        self.assertEqual(action["context"]["transition_id"], self.close.id, "one choice opens its screen at once")
        screen = self.env[action["res_model"]].with_context(**action["context"]).create({})
        result = screen.with_context(**action["context"]).action_save()
        self.assertEqual((a | b).mapped("state_id"), self.done)
        self.assertEqual(result["res_model"], "riverflow.service")
        self.assertEqual(result["domain"], [("id", "in", (a | b).ids)])
        self.assertFalse(result["context"]["riverflow_bulk_skipped"], "no stale warnings on the next screen")

    def test_no_flagged_transition_refuses(self):
        self.close.allow_bulk = False
        with self.assertRaises(UserError):
            self.Chooser._open_for(self._task("A", self.waiting))
