# test_audit_coverage.py
# The coverage rules: which fields and records the audit log writes (19.0.1.723, plan
# oteny-audit/plans/audit-storage-reduction.md), and the insert that writes only the record's id,
# the changes in the transaction of the create that write nothing, and the rows of a database
# cascade (19.0.1.747, plan oteny-audit/plans/audit-insert-id-only.md).
from odoo.tests import TransactionCase, tagged

from .common import end_transaction


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditCoverage(TransactionCase):
    def setUp(self):
        super().setUp()
        self.log_model = self.env["oteny.audit.log"]
        self.ref_model = self.env["oteny.audit.log.ref"]
        self.coverage = self.env["oteny.audit.test.coverage"]

    def _rows(self, record_or_model, record_id=None, change_type=None):
        self.env.flush_all()
        model_name = record_or_model._name
        record_id = record_id or record_or_model.id
        domain = [("model_name", "=", model_name), ("record_id", "=", record_id)]
        if change_type:
            domain.append(("change_type", "=", change_type))
        return self.log_model.search(domain, order="id")

    def _delete_snapshot(self, record):
        """Delete `record` and return the snapshot of its delete row."""
        model, record_id = record.browse(), record.id
        record.unlink()
        delete = self._rows(model, record_id, "d")
        self.assertEqual(len(delete), 1)
        return delete.snapshot or {}

    # --- the insert ------------------------------------------------------------------- #

    def test_an_insert_writes_one_row_with_the_id(self):
        record = self.coverage.create({"name": "New", "code_state": "draft", "count": 3})
        insert = self._rows(record)
        self.assertEqual(len(insert), 1)
        self.assertEqual(
            (insert.change_type, insert.field_name, insert.new_value, insert.snapshot),
            ("i", "id", str(record.id), False),
        )
        self.assertEqual(insert.record_display_name, record.display_name)

    def test_changes_in_the_create_transaction_write_nothing(self):
        child = self.env["oteny.audit.test.child"].create({"name": "Tag"})
        record = self.coverage.create({"name": "First"})
        record.write({"name": "Second", "count": 3})
        record.flush_recordset()
        record.write({"name": "Third", "tag_ids": [(4, child.id)]})
        rows = self._rows(record)
        self.assertEqual(rows.mapped("change_type"), ["i"], "the record holds the value at the commit")

    def test_parent_set_after_the_create_gives_the_insert_its_parent_ref(self):
        parent = self.env["oteny.audit.test.parent"].create({"name": "Late parent"})
        child = self.env["oteny.audit.test.child"].create({"name": "Late child"})
        child.write({"parent_id": parent.id})
        insert = self._rows(child, change_type="i")
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
        update = self._rows(record, change_type="u").filtered(lambda row: row.field_name == "name")
        self.assertEqual((update.old_value, update.new_value), ("Before", "After"))

    def test_an_update_after_the_insert_row_is_gone_is_logged(self):
        record = self.coverage.create({"name": "Gone insert"})
        self._rows(record, change_type="i").unlink()
        record.write({"name": "Still logged"})
        self.assertTrue(self._rows(record, change_type="u").filtered(lambda row: row.field_name == "name"))

    def test_the_value_at_the_insert_is_the_old_value_of_the_first_update(self):
        """The reconstruction rule of 19.0.1.747: a field's value at the insert is the old value
        of its first update row after the insert, else the current value."""
        record = self.coverage.create({"name": "Original", "count": 1})
        end_transaction(self.env)
        record.write({"name": "Changed"})
        end_transaction(self.env)
        record.write({"name": "Changed again"})
        updates = self._rows(record, change_type="u").filtered(lambda row: row.field_name == "name")
        self.assertEqual(updates[0].old_value, "Original")
        self.assertEqual(record.count, 1, "a field without an update row still has its insert value")

    # --- which fields --------------------------------------------------------------- #

    def test_readonly_field_without_compute_is_logged(self):
        """Cut I: code writes a field that users cannot edit; the log shows it."""
        record = self.coverage.create({"name": "Cover", "code_state": "draft"})
        end_transaction(self.env)
        record.write({"code_state": "posted"})
        update = self._rows(record, change_type="u").filtered(lambda row: row.field_name == "code_state")
        self.assertEqual((update.old_value, update.new_value), ("draft", "posted"))
        self.assertEqual(self._delete_snapshot(record)["code_state"]["raw"], "posted")

    def test_readonly_compute_readonly_active_and_parent_path_are_not_logged(self):
        parent = self.coverage.create({"name": "Root"})
        record = self.coverage.create({"name": "Leaf", "parent_id": parent.id})
        end_transaction(self.env)
        record.write({"active": False})
        self.assertFalse(
            self._rows(record, change_type="u").filtered(lambda row: row.field_name == "active"),
            "a read-only active is only written by a retire right before the delete",
        )
        snapshot = self._delete_snapshot(record)
        self.assertNotIn("name_upper", snapshot, "a read-only compute is explained by its inputs")
        self.assertNotIn("parent_path", snapshot, "parent_path repeats parent_id")
        self.assertIn("parent_id", snapshot)

    def test_ignore_fields_of_the_model_and_of_its_mixin_both_apply(self):
        """A class attribute is overridden by a later class; the sets must be joined."""
        record = self.coverage.create({"name": "Ignored", "skipped_note": "a", "mixin_note": "b"})
        end_transaction(self.env)
        record.write({"skipped_note": "c", "mixin_note": "d"})
        self.assertFalse(self._rows(record, change_type="u"))
        snapshot = self._delete_snapshot(record)
        self.assertNotIn("skipped_note", snapshot)
        self.assertNotIn("mixin_note", snapshot)

    def test_delete_snapshot_keeps_its_inclusive_rule(self):
        record = self.coverage.create({"name": "Gone", "flag_on": True})
        self.assertEqual(self._delete_snapshot(record)["flag_on"]["raw"], "True")

    # --- a database cascade ------------------------------------------------------------ #

    def test_a_database_cascade_writes_the_delete_rows_of_child_and_grandchild(self):
        parent = self.env["oteny.audit.test.parent"].create({"name": "Top"})
        child = self.env["oteny.audit.test.child"].create({"name": "Middle", "parent_id": parent.id})
        grandchild = self.coverage.create({"name": "Bottom", "holder_id": child.id, "count": 7})
        end_transaction(self.env)
        Child, child_id, grandchild_id = child.browse(), child.id, grandchild.id
        parent.unlink()  # PostgreSQL deletes the child and the grandchild (ON DELETE CASCADE)
        self.assertFalse(grandchild.exists())
        child_delete = self._rows(Child, child_id, "d")
        self.assertEqual(child_delete.snapshot["name"]["raw"], "Middle")
        grandchild_delete = self._rows(self.coverage, grandchild_id, "d")
        self.assertEqual(grandchild_delete.snapshot["count"]["raw"], "7")
        self.assertEqual(grandchild_delete.snapshot["holder_id"]["raw"], str(child_id))

    def test_a_database_set_null_writes_an_update_row(self):
        parent = self.coverage.create({"name": "Pointed to"})
        record = self.coverage.create({"name": "Pointer", "parent_id": parent.id})
        end_transaction(self.env)
        parent.unlink()  # PostgreSQL sets record.parent_id to NULL (ON DELETE SET NULL)
        update = self._rows(record, change_type="u").filtered(lambda row: row.field_name == "parent_id")
        self.assertEqual(
            (update.old_value, update.old_value_display_name, update.new_value),
            (str(parent.id), "Pointed to", ""),
        )

    def test_a_record_deleted_twice_gets_one_delete_row(self):
        """A cascade wrote the row; an ORM delete of the same record afterwards adds none."""
        parent = self.env["oteny.audit.test.parent"].create({"name": "Top"})
        child = self.env["oteny.audit.test.child"].create({"name": "Middle", "parent_id": parent.id})
        end_transaction(self.env)
        Child, child_id = child.browse(), child.id
        parent.unlink()
        Child.browse(child_id).unlink()
        self.assertEqual(len(self._rows(Child, child_id, "d")), 1)

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
        end_transaction(self.env)
        snapshot = self._delete_snapshot(attachment)
        for name in ("checksum", "mimetype", "file_size", "index_content"):
            self.assertNotIn(name, snapshot)
        self.assertIn("store_fname", snapshot, "the content hash proves which file was uploaded")

    def test_message_delivery_fields_are_not_logged(self):
        """Rule N3."""
        partner = self.env["res.partner"].create({"name": "Mail target"})
        message = partner.message_post(body="Hello", message_type="comment")
        end_transaction(self.env)
        snapshot = self._delete_snapshot(message)
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
        end_transaction(self.env)
        entry = self._delete_snapshot(record)["kind"]
        self.assertEqual((entry["raw"], entry["display"]), ("alpha", "Alpha kind"))

    def test_caption_shows_the_label_of_an_old_selection_row(self):
        """A row written before 19.0.1.723 holds the technical value as its display value."""
        record = self.coverage.create({"name": "Old kind"})
        end_transaction(self.env)
        record.write({"kind": "beta"})
        update = self._rows(record, change_type="u").filtered(lambda row: row.field_name == "kind")
        update.new_value_display_name = "beta"  # as the old code stored it
        self.env.flush_all()
        row = self.env["oteny.audit.log.aggregated"].search(
            [("audit_log_id", "=", update.id), ("model_name", "=", record._name)]
        )
        self.assertIn("Beta kind", row.caption)

    def test_the_caption_of_an_insert_is_its_header(self):
        record = self.coverage.create({"name": "Header only"})
        self.env.flush_all()
        row = self.env["oteny.audit.log.aggregated"].search(
            [("model_name", "=", record._name), ("record_id", "=", record.id)]
        )
        self.assertEqual(len(row), 1)
        self.assertIn("Insert", row.caption)
        self.assertNotIn("Set <b>", row.caption)

    def test_fields_of_a_delete_read_in_the_order_of_their_label(self):
        record = self.coverage.create({"name": "Order", "code_state": "x", "kind": "alpha", "count": 2})
        end_transaction(self.env)
        Coverage, record_id = record.browse(), record.id
        record.unlink()
        self.env.flush_all()
        rows = self.env["oteny.audit.log.aggregated"].search(
            [("model_name", "=", Coverage._name), ("record_id", "=", record_id), ("change_type", "=", "d")]
        )
        labels = rows.mapped("field_display_name")
        self.assertEqual(labels, sorted(labels))
