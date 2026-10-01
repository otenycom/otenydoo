"""A bounced outgoing email opens one Review email bounce task.

The live hook is mail.thread._routing_handle_bounce, after that method
writes notification_status bounce and failure_type mail_bounce. The
temporary exception mail.mail._send writes is a different write and does
not open a task. Mail send is mocked: these tests do not call a mail server.
"""

from datetime import timedelta
from unittest.mock import patch

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("riverflow", "post_install", "-at_install", "test_review_email_bounce")
class TestReviewEmailBounce(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Team = cls.env["riverflow.team"]
        cls.parent_team = Team.create({"name": "Bounce Parent Team"})
        cls.template_team = Team.create({"name": "Bounce Template Team"})
        cls.template = cls.env.ref("riverflow.template_service_review_email_bounce")
        cls.note_subtype = cls.env.ref("mail.mt_note")
        cls.Service = cls.env["riverflow.service"]

    def setUp(self):
        super().setUp()
        patcher = patch.object(
            type(self.env["mail.mail"]), "send", autospec=True, return_value=True
        )
        self.send_mock = patcher.start()
        self.addCleanup(patcher.stop)
        self.mails_before = set(self.env["mail.mail"].search([]).ids)

    def _parent_service(self):
        return self.Service.create(
            {
                "name": "Bounce parent",
                "project_deadline": fields.Date.today(),
                "responsible_team_id": self.parent_team.partner_id.id,
            }
        )

    def _partner(self, name, email):
        return self.env["res.partner"].create({"name": name, "email": email})

    def _message(self, owner, message_type="email_outgoing", when=None):
        message = self.env["mail.message"].sudo().create(
            {
                "model": owner._name,
                "res_id": owner.id,
                "message_type": message_type,
                "subtype_id": self.env.ref("mail.mt_comment").id,
                "subject": "Outbound",
                "body": "<p>Hello</p>",
            }
        )
        if when is not None:
            message.date = when
        return message

    def _notification(self, message, partner, status="sent", failure_type=False, reason=False):
        return self.env["mail.notification"].sudo().create(
            {
                "mail_message_id": message.id,
                "res_partner_id": partner.id,
                "notification_type": "email",
                "notification_status": status,
                "mail_email_address": partner.email,
                "failure_type": failure_type or False,
                "failure_reason": reason or False,
            }
        )

    def _route_bounce(self, message, partner, reason):
        # The clone copies this team. It is not the parent team.
        self.template.responsible_team_id = self.template_team.partner_id
        self.env["mail.thread"]._routing_handle_bounce(
            False,
            {
                "bounced_email": partner.email,
                "bounced_partner": partner,
                "bounced_msg_ids": ["<bounce-review@example.com>"],
                "bounced_message": message,
                "email_from": "mailer-daemon@example.com",
                "to": "bounce@example.com",
                "message_id": f"<bounce-{message.id}-{partner.id}@example.com>",
                "body": f"<p>{reason}</p>",
            },
        )

    def _children(self, message):
        return self.Service.with_context(active_test=False).search(
            [
                ("bounced_mail_message_id", "=", message.id),
                ("active", "in", (True, False)),
            ]
        )

    def _notes(self, record, message):
        marker = self.Service._bounce_note_marker(message)
        return record.message_ids.filtered(
            lambda note: note.message_type == "comment"
            and note.subtype_id == self.note_subtype
            and marker in (note.body or "")
        )

    def _sentence(self, *partners):
        """The visible sentence. Brackets are escaped because the body is HTML.

        The name is display_name, the same value the note builder writes.
        """
        parts = [f"{partner.display_name} &lt;{partner.email}&gt;" for partner in partners]
        return "Bounced email to " + ", ".join(parts)

    def _assert_no_mail_sent(self):
        self.send_mock.assert_not_called()
        created = set(self.env["mail.mail"].search([]).ids) - self.mails_before
        self.assertFalse(created, "the bounce review must not create a mail.mail")

    def test_bounce_opens_one_child_and_both_notes(self):
        parent = self._parent_service()
        partner = self._partner("Bounce Recipient", "bounce-one@example.com")
        message = self._message(parent)
        notification = self._notification(message, partner)
        self._route_bounce(message, partner, "550 5.4.1 Recipient address rejected")

        self.assertEqual(notification.notification_status, "bounce")
        self.assertEqual(notification.failure_type, "mail_bounce")
        child = self._children(message)
        self.assertEqual(len(child), 1)
        self.assertEqual(child.parent_id, parent)
        self.assertFalse(child.is_this_a_template)
        self.assertEqual(child.workflow_id, self.env.ref("riverflow.workflow_service_task"))
        self.assertEqual(child.state_id, self.env.ref("riverflow.state_task_not_started"))
        self.assertEqual(child.responsible_team_id, parent.responsible_team_id)
        self.assertNotEqual(child.responsible_team_id, self.template.responsible_team_id)
        self.assertEqual(child.use_project_deadline_from, "self")
        self.assertEqual(child.project_deadline, fields.Date.today())
        self.assertEqual(child.bounced_mail_message_id, message)

        child_notes = self._notes(child, message)
        parent_notes = self._notes(parent, message)
        self.assertEqual(len(child_notes), 1)
        self.assertEqual(len(parent_notes), 1)
        sentence = self._sentence(partner)
        for note in child_notes | parent_notes:
            self.assertEqual(note.message_type, "comment")
            self.assertEqual(note.subtype_id, self.note_subtype)
            self.assertFalse(note.partner_ids)
            self.assertIn(sentence, note.body)
            self.assertNotIn(f"Bounced email {message.id}", note.body)
            self.assertIn("550 5.4.1", note.body)
        self._assert_no_mail_sent()

    def test_second_notification_on_the_same_message_appends(self):
        parent = self._parent_service()
        first = self._partner("First Recipient", "bounce-first@example.com")
        second = self._partner("Second Recipient", "bounce-second@example.com")
        message = self._message(parent)
        self._notification(message, first)
        self._notification(message, second)
        self._route_bounce(message, first, "550 5.4.1 first address")
        self._route_bounce(message, second, "550 5.1.1 second address")

        children = self._children(message)
        self.assertEqual(len(children), 1)
        child_notes = self._notes(children, message)
        parent_notes = self._notes(parent, message)
        self.assertEqual(len(child_notes), 1)
        self.assertEqual(len(parent_notes), 1)
        sentence = self._sentence(first, second)
        for note in child_notes | parent_notes:
            self.assertIn(sentence, note.body)
            self.assertNotIn(f"Bounced email {message.id}", note.body)
            self.assertIn("550 5.4.1", note.body)
            self.assertIn("550 5.1.1", note.body)
        self._assert_no_mail_sent()

    def test_later_message_opens_its_own_child(self):
        parent = self._parent_service()
        partner = self._partner("Later Recipient", "bounce-later@example.com")
        first = self._message(parent)
        second = self._message(parent)
        self._notification(first, partner)
        self._notification(second, partner)
        self._route_bounce(first, partner, "550 5.4.1 first message")
        self._route_bounce(second, partner, "550 5.4.1 second message")

        first_child = self._children(first)
        second_child = self._children(second)
        self.assertEqual(len(first_child), 1)
        self.assertEqual(len(second_child), 1)
        self.assertNotEqual(first_child, second_child)
        self.assertEqual(len(self._notes(parent, first)), 1)
        self.assertEqual(len(self._notes(parent, second)), 1)
        self._assert_no_mail_sent()

    def test_closed_child_opens_a_new_one(self):
        parent = self._parent_service()
        partner = self._partner("Closed Recipient", "bounce-closed@example.com")
        message = self._message(parent)
        self._notification(message, partner)
        self._route_bounce(message, partner, "550 5.4.1 closed")
        first = self._children(message)
        first.state_id = self.env.ref("riverflow.state_task_done")
        self.assertTrue(first.is_end_state)
        self._route_bounce(message, partner, "550 5.4.1 closed again")

        children = self._children(message)
        self.assertEqual(len(children), 2)
        open_children = children.filtered(lambda service: service.active and not service.is_end_state)
        self.assertEqual(open_children, children - first)
        self.assertEqual(len(open_children), 1)
        self._assert_no_mail_sent()

    def test_archived_template_does_nothing(self):
        """An archived template does not open a service and does not post.

        An open child that already exists is left as it is, including its note.
        """
        parent = self._parent_service()
        first = self._partner("Archived First", "bounce-arch-first@example.com")
        second = self._partner("Archived Second", "bounce-arch-second@example.com")
        message = self._message(parent)
        self._notification(message, first)
        self._notification(message, second)
        self._route_bounce(message, first, "550 5.4.1 before archive")
        child = self._children(message)
        note_body = self._notes(child, message).body
        self.template.active = False
        self._route_bounce(message, second, "550 5.4.1 after archive")

        self.assertEqual(self._children(message), child)
        self.assertEqual(self._notes(child, message).body, note_body)
        self.assertNotIn("bounce-arch-second@example.com", note_body or "")
        self._assert_no_mail_sent()

    def test_missing_template_does_nothing(self):
        parent = self._parent_service()
        partner = self._partner("Missing Recipient", "bounce-missing@example.com")
        message = self._message(parent)
        self._notification(message, partner)
        xmlid = self.env["ir.model.data"].search(
            [
                ("module", "=", "riverflow"),
                ("name", "=", "template_service_review_email_bounce"),
            ]
        )
        xmlid.unlink()
        self._route_bounce(message, partner, "550 5.4.1 missing xmlid")

        self.assertFalse(self._children(message))
        self.assertFalse(self._notes(parent, message))
        self._assert_no_mail_sent()

    def test_exception_write_does_not_open_a_service(self):
        """mail.mail._send writes exception / unknown on the way out.

        That write is not a bounce. It must not open a service, including
        for mail that then sends.
        """
        parent = self._parent_service()
        partner = self._partner("Exception Recipient", "bounce-exception@example.com")
        message = self._message(parent)
        notification = self._notification(message, partner)
        notification.write(
            {
                "notification_status": "exception",
                "failure_type": "unknown",
                "failure_reason": "temporary",
            }
        )
        self.assertFalse(self._children(message))
        self.assertFalse(self._notes(parent, message))
        self._assert_no_mail_sent()

    def test_record_that_cannot_own_a_service_is_skipped(self):
        channel = self.env["discuss.channel"].create({"name": "Bounce channel"})
        self.assertFalse(self.Service._record_can_own_service(channel))
        partner = self._partner("Channel Recipient", "bounce-channel@example.com")
        message = self._message(channel)
        self._notification(message, partner)
        self._route_bounce(message, partner, "550 5.4.1 channel")
        self.assertFalse(self._children(message))
        self._assert_no_mail_sent()

    def test_other_owner_uses_res_model_without_parent(self):
        holder = self._partner("Subject Holder", "holder-bounce@example.com")
        self.Service.create(
            {
                "name": "Subject link",
                "res_model": "res.partner",
                "res_id": holder.id,
                "project_deadline": fields.Date.today(),
            }
        )
        owner = self._partner("Bounce Owner", "bounce-owner@example.com")
        message = self._message(owner)
        self._notification(message, owner)
        self._route_bounce(message, owner, "550 5.4.1 owner")

        child = self._children(message)
        self.assertEqual(len(child), 1)
        self.assertFalse(child.parent_id)
        self.assertEqual(child.res_model, "res.partner")
        self.assertEqual(child.res_id, owner.id)
        self.assertTrue(self._notes(owner, message))
        self.assertTrue(self._notes(child, message))
        self._assert_no_mail_sent()

    def test_migration_skips_a_bounce_older_than_72_hours(self):
        parent = self._parent_service()
        message = self._message(parent, when=fields.Datetime.now() - timedelta(hours=73))
        partner = self._partner("Old Recipient", "old-bounce@example.com")
        self._notification(
            message,
            partner,
            status="bounce",
            failure_type="mail_bounce",
            reason="550 5.4.1 old",
        )
        self.Service._backfill_review_email_bounces()
        self.assertFalse(self._children(message))
        self.assertFalse(self._notes(parent, message))
        self._assert_no_mail_sent()

    def test_migration_skips_a_latest_send_with_no_bounce(self):
        """The latest email did not bounce, so an older bounce is skipped.

        A newer note and a newer tracking message are not sends. They do not
        become the latest message, and a bounce on them does not qualify.
        """
        parent = self._parent_service()
        now = fields.Datetime.now()
        older = self._message(parent, when=now - timedelta(hours=2))
        bounced = self._partner("Older Recipient", "older-bounce@example.com")
        self._notification(
            older,
            bounced,
            status="bounce",
            failure_type="mail_bounce",
            reason="550 5.4.1 older",
        )
        latest = self._message(parent, when=now - timedelta(hours=1))
        sent = self._partner("Latest Recipient", "latest-sent@example.com")
        self._notification(latest, sent, status="sent")
        note = self._message(parent, message_type="comment", when=now)
        self._notification(
            note,
            bounced,
            status="bounce",
            failure_type="mail_bounce",
            reason="550 5.4.1 note",
        )
        tracking = self._message(parent, message_type="notification", when=now)
        self._notification(
            tracking,
            bounced,
            status="bounce",
            failure_type="mail_bounce",
            reason="550 5.4.1 tracking",
        )
        self.Service._backfill_review_email_bounces()
        for message in (older, latest, note, tracking):
            self.assertFalse(self._children(message))
        self._assert_no_mail_sent()

    def test_migration_opens_a_service_for_a_recent_bounce(self):
        parent = self._parent_service()
        self.template.responsible_team_id = self.template_team.partner_id
        message = self._message(
            parent,
            message_type="email",
            when=fields.Datetime.now() - timedelta(hours=1),
        )
        partner = self._partner("Recent Recipient", "recent-bounce@example.com")
        self._notification(
            message,
            partner,
            status="bounce",
            failure_type="mail_bounce",
            reason="550 5.4.1 recent",
        )
        opened = self.Service._backfill_review_email_bounces()
        child = self._children(message)
        self.assertEqual(len(child), 1)
        self.assertGreaterEqual(opened, 1)
        self.assertEqual(child.parent_id, parent)
        self.assertEqual(child.responsible_team_id, parent.responsible_team_id)
        self.assertEqual(child.project_deadline, fields.Date.today())
        note = self._notes(child, message)
        self.assertEqual(len(note), 1)
        self.assertIn("recent-bounce@example.com", note.body)
        self.assertIn("550 5.4.1", note.body)
        self.assertFalse(note.partner_ids)
        self.assertTrue(self._notes(parent, message))
        self._assert_no_mail_sent()
