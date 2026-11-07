# test_audit_coverage.py
# The coverage rules of 19.0.1.712 (plan oteny-audit/plans/audit-storage-reduction.md):
# which fields and records the audit log writes, and the fold of changes made in the
# transaction of a record's create into its insert snapshot (cut E).
from odoo.tests import TransactionCase, tagged

from .common import end_transaction


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditCoverage(TransactionCase):
    def setUp(self):
        super().setUp()
        self.log_model = self.env["oteny.audit.log"]
        self.ref_model = self.env["oteny.audit.log.ref"]
        self.coverage = self.env["oteny.audit.test.coverage"]

    def _rows(self, record, change_type=None):
        self.env.flush_all()
        domain = [("model_name", "=", record._name), ("record_id", "=", record.id)]
        if change_type:
            domain.append(("change_type", "=", change_type))
        return self.log_model.search(domain, order="id")

    def _snapshot(self, record):
        insert = self._rows(record, "i")
        self.assertEqual(len(insert), 1)
        return insert.snapshot or {}

    # --- which fields --------------------------------------------------------------- #

    def test_readonly_field_without_compute_is_logged(self):
        """Cut I: code writes a field that users cannot edit; the log shows it."""
        record = self.coverage.create({"name": "Cover", "code_state": "draft"})
        self.assertEqual(self._snapshot(record)["code_state"]["raw"], "draft")
        end_transaction(self.env)
        record.write({"code_state": "posted"})
        update = self._rows(record, "u").filtered(lambda row: row.field_name == "code_state")
        self.assertEqual((update.old_value, update.new_value), ("draft", "posted"))

    def test_readonly_compute_readonly_active_and_parent_path_are_not_logged(self):
        parent = self.coverage.create({"name": "Root"})
        record = self.coverage.create({"name": "Leaf", "parent_id": parent.id})
        snapshot = self._snapshot(record)
        self.assertNotIn("name_upper", snapshot, "a read-only compute is explained by its inputs")
        self.assertNotIn("parent_path", snapshot, "parent_path repeats parent_id")
        self.assertIn("parent_id", snapshot)
        end_transaction(self.env)
        record.write({"active": False})
        self.assertFalse(
            self._rows(record, "u").filtered(lambda row: row.field_name == "active"),
            "a read-only active is only written by a retire right before the delete",
        )

    def test_ignore_fields_of_the_model_and_of_its_mixin_both_apply(self):
        """A class attribute is overridden by a later class; the sets must be joined."""
        record = self.coverage.create({"name": "Ignored", "skipped_note": "a", "mixin_note": "b"})
        snapshot = self._snapshot(record)
        self.assertNotIn("skipped_note", snapshot)
        self.assertNotIn("mixin_note", snapshot)
        end_transaction(self.env)
        record.write({"skipped_note": "c", "mixin_note": "d"})
        self.assertFalse(self._rows(record, "u"))

    def test_empty_value_without_default_is_left_out_of_the_insert(self):
        """Rule N1: absent already means empty for a field without a default; an empty value
        of a field with a default is information."""
        record = self.coverage.create({"name": "Empty", "flag": False, "count": 0, "flag_on": False})
        snapshot = self._snapshot(record)
        self.assertNotIn("flag", snapshot)
        self.assertNotIn("count", snapshot)
        self.assertEqual(snapshot["flag_on"]["raw"], "False")

    def test_delete_snapshot_keeps_its_inclusive_rule(self):
        record = self.coverage.create({"name": "Gone", "flag_on": True})
        record_id = record.id
        record.unlink()
        self.env.flush_all()
        delete = self.log_model.search(
            [("model_name", "=", self.coverage._name), ("record_id", "=", record_id), ("change_type", "=", "d")]
        )
        self.assertEqual(delete.snapshot["flag_on"]["raw"], "True")

    # --- the fold (cut E) ------------------------------------------------------------ #

    def test_changes_in_the_create_transaction_fold_into_the_insert(self):
        record = self.coverage.create({"name": "First"})
        record.write({"name": "Second", "count": 3})
        record.flush_recordset()
        record.write({"name": "Third"})
        self.assertFalse(self._rows(record, "u"), "no update row in the transaction of the create")
        snapshot = self._snapshot(record)
        self.assertEqual(snapshot["name"]["raw"], "Third")
        self.assertEqual(snapshot["count"]["raw"], "3")

    def test_a_value_set_back_to_empty_leaves_the_insert(self):
        record = self.coverage.create({"name": "Count", "count": 5})
        record.write({"count": 0})
        self.assertNotIn("count", self._snapshot(record))

    def test_x2many_change_in_the_create_transaction_folds(self):
        child = self.env["oteny.audit.test.child"].create({"name": "Tag"})
        record = self.coverage.create({"name": "Tags"})
        record.write({"tag_ids": [(4, child.id)]})
        self.assertFalse(self._rows(record, "u"))
        self.assertIn(str(child.id), self._snapshot(record)["tag_ids"]["raw"])

    def test_parent_set_after_the_create_gives_the_insert_its_parent_ref(self):
        parent = self.env["oteny.audit.test.parent"].create({"name": "Late parent"})
        child = self.env["oteny.audit.test.child"].create({"name": "Late child"})
        child.write({"parent_id": parent.id})
        insert = self._rows(child, "i")
        refs = self.ref_model.search([("audit_log_id", "=", insert.id)])
        self.assertEqual(
            [(ref.target_model_name, ref.target_record_id, ref.is_direct) for ref in refs],
            [("oteny.audit.test.parent", parent.id, False)],
            "the creation of the child shows under its parent",
        )

    def test_a_later_transaction_still_writes_update_rows(self):
        record = self.coverage.create({"name": "Before"})
        end_transaction(self.env)
        record.write({"name": "After"})
        update = self._rows(record, "u").filtered(lambda row: row.field_name == "name")
        self.assertEqual((update.old_value, update.new_value), ("Before", "After"))
        self.assertEqual(self._snapshot(record)["name"]["raw"], "Before")

    def test_redaction_applies_to_a_folded_value(self):
        record = self.env["oteny.audit.test.secret"].create({"name": "Secret"})
        record.write({"access_token": "s3cr3t-value"})
        record.flush_recordset()
        insert = self._rows(record, "i")
        self.assertEqual(insert.snapshot["access_token"]["raw"], "***redacted***")

    def test_an_update_after_the_insert_row_is_gone_is_logged(self):
        record = self.coverage.create({"name": "Gone insert"})
        self._rows(record, "i").unlink()
        record.write({"name": "Still logged"})
        self.assertTrue(self._rows(record, "u").filtered(lambda row: row.field_name == "name"))

    # --- which records and models ----------------------------------------------------- #

    def test_generated_images_and_asset_bundles_write_no_rows(self):
        """Cut C and rule N4."""
        Attachment = self.env["ir.attachment"]
        source = Attachment.create({"name": "doc.pdf", "raw": b"%PDF-1.4"})
        thumbnail = Attachment.create({
            "name": "thumbnail", "raw": b"png", "res_model": "ir.attachment",
            "res_id": source.id, "res_field": "thumbnail",
        })
        bundle = Attachment.create({
            "name": "web.assets_web.min.js", "raw": b"js", "url": "/web/assets/1/web.assets_web.min.js",
            "res_model": "ir.ui.view", "public": True,
        })
        self.assertTrue(self._rows(source))
        self.assertFalse(self._rows(thumbnail))
        self.assertFalse(self._rows(bundle))
        bundle_id = bundle.id
        bundle.unlink()
        self.env.flush_all()
        self.assertFalse(self.log_model.search([("model_name", "=", "ir.attachment"), ("record_id", "=", bundle_id)]))

    def test_attachment_technical_fields_are_not_logged(self):
        attachment = self.env["ir.attachment"].create({"name": "notes.txt", "raw": b"hello"})
        snapshot = self._snapshot(attachment)
        for name in ("checksum", "mimetype", "file_size", "index_content"):
            self.assertNotIn(name, snapshot)
        self.assertIn("store_fname", snapshot, "the content hash proves which file was uploaded")

    def test_message_delivery_fields_are_not_logged(self):
        """Rule N3."""
        partner = self.env["res.partner"].create({"name": "Mail target"})
        message = partner.message_post(body="Hello", message_type="comment")
        snapshot = self._snapshot(message)
        for name in ("reply_to", "record_alias_domain_id", "record_company_id", "email_layout_xmlid",
                     "email_add_signature", "reply_to_force_new", "message_id"):
            self.assertNotIn(name, snapshot)
        self.assertIn("body", snapshot)

    def test_scheduler_and_constraint_rows_are_not_logged(self):
        """Cut D: ir.cron.trigger and ir.model.constraint are default-ignored."""
        Log = self.log_model
        self.assertTrue(Log._is_audit_ignored("ir.cron.trigger"))
        self.assertTrue(Log._is_audit_ignored("ir.model.constraint"))

    # --- what the screen shows ----------------------------------------------------------- #

    def test_selection_shows_its_label(self):
        record = self.coverage.create({"name": "Kind", "kind": "alpha"})
        entry = self._snapshot(record)["kind"]
        self.assertEqual((entry["raw"], entry["display"]), ("alpha", "Alpha kind"))

    def test_caption_shows_the_label_of_an_old_selection_row(self):
        """A row written before 19.0.1.712 holds the technical value as its display value."""
        record = self.coverage.create({"name": "Old kind"})
        end_transaction(self.env)
        record.write({"kind": "beta"})
        update = self._rows(record, "u").filtered(lambda row: row.field_name == "kind")
        update.new_value_display_name = "beta"  # as the old code stored it
        self.env.flush_all()
        row = self.env["oteny.audit.log.aggregated"].search(
            [("audit_log_id", "=", update.id), ("model_name", "=", record._name)]
        )
        self.assertIn("Beta kind", row.caption)

    def test_fields_of_an_insert_read_in_the_order_of_their_label(self):
        record = self.coverage.create({"name": "Order", "code_state": "x", "kind": "alpha", "count": 2})
        self.env.flush_all()
        rows = self.env["oteny.audit.log.aggregated"].search(
            [("model_name", "=", record._name), ("record_id", "=", record.id)], order="id desc"
        )
        labels = rows.mapped("field_display_name")
        self.assertEqual(labels, sorted(labels))
