from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_transition_from_archived_record")
class TestTransitionFromArchivedRecord(TransactionCase):
    """A transition may start from an archived record.

    A workflow may hide its record in a state (an end state that archives, or a
    user's Archive), and offer a transition back from there. The transition
    button passes the record in active_ids, without active_test=False, as the
    form does. The wizard must still find the record on OK: a Many2many leaves
    out inactive records in a normal context, so the wizard saw no record,
    built an empty new one and refused with "Another user just updated this
    record" (radar, Reactivate Directly on an archived ship, 15-Oct-2026).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        State = cls.env["riverflow.state"]
        service_model = cls.env["ir.model"]._get("riverflow.service")
        default_action = cls.env.ref("riverflow.transition_action_default")

        cls.workflow = cls.env["riverflow.workflow"].create(
            {"model_id": service_model.id, "name": "Archived Record WF"}
        )
        cls.state_open = State.create({"workflow_id": cls.workflow.id, "name": "Open", "sequence": 10})
        cls.state_closed = State.create(
            {"workflow_id": cls.workflow.id, "name": "Closed", "sequence": 20, "is_end_state": True}
        )
        cls.transition_reopen = cls.env["riverflow.transition"].create(
            {
                "name": "Reopen",
                "from_state_id": cls.state_closed.id,
                "to_state_id": cls.state_open.id,
                "action_id": default_action.id,
                "sequence": 10,
            }
        )

    def _run_from_button(self, record, transition):
        """The button path: the action the form button opens, its context as is,
        then OK on the wizard."""
        action = record.with_context(transition_id=transition.id)._prepare_transition_action()
        Wizard = self.env[action["res_model"]].with_context(**action["context"])
        wizard = Wizard.create(Wizard.default_get(list(Wizard.fields_get())))
        self.assertNotIn("active_test", action["context"], "The button passes no active_test=False.")
        self.assertEqual(wizard.records_to_transition_ids, record, "The wizard keeps the archived record.")
        wizard.action_save()

    def test_transition_runs_on_archived_record(self):
        service = self.env["riverflow.service"].create(
            {"name": "Archived Record", "workflow_id": self.workflow.id, "state_id": self.state_closed.id}
        )
        service.active = False
        self.assertFalse(service.active)

        self._run_from_button(service, self.transition_reopen)

        self.assertEqual(service.state_id, self.state_open)

    def test_transition_runs_on_active_record(self):
        """Control: the same transition on an active record, unchanged."""
        service = self.env["riverflow.service"].create(
            {"name": "Active Record", "workflow_id": self.workflow.id, "state_id": self.state_closed.id}
        )

        self._run_from_button(service, self.transition_reopen)

        self.assertEqual(service.state_id, self.state_open)
