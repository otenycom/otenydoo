from odoo.tests.common import TransactionCase
from odoo.tests import tagged

from .common import end_transaction


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditParentRef(TransactionCase):
    def setUp(self):
        super().setUp()
        self.parent_model = self.env["oteny.audit.test.parent"]
        self.child_model = self.env["oteny.audit.test.child"]
        self.log_model = self.env["oteny.audit.log"]
        self.ref_model = self.env["oteny.audit.log.ref"]
        self.aggregated_log_model = self.env["oteny.audit.log.aggregated"]

        self.parent_record = self.parent_model.create({"name": "Test Parent"})

    def dump_aggregated_log(self, parent_record):
        """Helper to print the content of the aggregated log for a given parent."""
        print(
            f"\n--- Dumping aggregated logs for {parent_record.display_name} ({parent_record._name}:{parent_record.id}) ---"
        )
        # We need to invalidate the cache for the view model to make sure we get fresh results
        self.aggregated_log_model.invalidate_model()
        aggregated_logs = self.aggregated_log_model.search(
            [
                ("model_name", "=", parent_record._name),
                ("record_id", "=", parent_record.id),
            ]
        )
        if not aggregated_logs:
            print("No aggregated logs found.")
        for log in aggregated_logs:
            print(f"  Log ID: {log.id}, Change: {log.change_type}, Is Child: {log.is_child_log}")
            if log.is_child_log:
                print(f"    Child Record: {log.child_model_name}:{log.child_record_id}")
            print(f"    Field: {log.field_display_name} ('{log.field_name}')")
            print(f"    Old Value: {log.old_value_display_name}")
            print(f"    New Value: {log.new_value_display_name}")
        print("----------------------------------------------------\n")

    def test_parent_child_ref(self):
        """Changes to a child record create parent references in the audit log.

        With Measure 3, a child create produces ONE tombstone snapshot row for the
        child. The row shows under the child itself without a reference (the view
        reads it from the log), and it gets one parent ref (to the parent). Later
        updates remain per-field and get their own parent ref.
        """
        # 1. Create a child record
        child_record = self.child_model.create({"name": "Test Child 1", "parent_id": self.parent_record.id})

        # 2. The create produces a single snapshot insert log for the child.
        snapshot_log = self.log_model.search(
            [
                ("model_name", "=", self.child_model._name),
                ("record_id", "=", child_record.id),
                ("change_type", "=", "i"),
                ("field_name", "=", "__snapshot__"),
            ]
        )
        self.assertEqual(len(snapshot_log), 1, "Should find one tombstone snapshot for the child create.")
        snapshot = snapshot_log.snapshot or {}
        self.assertIn("name", snapshot)
        self.assertEqual(snapshot["name"]["raw"], "Test Child 1")
        self.assertIn("parent_id", snapshot)

        # The snapshot row has only a parent ref (to parent): no direct ref since 19.0.1.712.
        refs = self.ref_model.search([("audit_log_id", "=", snapshot_log.id)])
        self.assertEqual(len(refs), 1, "Snapshot should have one parent ref and no direct ref.")
        parent_ref = refs.filtered(lambda r: not r.is_direct)
        self.assertEqual(len(parent_ref), 1, "Should find one parent reference.")
        self.assertEqual(parent_ref.target_model_name, self.parent_model._name)
        self.assertEqual(parent_ref.target_record_id, self.parent_record.id)

        # 3. Update the child record in a later transaction (per-field log)
        end_transaction(self.env)
        child_record.write({"name": "Test Child 1 Updated"})
        child_record.flush_recordset()

        update_log_entry = self.log_model.search(
            [
                ("model_name", "=", self.child_model._name),
                ("record_id", "=", child_record.id),
                ("change_type", "=", "u"),
                ("field_name", "=", "name"),
            ]
        )
        self.assertEqual(len(update_log_entry), 1, "Should find one update log for the child.")

        update_refs = self.ref_model.search([("audit_log_id", "=", update_log_entry.id)])
        self.assertEqual(len(update_refs), 1, "Update log should have one parent ref.")
        self.assertFalse(update_refs.is_direct)

        # 4. Aggregated view: each captured field of the snapshot insert is
        #    surfaced as its own virtual row (UNNESTed), plus the update for
        #    'name'. So the parent sees 'name' insert + 'parent_id' insert +
        #    'name' update as three child rows.
        aggregated_logs = self.aggregated_log_model.search(
            [
                ("model_name", "=", self.parent_model._name),
                ("record_id", "=", self.parent_record.id),
            ]
        )

        self.dump_aggregated_log(self.parent_record)

        child_logs_in_aggregated = aggregated_logs.filtered(lambda r: r.is_child_log)
        # No row has the sentinel field_name in the aggregated view — snapshot
        # rows are unnested so callers see real field names everywhere.
        self.assertFalse(
            child_logs_in_aggregated.filtered(lambda r: r.field_name == "__snapshot__"),
            "Aggregated view must not expose the __snapshot__ sentinel — it is unnested.",
        )

        # Insert side: one virtual row per captured field.
        insert_rows = child_logs_in_aggregated.filtered(lambda r: r.change_type == "i")
        insert_field_names = set(insert_rows.mapped("field_name"))
        self.assertIn("name", insert_field_names)
        self.assertIn("parent_id", insert_field_names)
        # All insert rows are flagged as child of the right record.
        for row in insert_rows:
            self.assertEqual(row.child_model_name, self.child_model._name)
            self.assertEqual(row.child_record_id, child_record.id)

        # The 'name' insert row carries the captured value in new_value.
        name_insert = insert_rows.filtered(lambda r: r.field_name == "name")
        self.assertEqual(len(name_insert), 1)
        self.assertEqual(name_insert.new_value, "Test Child 1")

        # Update side: one row for 'name'.
        update_logs = child_logs_in_aggregated.filtered(lambda r: r.change_type == "u")
        self.assertEqual(len(update_logs), 1)
        self.assertEqual(update_logs.field_name, "name")
        self.assertEqual(update_logs.old_value, "Test Child 1")
        self.assertEqual(update_logs.new_value, "Test Child 1 Updated")
