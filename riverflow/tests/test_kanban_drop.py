"""A kanban drop is a step (radar pipeline applicants plan, decision 127).

A card dropped in another column of a board grouped by state opens the step
screen of the first step into that column, as the button would; it never writes
the state. The browser half (the drop calls the server instead of saving the
field) is the hoot test kanban_drop.test.js.
"""

from odoo.exceptions import UserError
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_kanban_drop")
class TestKanbanDrop(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        State = cls.env["riverflow.state"]
        Transition = cls.env["riverflow.transition"]
        default_action = cls.env.ref("riverflow.transition_action_default")

        cls.workflow = cls.env["riverflow.workflow"].create({
            "model_id": cls.env["ir.model"]._get("riverflow.service").id,
            "name": "Test Kanban Drop WF",
        })

        def state(name, sequence):
            return State.create({"workflow_id": cls.workflow.id, "name": name, "sequence": sequence})

        cls.state_applicant = state("Applicant", 10)
        cls.state_signed = state("Signed", 20)
        cls.state_queued = state("Queued", 30)
        cls.state_offboarding = state("Offboarding", 40)

        def transition(name, to_state, sequence, **extra):
            return Transition.create({
                "name": name,
                "from_state_id": cls.state_applicant.id,
                "to_state_id": to_state.id,
                "action_id": default_action.id,
                "sequence": sequence,
                **extra,
            })

        # Created before the first step, so the choice must follow the sequence,
        # not the record order.
        cls.trans_second_to_signed = transition("Sign Later", cls.state_signed, 20)
        cls.trans_first_to_signed = transition("Record Agreement", cls.state_signed, 10)
        cls.trans_stay = transition("Change Agency", cls.state_applicant, 30)
        # A bot's work step: the button strip hides it from a person, so a drop
        # must not reach it either.
        cls.trans_bot_claim = transition("Bot: claim", cls.state_queued, 40, bot_role="claim")

        cls.service = cls.env["riverflow.service"].with_context(bot_no_inline_dispatch=True).create({
            "name": "Kanban drop subject",
            "workflow_id": cls.workflow.id,
            "state_id": cls.state_applicant.id,
        })

    def test_a_drop_opens_the_first_step_into_the_column_and_keeps_the_state(self):
        action = self.service.action_kanban_drop(self.state_applicant.id, self.state_signed.id)

        self.assertEqual(action["type"], "ir.actions.act_window")
        self.assertEqual(action["target"], "new")
        self.assertEqual(action["context"]["transition_id"], self.trans_first_to_signed.id)
        # Only the step's OK changes the state; the drop itself does not.
        self.assertEqual(self.service.state_id, self.state_applicant)

    def test_a_drop_without_a_step_gives_a_notice_naming_the_steps_on_offer(self):
        action = self.service.action_kanban_drop(self.state_applicant.id, self.state_offboarding.id)

        self.assertEqual(action["tag"], "display_notification")
        self.assertEqual(action["params"]["type"], "warning")
        self.assertEqual(
            action["params"]["message"],
            "No step leads from Applicant to Offboarding. "
            "Steps from Applicant: Record Agreement, Sign Later, Change Agency.",
        )
        self.assertEqual(self.service.state_id, self.state_applicant)

        # The bot's step into Queued exists, but a person does not get it.
        action = self.service.action_kanban_drop(self.state_applicant.id, self.state_queued.id)
        self.assertEqual(action["tag"], "display_notification")
        self.assertNotIn("Bot: claim", action["params"]["message"])

    def test_a_card_whose_state_changed_since_the_board_loaded_is_refused(self):
        self.service.state_id = self.state_signed

        with self.assertRaises(UserError):
            self.service.action_kanban_drop(self.state_applicant.id, self.state_signed.id)
