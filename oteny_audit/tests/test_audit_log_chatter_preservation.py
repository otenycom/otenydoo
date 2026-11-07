# test_audit_log_chatter_preservation.py
#
# Coverage for the chatter preservation fix:
# - mail.message is audited (NOT in _DEFAULT_IGNORED_MODEL_NAMES)
# - mail.message.body is HTML-stripped (size bounded, prose readable)
# - Parent ref still resolves when the parent was unlinked earlier in the
#   same transaction (cascade-unlink case), so the chatter trail remains
#   visible under the parent's audit filter even after the parent is gone.
from odoo.tests import TransactionCase, tagged


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditLogChatterPreservation(TransactionCase):
    """Verify mail.message audit + cascade-unlink parent ref survival."""

    def setUp(self):
        super().setUp()
        self.log_model = self.env["oteny.audit.log"]
        self.ref_model = self.env["oteny.audit.log.ref"]

    def test_mail_message_is_not_default_ignored(self):
        """mail.message must be audited — it is the only durable copy of
        chatter content once the parent record is cascade-unlinked."""
        self.assertNotIn(
            "mail.message",
            self.log_model._DEFAULT_IGNORED_MODEL_NAMES,
            "mail.message must NOT be default-ignored — it is the only place "
            "that preserves chatter after a cascade unlink.",
        )
        self.assertFalse(self.log_model._is_audit_ignored("mail.message"))

    def test_mail_message_body_is_html_stripped(self):
        """mail.message creates an audit row whose body is plain text, not HTML."""
        partner = self.env["res.partner"].create({"name": "Chatter probe parent"})
        message = self.env["mail.message"].create(
            {
                "model": "res.partner",
                "res_id": partner.id,
                "subject": "Test",
                "body": "<p>Dear <b>customer</b>,</p><p>Your <i>order</i> is ready.</p>",
                "message_type": "comment",
            }
        )
        message.flush_recordset()

        snapshot_logs = self.log_model.search(
            [
                ("model_name", "=", "mail.message"),
                ("record_id", "=", message.id),
                ("change_type", "=", "i"),
                ("field_name", "=", "__snapshot__"),
            ]
        )
        self.assertEqual(len(snapshot_logs), 1)
        body_entry = (snapshot_logs.snapshot or {}).get("body")
        self.assertIsNotNone(body_entry, "body must be in the create snapshot")
        # Tags removed, prose preserved.
        self.assertNotIn("<", body_entry["raw"])
        self.assertNotIn(">", body_entry["raw"])
        self.assertIn("Dear", body_entry["raw"])
        self.assertIn("customer", body_entry["raw"])
        self.assertIn("order", body_entry["raw"])

    def test_chatter_audit_is_linked_to_parent_record(self):
        """Audit logs for chatter messages get a parent ref to their host record
        via the polymorphic `model,res_id` mapping in `_model_parent_keys`."""
        partner = self.env["res.partner"].create({"name": "Chatter parent"})

        # Drop creation logs for the partner to focus on chatter linking.
        self.log_model.search(
            [("model_name", "=", "res.partner"), ("record_id", "=", partner.id)]
        ).unlink()

        message = self.env["mail.message"].create(
            {
                "model": "res.partner",
                "res_id": partner.id,
                "subject": "Test linkage",
                "body": "<p>Body</p>",
                "message_type": "comment",
            }
        )
        message.flush_recordset()

        msg_logs = self.log_model.search(
            [("model_name", "=", "mail.message"), ("record_id", "=", message.id)]
        )
        refs = self.ref_model.search([("audit_log_id", "in", msg_logs.ids)])

        # Direct ref to mail.message + parent ref to res.partner.
        parent_refs = refs.filtered(
            lambda r: not r.is_direct
            and r.target_model_name == "res.partner"
            and r.target_record_id == partner.id
        )
        self.assertTrue(parent_refs, "Chatter audit must have parent ref to host record")

    def test_chatter_delete_preserves_parent_ref_through_cascade_unlink(self):
        """The forensic guarantee: when a host record is unlinked and its
        chatter is cascade-deleted in the same transaction, the chatter's
        delete tombstone must still carry a parent ref pointing at the
        (now-gone) host record. Without this, the chatter trail would be
        invisible from the host's audit filter — the exact gap that motivated
        this fix.
        """
        partner = self.env["res.partner"].create({"name": "Cascade unlink probe"})

        message = self.env["mail.message"].create(
            {
                "model": "res.partner",
                "res_id": partner.id,
                "subject": "Important email",
                "body": "<p>This is the only copy of this content.</p>",
                "message_type": "comment",
            }
        )
        message.flush_recordset()

        partner_id = partner.id
        message_id = message.id

        # Unlink the host. This cascade-unlinks the chatter via MailThread.
        partner.unlink()

        # The mail_message row is gone from the live table (cascade verified).
        self.assertFalse(
            self.env["mail.message"].browse(message_id).exists(),
            "mail.message must be cascade-deleted with its host (Odoo MailThread behaviour)",
        )

        # The chatter's delete tombstone exists in the audit log.
        chatter_delete = self.log_model.search(
            [
                ("model_name", "=", "mail.message"),
                ("record_id", "=", message_id),
                ("change_type", "=", "d"),
                ("field_name", "=", "__snapshot__"),
            ]
        )
        self.assertEqual(
            len(chatter_delete),
            1,
            "Chatter delete must produce a tombstone snapshot row",
        )

        # The body content is preserved (stripped of HTML) in the snapshot.
        body_entry = (chatter_delete.snapshot or {}).get("body")
        self.assertIsNotNone(body_entry)
        self.assertIn("only copy of this content", body_entry["raw"])

        # The critical invariant: parent ref to res.partner still exists,
        # even though the partner was unlinked earlier in the same transaction.
        chatter_delete_refs = self.ref_model.search(
            [("audit_log_id", "=", chatter_delete.id)]
        )
        parent_ref = chatter_delete_refs.filtered(
            lambda r: not r.is_direct
            and r.target_model_name == "res.partner"
            and r.target_record_id == partner_id
        )
        self.assertEqual(
            len(parent_ref),
            1,
            "Chatter delete tombstone must carry a parent ref to the unlinked host, "
            "so the chatter trail remains discoverable via parent-record audit filter.",
        )

        # The parent ref carries a recovered display_name from the host's own
        # delete tombstone (written earlier in the same transaction).
        self.assertTrue(
            parent_ref.parent_display_name,
            "Parent ref must have a non-empty display_name even when host is gone",
        )
        # Should NOT fall back to the bare "ID: N" placeholder, since the host
        # was audited and its display_name lives on its delete tombstone.
        self.assertNotEqual(parent_ref.parent_display_name, f"ID: {partner_id}")

    def test_full_chatter_trail_discoverable_after_host_unlink(self):
        """End-to-end: send an email, delete the host, then the audit log alone
        is sufficient to answer "what email did we send for that record?".
        """
        partner = self.env["res.partner"].create({"name": "End-to-end probe"})
        partner_id = partner.id

        message = self.env["mail.message"].create(
            {
                "model": "res.partner",
                "res_id": partner.id,
                "subject": "End to end",
                "body": "<p>The body that must survive cascade.</p>",
                "message_type": "comment",
            }
        )
        message.flush_recordset()
        partner.unlink()

        # Search for ALL audit log rows tied to the (gone) partner: its own rows,
        # and the rows of its children through their parent refs.
        partner_refs = self.ref_model.search(
            [
                ("target_model_name", "=", "res.partner"),
                ("target_record_id", "=", partner_id),
            ]
        )
        own_logs = self.log_model.search(
            [("model_name", "=", "res.partner"), ("record_id", "=", partner_id)]
        )
        partner_logs = own_logs | partner_refs.mapped("audit_log_id")

        # We expect to find: partner insert snapshot, partner delete snapshot,
        # mail.message insert snapshot, mail.message delete snapshot.
        change_types_by_model = {
            (l.model_name, l.change_type) for l in partner_logs
        }
        self.assertIn(("res.partner", "i"), change_types_by_model)
        self.assertIn(("res.partner", "d"), change_types_by_model)
        self.assertIn(("mail.message", "i"), change_types_by_model)
        self.assertIn(("mail.message", "d"), change_types_by_model)

        # And the email body is recoverable (in plain-text form) from the
        # mail.message delete snapshot.
        msg_delete = partner_logs.filtered(
            lambda l: l.model_name == "mail.message" and l.change_type == "d"
        )
        self.assertEqual(len(msg_delete), 1)
        body = (msg_delete.snapshot or {}).get("body", {}).get("raw", "")
        self.assertIn("must survive cascade", body)
