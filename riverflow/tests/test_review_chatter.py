"""Mark as Reviewed in the chatter top bar (radar plan
riverflow/plans/mark-as-reviewed-in-chatter.md).

- The thread data of a record with riverflow.mail.thread.review.mixin carries
  unreviewedMessageCount when the chatter asks for it (D2); a record without the
  mixin sends no count, so its chatter shows nothing.
- Mark as Reviewed brings the count to 0 and sends the new count to the internal
  followers through the bus (D3, D5).
"""

from unittest.mock import patch

from odoo.addons.mail.tools.discuss import Store
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_review_chatter")
class TestReviewChatter(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        workflow = cls.env["riverflow.workflow"].create({"model_id": service_model.id, "name": "Review WF"})
        state = cls.env["riverflow.state"].create({"workflow_id": workflow.id, "name": "Open", "sequence": 10})
        cls.service = cls.env["riverflow.service"].create(
            {"name": "Review chatter", "workflow_id": workflow.id, "state_id": state.id}
        )
        cls.partner = cls.env["res.partner"].create({"name": "No review mixin"})

    def _thread_values(self, record):
        store = Store()
        record._thread_to_store(store, [], request_list=["unreviewedMessageCount"])
        threads = store.get_result().get("mail.thread", [])
        return next((t for t in threads if t["id"] == record.id and t["model"] == record._name), {})

    def test_thread_data_carries_the_count(self):
        self.assertEqual(self._thread_values(self.service).get("unreviewedMessageCount"), 0)
        self.service._send_dummy_external_message()
        self.service._send_dummy_external_message()
        self.assertEqual(self._thread_values(self.service)["unreviewedMessageCount"], 2)

    def test_record_without_the_mixin_sends_no_count(self):
        self.assertNotIn("unreviewedMessageCount", self._thread_values(self.partner))

    def test_mark_as_reviewed_clears_and_sends_the_count(self):
        follower = self.env["res.users"].create(
            {"name": "Review Follower", "login": "review.follower", "group_ids": [(4, self.env.ref("base.group_user").id)]}
        )
        self.service.message_subscribe(partner_ids=follower.partner_id.ids)
        message = self.service._send_dummy_external_message()
        # The review time keeps whole seconds; a message of the same second still
        # counts as newer. A person reads the message before the click.
        self.env.cr.execute(
            "UPDATE mail_message SET create_date = create_date - interval '1 minute' WHERE id = %s", [message.id]
        )
        message.invalidate_recordset(["create_date"])
        self.assertEqual(self.service.unreviewed_message_count, 1)

        Users = type(self.env["res.users"])
        sent = []
        bus_send = Users._bus_send

        def spy(users, notification_type, message, /, **kwargs):
            sent.append((users, message))
            return bus_send(users, notification_type, message, **kwargs)

        with patch.object(Users, "_bus_send", spy):
            self.service.action_mark_external_messages_reviewed()
        self.assertEqual(self.service.unreviewed_message_count, 0)
        threads = [t for users, message in sent if users == follower for t in message.get("mail.thread", [])]
        self.assertIn(0, [t.get("unreviewedMessageCount") for t in threads if t["id"] == self.service.id])
