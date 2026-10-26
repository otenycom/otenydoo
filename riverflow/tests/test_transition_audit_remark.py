from unittest import SkipTest

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_transition_audit_remark")
class TestTransitionAuditRemark(TransactionCase):
    """Every transition and every process that changes a state writes a remark in
    the audit log when oteny_audit is installed (radar plan
    riverflow/plans/transition-in-audit-log.md). The audit log records only
    changed values, so without the remark two transitions into the same state
    look the same (live service 33239: Upload Ship A1 or Received already), and a
    transition back to the same state leaves no row at all."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        if "oteny.audit.log" not in cls.env:
            raise SkipTest("oteny_audit is not installed")
        State = cls.env["riverflow.state"]
        Workflow = cls.env["riverflow.workflow"]
        Transition = cls.env["riverflow.transition"]
        service_model = cls.env["ir.model"]._get("riverflow.service")
        default_action = cls.env.ref("riverflow.transition_action_default")

        def transition(name, from_state, to_state, sequence=10):
            return Transition.create({
                "name": name,
                "from_state_id": from_state.id if from_state else False,
                "to_state_id": to_state.id,
                "action_id": default_action.id,
                "sequence": sequence,
            })

        # A child workflow like the Ship A1 one: two transitions into one state.
        cls.child_workflow = Workflow.create({"model_id": service_model.id, "name": "Remark Child WF"})
        cls.awaiting = State.create({"workflow_id": cls.child_workflow.id, "name": "Awaiting", "sequence": 10})
        cls.received = State.create({"workflow_id": cls.child_workflow.id, "name": "Received", "sequence": 20})
        cls.child_done = State.create({
            "workflow_id": cls.child_workflow.id, "name": "Done", "sequence": 30, "is_end_state": True,
        })
        cls.child_cancelled = State.create({
            "workflow_id": cls.child_workflow.id,
            "name": "Cancelled",
            "sequence": 40,
            "is_end_state": True,
            "is_cancelled_state": True,
        })
        cls.start = transition("Start", False, cls.awaiting)
        cls.upload = transition("Upload", cls.awaiting, cls.received, 10)
        cls.received_already = transition("Received already", cls.awaiting, cls.received, 20)
        cls.resend = transition("Resend", cls.awaiting, cls.awaiting, 30)
        cls.finish = transition("Finish", cls.received, cls.child_done)

        # A parent workflow with the three processes: auto-progress out of
        # Waiting, the Done cascade on Closed, the cancel cascade on Cancelled.
        cls.parent_workflow = Workflow.create({"model_id": service_model.id, "name": "Remark Parent WF"})
        cls.parent_open = State.create({"workflow_id": cls.parent_workflow.id, "name": "Open", "sequence": 10})
        cls.parent_waiting = State.create({
            "workflow_id": cls.parent_workflow.id,
            "name": "Waiting",
            "sequence": 20,
            "auto_progress_on_children_done": True,
        })
        cls.parent_next = State.create({"workflow_id": cls.parent_workflow.id, "name": "Next", "sequence": 30})
        cls.parent_closed = State.create({
            "workflow_id": cls.parent_workflow.id,
            "name": "Closed",
            "sequence": 40,
            "is_end_state": True,
            "auto_done_children_on_enter": True,
        })
        cls.parent_cancelled = State.create({
            "workflow_id": cls.parent_workflow.id,
            "name": "Cancelled",
            "sequence": 50,
            "is_end_state": True,
            "is_cancelled_state": True,
            "auto_done_children_on_enter": True,
            "hide_in_statusbar": True,
        })
        cls.close = transition("Close", cls.parent_open, cls.parent_closed)
        cls.cancel = transition("Cancel", cls.parent_open, cls.parent_cancelled, 20)
        cls.close_waiting = transition("Close while waiting", cls.parent_waiting, cls.parent_closed)

        cls.Service = cls.env["riverflow.service"].with_context(bot_no_inline_dispatch=True)
        cls.AuditLog = cls.env["oteny.audit.log"]

    def _service(self, name, workflow, state, parent=None):
        return self.Service.create({
            "name": name,
            "workflow_id": workflow.id,
            "state_id": state.id,
            "parent_id": parent.id if parent else False,
        })

    def _run(self, service, transition):
        """A person clicks the transition and saves its wizard."""
        action = service.with_context(transition_id=transition.id)._prepare_transition_action()
        return self.env[action["res_model"]].with_context(**action["context"]).create({}).action_save()

    def _remarks(self, service, key=None):
        domain = [
            ("model_name", "=", "riverflow.service"),
            ("record_id", "=", service.id),
            ("change_type", "=", "r"),
        ]
        if key:
            domain.append(("field_name", "=", key))
        return self.AuditLog.search(domain, order="id")

    def test_two_transitions_into_one_state_give_different_remarks(self):
        first = self._service("First", self.child_workflow, self.awaiting)
        second = self._service("Second", self.child_workflow, self.awaiting)

        self._run(first, self.upload)
        self._run(second, self.received_already)

        self.assertEqual(first.state_id, second.state_id)
        self.assertEqual(self._remarks(first, "transition").mapped("new_value_display_name"), ["Upload"])
        self.assertEqual(
            self._remarks(second, "transition").mapped("new_value_display_name"), ["Received already"]
        )
        remark = self._remarks(second)
        self.assertEqual(remark.field_display_name, "Transition")
        self.assertEqual(remark.create_uid, self.env.user)

    def test_transition_back_to_the_same_state_gives_a_remark(self):
        service = self._service("Resent", self.child_workflow, self.awaiting)

        self._run(service, self.resend)

        self.assertEqual(service.state_id, self.awaiting)
        self.assertEqual(self._remarks(service, "transition").mapped("new_value_display_name"), ["Resend"])

    def test_remark_has_the_transaction_of_the_state_change(self):
        service = self._service("Same transaction", self.child_workflow, self.awaiting)

        self._run(service, self.received_already)

        state_row = self.AuditLog.search([
            ("model_name", "=", "riverflow.service"),
            ("record_id", "=", service.id),
            ("field_name", "=", "state_id"),
            ("change_type", "=", "u"),
        ])
        self.assertEqual(len(state_row), 1)
        self.assertEqual(self._remarks(service).transaction_id, state_row.transaction_id)

    def test_start_transition_gives_a_remark_on_the_new_record(self):
        action = self.env["riverflow.service"].with_context(transition_id=self.start.id)._prepare_transition_action()
        Wizard = self.env[action["res_model"]].with_context(**action["context"])
        Wizard = Wizard.with_context(active_model=False, active_id=False, active_ids=[])
        result = Wizard.create({"name": "Started"}).action_save()

        service = self.env["riverflow.service"].browse(result["res_id"])
        self.assertEqual(service.state_id, self.awaiting)
        self.assertEqual(self._remarks(service, "transition").mapped("new_value_display_name"), ["Start"])

    def test_held_save_check_writes_no_remark(self):
        """A move to an end state by hand holds while child services are open and
        the state would complete by itself (save check, incomplete-children
        warning): nothing moves, nothing is written."""
        parent = self._service("Held", self.parent_workflow, self.parent_waiting)
        self._service("Held child", self.child_workflow, self.awaiting, parent)

        result = self._run(parent, self.close_waiting)

        self.assertEqual(result.get("tag"), "riverflow_save_check_hold")
        self.assertEqual(parent.state_id, self.parent_waiting)
        self.assertFalse(self._remarks(parent))

    def test_done_cascade_gives_a_remark_on_the_child_service(self):
        parent = self._service("Closing parent", self.parent_workflow, self.parent_open)
        child = self._service("Closed child", self.child_workflow, self.awaiting, parent)

        self._run(parent, self.close)

        self.assertEqual(child.state_id, self.child_done)
        self.assertEqual(
            self._remarks(child, "process").mapped("new_value_display_name"),
            [f"Done with parent service {parent.display_name}"],
        )
        self.assertEqual(self._remarks(child, "process").field_display_name, "Process")

    def test_cancel_cascade_gives_a_remark_on_the_child_service(self):
        parent = self._service("Cancelled parent", self.parent_workflow, self.parent_open)
        child = self._service("Cancelled child", self.child_workflow, self.awaiting, parent)

        self._run(parent, self.cancel)

        self.assertEqual(child.state_id, self.child_cancelled)
        self.assertEqual(
            self._remarks(child, "process").mapped("new_value_display_name"),
            [f"Cancelled with parent service {parent.display_name}"],
        )

    def test_auto_progress_gives_a_remark_on_the_parent_service(self):
        parent = self._service("Waiting parent", self.parent_workflow, self.parent_waiting)
        child = self._service("Last child", self.child_workflow, self.received, parent)

        self._run(child, self.finish)

        self.assertEqual(parent.state_id, self.parent_next)
        self.assertEqual(
            self._remarks(parent, "process").mapped("new_value_display_name"), ["All child services done"]
        )
