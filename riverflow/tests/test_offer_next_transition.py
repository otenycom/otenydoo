"""A transition can offer the next one on its screen (action context key
offer_next_transition_id_ref). Ticked, OK saves and then opens the screen of the
offered transition on the same record, as a click on its button would. Radar
example: Approve Unpaid Leave offers Send Approval (6-Nov-2026).

The workflow is made here, so the tests hold for every riverflow app.
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_offer_next_transition")
class TestOfferNextTransition(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        State = cls.env["riverflow.state"]
        Transition = cls.env["riverflow.transition"]
        default_action = cls.env.ref("riverflow.transition_action_default")
        service_model = cls.env["ir.model"]._get("riverflow.service").id
        cls.workflow = cls.env["riverflow.workflow"].create({"name": "Offer next", "model_id": service_model})
        cls.waiting = State.create({"name": "Waiting", "workflow_id": cls.workflow.id, "sequence": 10})
        cls.done = State.create(
            {"name": "Done", "workflow_id": cls.workflow.id, "sequence": 20, "is_end_state": True}
        )

        def step(name, from_state, to_state, context=False):
            return Transition.create(
                {
                    "name": name,
                    "from_state_id": from_state.id,
                    "to_state_id": to_state.id,
                    "action_id": default_action.id,
                    "action_context": context,
                    "workflow_id": cls.workflow.id,
                }
            )

        cls.send = step("Send Confirmation", cls.done, cls.done)
        cls.plain_close = step("Close", cls.waiting, cls.done)
        # The offered transition is named by id: the _id_ref form needs an XML id
        # on this transition, which a test record does not have.
        cls.close = step("Approve", cls.waiting, cls.done, "{'offer_next_transition_id': %d}" % cls.send.id)

    def _task(self):
        return self.env["riverflow.service"].create(
            {"name": "Task", "workflow_id": self.workflow.id, "state_id": self.waiting.id}
        )

    def _save(self, transition, task, screen=None):
        """Run a step as the button runs it: the step screen, then OK."""
        action = task.with_context(transition_id=transition.id).prepare_transition_action()
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create(screen or {})
        return wizard, wizard.with_context(**action["context"]).action_save()

    def test_the_screen_offers_the_next_transition_ticked(self):
        task = self._task()
        action = task.with_context(transition_id=self.close.id).prepare_transition_action()
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create({})
        self.assertEqual(wizard.next_transition_id, self.send)
        self.assertEqual(wizard.next_transition_name, "Send Confirmation")
        self.assertTrue(wizard.run_next_transition)

    def test_ticked_ok_opens_the_next_screen_on_the_same_record(self):
        task = self._task()
        _wizard, result = self._save(self.close, task)
        self.assertEqual(task.state_id, self.done)
        self.assertEqual(result["type"], "ir.actions.act_window")
        self.assertEqual(result["context"]["transition_id"], self.send.id)
        self.assertEqual(result["context"]["active_id"], task.id)

    def test_unticked_ok_only_closes(self):
        task = self._task()
        _wizard, result = self._save(self.close, task, {"run_next_transition": False})
        self.assertEqual(task.state_id, self.done)
        self.assertEqual(result, {"type": "ir.actions.act_window_close"})

    def test_a_transition_without_the_key_offers_nothing(self):
        task = self._task()
        wizard, result = self._save(self.plain_close, task)
        self.assertFalse(wizard.next_transition_id)
        self.assertEqual(result, {"type": "ir.actions.act_window_close"})
