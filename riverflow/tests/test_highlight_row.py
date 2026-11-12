"""A row is highlighted after a user change, never after a form's copy of the record.

An onchange works on a copy of the record (a NewId with the record as origin), and
Odoo computes a stored, read-only field again on that copy. The message summary
compute marked the copy as changed by a user. So every onchange of a form with a
list of services reported a changed service (highlight_row), and the browser sent
that change back with the next onchange (radar Oops Log 18, 12-Nov-2026: the
contact table of a ship).
"""

from datetime import datetime

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_highlight_row")
class TestHighlightRow(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        workflow = cls.env["riverflow.workflow"].create({"model_id": service_model.id, "name": "Highlight WF"})
        state = cls.env["riverflow.state"].create({"workflow_id": workflow.id, "name": "Open", "sequence": 10})
        cls.service = cls.env["riverflow.service"].create(
            {"name": "Highlight row", "workflow_id": workflow.id, "state_id": state.id}
        )
        cls.last_week = datetime(2026, 11, 5, 14, 0, 0)

    def test_message_summary_on_a_form_copy_is_no_user_change(self):
        self.service._send_dummy_external_message()
        self.assertTrue(self.service.external_messages_summary)
        self.service.user_write_date = self.last_week

        copy = self.service.new(origin=self.service)
        self.assertTrue(copy.external_messages_summary, "The copy computes the summary again.")
        self.assertEqual(copy.user_write_date, self.last_week)
        self.assertFalse(copy.highlight_row)

    def test_new_message_is_a_user_change(self):
        self.service.user_write_date = self.last_week
        self.service._send_dummy_external_message()
        self.assertTrue(self.service.external_messages_summary)
        self.assertGreater(self.service.user_write_date, self.last_week)
