from datetime import timedelta

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_execute_child_transitions")
class TestExecuteChildTransitions(TransactionCase):
    """A state that ends the case lets the child services that are not started
    execute their own closing transition (riverflow.state.execute_child_transitions
    and riverflow.transition.execute_on_parent_transitions; radar pipeline
    applicants plan, Q162).

    The parent service decides WHEN (only its "case over" state carries the
    value; an end state such as "Sent" does not), the child service's workflow
    decides HOW (its marked transition executes through its own transition
    wizard). A started child service stays open with everything below it; a
    closed one is descended through."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        State = cls.env["riverflow.state"]
        Workflow = cls.env["riverflow.workflow"]
        Transition = cls.env["riverflow.transition"]
        service_model = cls.env["ir.model"]._get("riverflow.service")
        default_action = cls.env.ref("riverflow.transition_action_default")
        cls.today = fields.Date.today()

        # Parent: Done ends the case, Sent only ends the parent's own part.
        cls.parent_workflow = Workflow.create({"model_id": service_model.id, "name": "Case WF"})
        cls.parent_open = State.create({"workflow_id": cls.parent_workflow.id, "name": "Open", "sequence": 10})
        cls.parent_done = State.create({
            "workflow_id": cls.parent_workflow.id,
            "name": "Done",
            "sequence": 20,
            "is_end_state": True,
            "execute_child_transitions": "cancel",
        })
        cls.parent_sent = State.create({
            "workflow_id": cls.parent_workflow.id,
            "name": "Sent",
            "sequence": 30,
            "is_end_state": True,
        })
        # The parent's transition clears the deadline: that key must not reach
        # the child service's transition wizard (the context is replaced).
        cls.to_done = Transition.create({
            "name": "Close the case",
            "from_state_id": cls.parent_open.id,
            "to_state_id": cls.parent_done.id,
            "action_id": default_action.id,
            "action_context": "{'clear_deadline': True}",
        })
        cls.to_sent = Transition.create({
            "name": "Send",
            "from_state_id": cls.parent_open.id,
            "to_state_id": cls.parent_sent.id,
            "action_id": default_action.id,
        })

        # Child service: only the Cancel transition from Not Started is marked.
        # Its own transition context sets a follow-up date, which shows that
        # the transition executed through its wizard and not as a direct write
        # of the state.
        cls.child_workflow = Workflow.create({"model_id": service_model.id, "name": "Child WF"})
        cls.child_not_started = State.create({"workflow_id": cls.child_workflow.id, "name": "Not Started", "sequence": 10})
        cls.child_in_progress = State.create({"workflow_id": cls.child_workflow.id, "name": "In Progress", "sequence": 20})
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
        Transition.create({
            "name": "Cancel",
            "from_state_id": cls.child_not_started.id,
            "to_state_id": cls.child_cancelled.id,
            "action_id": default_action.id,
            "action_context": "{'followup_in_days': 5}",
            "execute_on_parent_transitions": "cancel",
            "sequence": 30,
        })
        Transition.create({
            "name": "Start",
            "from_state_id": cls.child_not_started.id,
            "to_state_id": cls.child_in_progress.id,
            "action_id": default_action.id,
            "sequence": 10,
        })
        Transition.create({
            "name": "Cancel",
            "from_state_id": cls.child_in_progress.id,
            "to_state_id": cls.child_cancelled.id,
            "action_id": default_action.id,
        })

    def _execute_transition(self, service, transition):
        """A person clicks the transition and saves its wizard: as in the
        browser, the wizard runs with the transition's own context (here
        clear_deadline), and the rule runs inside that save."""
        action = service.with_context(transition_id=transition.id)._prepare_transition_action()
        self.env[action["res_model"]].with_context(**action["context"]).create({}).action_save()

    def _child(self, name, parent, state, **vals):
        return self.env["riverflow.service"].create(dict(
            {
                "name": name,
                "parent_id": parent.id,
                "workflow_id": self.child_workflow.id,
                "state_id": state.id,
                "use_project_deadline_from": "self",
                "project_deadline": self.today + timedelta(days=60),
            },
            **vals,
        ))

    def _case(self):
        """A parent service with every kind of child service the walk meets."""
        parent = self.env["riverflow.service"].create({
            "name": "Case",
            "workflow_id": self.parent_workflow.id,
            "state_id": self.parent_open.id,
            "use_project_deadline_from": "self",
            "project_deadline": self.today + timedelta(days=30),
        })
        children = {
            "not_started": self._child("Not started", parent, self.child_not_started),
            "started": self._child("Started", parent, self.child_in_progress),
            "closed": self._child("Closed", parent, self.child_done),
            "archived": self._child("Archived", parent, self.child_not_started, active=False),
        }
        children["below_not_started"] = self._child("Below not started", children["not_started"], self.child_not_started)
        children["below_started"] = self._child("Below started", children["started"], self.child_not_started)
        children["below_closed"] = self._child("Below closed", children["closed"], self.child_not_started)
        return parent, children

    def test_child_services_not_started_execute_their_cancel_transition(self):
        parent, children = self._case()

        self._execute_transition(parent, self.to_done)

        self.assertEqual(parent.state_id, self.parent_done)
        self.assertFalse(parent.project_deadline, "the parent's own transition cleared its deadline")
        # Not started, below it, and below a closed child service: cancelled
        # through their own transition, whose follow-up date shows on the
        # service; the parent's clear_deadline did not reach their wizard.
        for key in ("not_started", "below_not_started", "below_closed"):
            child = children[key]
            self.assertEqual(child.state_id, self.child_cancelled, key)
            self.assertEqual(child.project_deadline, self.today + timedelta(days=5), key)
        # A started child service stays open for a person, with what hangs below it.
        self.assertEqual(children["started"].state_id, self.child_in_progress)
        self.assertEqual(children["below_started"].state_id, self.child_not_started)
        # Closed and archived child services are left as they are.
        self.assertEqual(children["closed"].state_id, self.child_done)
        self.assertEqual(children["archived"].state_id, self.child_not_started)

    def test_an_end_state_without_the_value_leaves_the_child_services_open(self):
        """An e-mail that is Sent, an A1 application that is sent: the parent's
        part is done, the child services below are follow-up work."""
        parent, children = self._case()

        self._execute_transition(parent, self.to_sent)

        self.assertEqual(parent.state_id, self.parent_sent)
        self.assertEqual(children["not_started"].state_id, self.child_not_started)
        self.assertEqual(children["below_closed"].state_id, self.child_not_started)

    def test_code_that_writes_the_state_calls_the_same_rule(self):
        """A bulk upload or an automatic close writes the state without a
        transition and calls the rule itself; it has the same effect. A second
        call finds nothing left to do."""
        parent, children = self._case()
        parent.state_id = self.parent_done

        parent._execute_child_transitions()
        parent._execute_child_transitions()

        self.assertEqual(children["not_started"].state_id, self.child_cancelled)
        self.assertEqual(children["below_closed"].state_id, self.child_cancelled)
        self.assertEqual(children["started"].state_id, self.child_in_progress)

    def test_the_shipped_cancel_transitions_are_marked(self):
        """The generic workflows close a child service that is not started by
        their own transition; no riverflow state ends a case by itself."""
        for xml_id in (
            "riverflow.trans_task_not_started_to_cancelled",
            "riverflow.trans_taxi_not_started_to_cancelled",
            "riverflow.trans_train_not_started_to_cancelled",
            "riverflow.trans_se_5",
        ):
            self.assertEqual(self.env.ref(xml_id).execute_on_parent_transitions, "cancel", xml_id)

    # --- refuse_with_open_services (radar pipeline applicants plan, decision 145) ---

    def _save(self, service, transition):
        """Click the transition and save its wizard; returns the wizard and
        whether the save check held the save."""
        action = service.with_context(transition_id=transition.id)._prepare_transition_action()
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create({})
        result = wizard.with_context(**action["context"]).action_save()
        held = isinstance(result, dict) and result.get("tag") == "riverflow_save_check_hold"
        return wizard, held

    def test_a_transition_refuses_while_child_services_stay_open(self):
        """Close the case refuses and names what stays open: the started child
        service (with the open one below it, which the person working on it
        decides about). The services its own closing will cancel do not count:
        no false alarm for the work the rule of Q162 closes by itself."""
        self.to_done.refuse_with_open_services = True
        parent, children = self._case()

        wizard, held = self._save(parent, self.to_done)

        self.assertTrue(held)
        self.assertEqual(parent.state_id, self.parent_open, "the save is refused")
        self.assertEqual(wizard.save_check_state, "error")
        self.assertEqual(parent._open_services_left_by(self.to_done), children["started"])
        self.assertEqual(
            wizard.save_check_findings[0]["message"],
            f"Close the open services first: {children['started'].display_name}.",
        )

        children["started"].state_id = self.child_done
        wizard, held = self._save(parent, self.to_done)
        self.assertFalse(held, wizard.save_check_findings)
        self.assertEqual(parent.state_id, self.parent_done)
        self.assertEqual(children["not_started"].state_id, self.child_cancelled)
        self.assertEqual(children["below_started"].state_id, self.child_cancelled, "below a closed one: cancelled")

    def test_without_a_closing_value_every_open_child_service_counts(self):
        """Send carries no value, so it closes nothing: every open child service
        counts, also one below a closed child service; a transition without the
        setting never refuses."""
        parent, children = self._case()
        self.assertEqual(
            parent._open_services_left_by(self.to_sent),
            children["not_started"] | children["started"] | children["below_closed"],
        )
        _wizard, held = self._save(parent, self.to_sent)
        self.assertFalse(held, "the setting is off")

    def test_the_older_cascades_close_what_they_close(self):
        """cancel_children on the transition, or a cancelled target state with
        the Done cascade, cancel the whole tree: nothing counts. The Done cascade
        of a state that is not cancelled closes the direct child services only."""
        parent, children = self._case()
        self.to_sent.action_context = "{'cancel_children': True}"
        self.assertFalse(parent._open_services_left_by(self.to_sent))
        self.to_sent.action_context = False
        self.parent_sent.auto_done_children_on_enter = True
        self.assertEqual(
            parent._open_services_left_by(self.to_sent),
            children["below_not_started"] | children["below_started"] | children["below_closed"],
        )
