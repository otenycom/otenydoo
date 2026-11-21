"""A form can show the workflow of a linked record in its header.

The transition buttons and the status bar act on the record of the form. A form
that shows the workflow of a linked record (radar: a credential shows the status
bar and buttons of its service, Ries 21-Nov-2026) gets the buttons JSON of the
linked record with its model and id, so the widget runs each transition on that
record and loads the form again afterwards.
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow")
class TestLinkedTransitionButtons(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service").id
        cls.workflow = cls.env["riverflow.workflow"].create({"name": "Collect Something", "model_id": service_model})
        cls.not_started = cls.env["riverflow.state"].create(
            {"name": "Not Started", "workflow_id": cls.workflow.id, "sequence": 10}
        )
        cls.done = cls.env["riverflow.state"].create(
            {"name": "Done", "workflow_id": cls.workflow.id, "sequence": 20, "is_end_state": True}
        )
        cls.env["riverflow.transition"].create(
            {
                "name": "Finish",
                "from_state_id": cls.not_started.id,
                "to_state_id": cls.done.id,
                "action_id": cls.env.ref("riverflow.transition_action_default").id,
            }
        )
        cls.service = cls.env["riverflow.service"].create(
            {"name": "Collect Something", "workflow_id": cls.workflow.id, "state_id": cls.not_started.id}
        )

    def test_the_json_names_the_linked_record(self):
        linked = self.service._linked_transition_buttons_json()
        self.assertEqual((linked["res_model"], linked["res_id"]), ("riverflow.service", self.service.id))
        self.assertEqual(linked["layout"], "form_header")
        self.assertTrue(linked["reload_on_close"])
        self.assertEqual(
            [b["caption"] for b in linked["buttons"]],
            [b["caption"] for b in self.service.transition_buttons_json["buttons"]],
        )
        self.assertIn("Finish", [b["caption"] for b in linked["buttons"]])
