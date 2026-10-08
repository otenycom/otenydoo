"""riverflow.service._write_changed: a compute writes only what changed."""
from unittest.mock import patch

from odoo import Command
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_service_write_changed")
class TestServiceWriteChanged(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        workflow = cls.env["riverflow.workflow"].create({"model_id": service_model.id, "name": "Write Changed WF"})
        State = cls.env["riverflow.state"]
        cls.state_a = State.create({"workflow_id": workflow.id, "name": "A", "sequence": 10})
        cls.state_b = State.create({"workflow_id": workflow.id, "name": "B", "sequence": 20})
        cls.tag = cls.env["riverflow.service.tag"].create({"name": "Write Changed Tag"})
        cls.service = cls.env["riverflow.service"].create(
            {
                "name": "Info",
                "workflow_id": workflow.id,
                "state_id": cls.state_a.id,
                "daily_prio": 10,
                "tag_ids": [Command.set(cls.tag.ids)],
            }
        )

    def test_unchanged_values_are_not_written(self):
        """Equal values (a Many2one id or record, a Command.set of the same ids)
        cost no write, so no dependency search and no state hook runs."""
        vals = {
            "name": "Info",
            "state_id": self.state_a.id,
            "daily_prio": 10,
            "tag_ids": [Command.set(self.tag.ids)],
        }
        Service = type(self.service)
        with patch.object(Service, "write", side_effect=AssertionError("unchanged values written")):
            self.assertFalse(self.service._write_changed(vals))
            self.assertFalse(self.service._write_changed({"state_id": self.state_a}))

    def test_only_the_changed_values_are_written(self):
        Service = type(self.service)
        written = []
        write = Service.write

        def spy(records, vals):
            written.append(dict(vals))
            return write(records, vals)

        with patch.object(Service, "write", spy):
            self.assertTrue(
                self.service._write_changed({"name": "Info", "state_id": self.state_b.id, "daily_prio": 1})
            )
        self.assertEqual(written[0], {"state_id": self.state_b.id, "daily_prio": 1})
        self.assertEqual(self.service.state_id, self.state_b)
        self.assertEqual(self.service.daily_prio, 1)
