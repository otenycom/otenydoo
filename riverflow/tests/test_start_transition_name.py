"""A service started on the Add Service screen through a start transition
gets a name.

Add Service lists every start transition of every service workflow next to the
templates. Many start transitions hide the name field (default_name_invisible),
because a template names the service. Started without a template, the hidden
name stayed empty and the save stopped on "Missing required value for the
field Service Name" (radar, Collect Rijnvaartverklaring - Later, 19-Nov-2026).
The screen now proposes the workflow's name.
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow")
class TestStartTransitionName(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service").id
        cls.workflow = cls.env["riverflow.workflow"].create(
            {"name": "Collect Something", "model_id": service_model}
        )
        cls.not_started = cls.env["riverflow.state"].create(
            {"name": "Not Started", "workflow_id": cls.workflow.id, "sequence": 10}
        )
        cls.later = cls.env["riverflow.transition"].create(
            {
                "name": "Later",
                "from_state_id": False,
                "to_state_id": cls.not_started.id,
                "action_id": cls.env.ref("riverflow.transition_action_default").id,
                "action_context": "{'default_name_invisible': True}",
            }
        )
        cls.subject = cls.env["res.partner"].create({"name": "Subject"})

    def _start(self, **defaults):
        """Pick the start transition on the Add Service screen and open its wizard."""
        start = (
            self.env["riverflow.start.service"]
            .with_context(
                default_res_model="res.partner",
                default_res_id=self.subject.id,
                transition_id=self.later.id,
                **defaults,
            )
            .create({})
        )
        action = start.action_start_transition()
        Wizard = self.env[action["res_model"]].with_context(**action["context"])
        return Wizard.create({}), action["context"]

    def _new_services(self):
        return self.env["riverflow.service"].search(
            [("workflow_id", "=", self.workflow.id), ("res_id", "=", self.subject.id)]
        )

    def test_hidden_name_gets_the_workflow_name(self):
        wizard, context = self._start()
        self.assertTrue(wizard.name_invisible)
        self.assertEqual(wizard.name, "Collect Something")
        wizard.with_context(**context).action_save()

        service = self._new_services()
        self.assertEqual(len(service), 1)
        self.assertEqual(service.name, "Collect Something")
        self.assertEqual(service.state_id, self.not_started)
        self.assertEqual(service.res_model, "res.partner")

    def test_name_given_by_the_caller_is_kept(self):
        wizard, context = self._start(default_name="Collect Something for Subject")
        self.assertEqual(wizard.name, "Collect Something for Subject")
