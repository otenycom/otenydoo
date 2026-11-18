# test_audit_log_tombstone.py
# Insert and delete events are one log row per record. Since 19.0.1.747 an insert row holds
# the record's id only (plan oteny-audit/plans/audit-insert-id-only.md); a delete row holds a
# tombstone snapshot of every non-blank field in its `snapshot` JSON. Updates remain per-field,
# with the old and the new value. Together with the current record this allows to rebuild the
# state of a record at any time until log expiry: a field's value at time T is the old value of
# its first update after T, else its current value (or the value in the delete snapshot).
from odoo.tests import TransactionCase, tagged

from .common import end_transaction


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditLogTombstone(TransactionCase):
    """Verify the id row of an insert and the tombstone snapshot row of a delete."""

    def setUp(self):
        super().setUp()
        self.parent_model = self.env["oteny.audit.test.parent"]
        self.child_model = self.env["oteny.audit.test.child"]
        self.log_model = self.env["oteny.audit.log"]
        self.aggregated_model = self.env["oteny.audit.log.aggregated"]

    def test_insert_emits_single_id_row(self):
        """A create event is exactly one row that holds the record's id, no field value."""
        partner = self.env["res.partner"].create(
            {"name": "Tombstone insert probe", "email": "insert@example.com", "street": "Insert Street"}
        )
        partner.flush_recordset()
        insert_logs = self.log_model.search(
            [("model_name", "=", "res.partner"), ("record_id", "=", partner.id), ("change_type", "=", "i")]
        )
        self.assertEqual(len(insert_logs), 1, "Insert event must produce exactly one row")
        self.assertEqual(
            (insert_logs.field_name, insert_logs.new_value, insert_logs.snapshot),
            ("id", str(partner.id), False),
        )
        self.assertEqual(insert_logs.record_display_name, partner.display_name)

    def test_delete_emits_single_snapshot_row_with_full_values(self):
        """A delete event is captured as one snapshot containing all non-default fields.

        The snapshot must be complete enough to reconstruct the deleted record
        from log alone (combined with any still-existing related records).
        """
        partner = self.env["res.partner"].create(
            {
                "name": "Tombstone delete probe",
                "email": "delete-tombstone@example.com",
                "street": "Delete Street",
            }
        )
        partner_id = partner.id
        partner.unlink()

        delete_logs = self.log_model.search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner_id),
                ("change_type", "=", "d"),
            ]
        )
        self.assertEqual(len(delete_logs), 1, "Delete event must produce exactly one snapshot row")
        self.assertEqual(delete_logs.field_name, "__snapshot__")

        snapshot = delete_logs.snapshot or {}
        # Every populated field at delete time is in the snapshot.
        self.assertIn("name", snapshot)
        self.assertEqual(snapshot["name"]["raw"], "Tombstone delete probe")
        self.assertIn("email", snapshot)
        self.assertEqual(snapshot["email"]["raw"], "delete-tombstone@example.com")
        self.assertIn("street", snapshot)
        self.assertEqual(snapshot["street"]["raw"], "Delete Street")

    def test_updates_remain_per_field(self):
        """Each updated field yields its own log row carrying the old->new transition.

        Per-field updates are required so that audit replay can reconstruct
        any past state by playing transitions forward from the insert snapshot.
        """
        partner = self.env["res.partner"].create({"name": "Update probe", "street": "Old"})
        # Drop the create snapshot so we focus on the update behavior.
        self.log_model.search(
            [("model_name", "=", "res.partner"), ("record_id", "=", partner.id)]
        ).unlink()

        partner.write({"name": "Update probe v2", "street": "New", "city": "Newtown"})
        partner.flush_recordset()

        update_logs = self.log_model.search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner.id),
                ("change_type", "=", "u"),
            ]
        )
        update_field_names = set(update_logs.mapped("field_name"))
        # Per-field rows for each changed field, NOT a snapshot.
        self.assertNotIn("__snapshot__", update_field_names)
        self.assertIn("name", update_field_names)
        self.assertIn("street", update_field_names)
        self.assertIn("city", update_field_names)

        name_update = update_logs.filtered(lambda l: l.field_name == "name")
        self.assertEqual(name_update.old_value, "Update probe")
        self.assertEqual(name_update.new_value, "Update probe v2")

    def test_reconstruct_state_from_log_replay(self):
        """The delete snapshot and the update rows rebuild the record at every time.

        Walk the update rows backwards from the delete snapshot: the value before an update is
        its old value. The state at the insert follows, without an insert snapshot.
        """
        partner = self.env["res.partner"].create(
            {"name": "Replay probe", "email": "v1@example.com", "street": "First Street"}
        )
        partner_id = partner.id
        end_transaction(self.env)
        partner.write({"email": "v2@example.com"})
        end_transaction(self.env)
        partner.write({"name": "Replay probe v2", "email": "v3@example.com"})
        end_transaction(self.env)
        partner.unlink()
        self.env.flush_all()

        logs = self.log_model.search(
            [("model_name", "=", "res.partner"), ("record_id", "=", partner_id)], order="id DESC"
        )
        delete = logs.filtered(lambda log: log.change_type == "d")
        self.assertEqual(len(delete), 1, "Delete must produce a snapshot row")
        state = {fname: entry["raw"] for fname, entry in (delete.snapshot or {}).items()}
        self.assertEqual((state["name"], state["email"]), ("Replay probe v2", "v3@example.com"))
        for log in logs.filtered(lambda log: log.change_type == "u"):
            state[log.field_name] = log.old_value
        self.assertEqual(
            (state["name"], state["email"], state["street"]),
            ("Replay probe", "v1@example.com", "First Street"),
            "the state at the insert",
        )

    def test_insert_row_records_parent_refs(self):
        """The insert row gets its parent ref, like per-field rows do."""
        parent = self.parent_model.create({"name": "Parent for ref test"})
        # Drop parent's own logs to focus on the child.
        self.log_model.search(
            [("model_name", "=", self.parent_model._name), ("record_id", "=", parent.id)]
        ).unlink()

        child = self.child_model.create({"name": "Child for ref test", "parent_id": parent.id})

        insert_log = self.log_model.search(
            [
                ("model_name", "=", self.child_model._name),
                ("record_id", "=", child.id),
                ("change_type", "=", "i"),
            ]
        )
        self.assertEqual(len(insert_log), 1)

        refs = self.env["oteny.audit.log.ref"].search([("audit_log_id", "=", insert_log.id)])
        # One parent ref; no direct ref since 19.0.1.723 (the view reads the log itself).
        self.assertEqual(len(refs), 1)
        self.assertFalse(refs.is_direct)
        self.assertEqual(refs.target_model_name, self.parent_model._name)
        self.assertEqual(refs.target_record_id, parent.id)

    def _deleted_partner(self, values):
        partner = self.env["res.partner"].create(values)
        end_transaction(self.env)
        partner_id = partner.id
        partner.unlink()
        self.env.flush_all()
        return partner_id

    def test_aggregated_caption_renders_snapshot_lines(self):
        """The aggregated view caption expands a delete snapshot into one line per field."""
        partner_id = self._deleted_partner(
            {"name": "Caption probe", "email": "caption@example.com", "street": "Caption Street"}
        )
        agg = self.aggregated_model.search(
            [("model_name", "=", "res.partner"), ("record_id", "=", partner_id), ("change_type", "=", "d")]
        )
        self.assertTrue(agg, "Aggregated view should expose the snapshot row")
        caption_text = " ".join(str(a.caption) for a in agg)
        self.assertIn("Name", caption_text)
        self.assertIn("Caption probe", caption_text)
        self.assertIn("Email", caption_text)
        self.assertIn("caption@example.com", caption_text)
        self.assertIn("Street", caption_text)
        self.assertIn("Caption Street", caption_text)

    def test_aggregated_view_unnests_snapshot_into_per_field_rows(self):
        """The aggregated view UNNESTs each snapshot into one virtual row per
        captured field, so list views, group-by-field, and field_name filters
        work on a delete as on per-field rows.
        """
        partner_id = self._deleted_partner(
            {"name": "Exploded probe", "email": "exploded@example.com", "street": "Exploded Street"}
        )
        agg = self.aggregated_model.search(
            [("model_name", "=", "res.partner"), ("record_id", "=", partner_id), ("change_type", "=", "d")]
        )
        agg_field_names = set(agg.mapped("field_name"))
        self.assertNotIn("__snapshot__", agg_field_names)
        self.assertTrue({"name", "email", "street"} <= agg_field_names)
        name_row = agg.filtered(lambda a: a.field_name == "name")
        self.assertEqual(len(name_row), 1)
        # For a delete, the captured value lands in old_value (new_value empty).
        self.assertEqual((name_row.old_value, name_row.new_value), ("Exploded probe", ""))
        self.assertEqual(name_row.old_value_display_name, "Exploded probe")

    def test_aggregated_view_filter_by_field_name_finds_snapshot_rows(self):
        """A `('field_name', '=', 'street')` filter matches the delete snapshot row and the
        update rows of that field; an insert row holds no field."""
        partner = self.env["res.partner"].create({"name": "Filter probe", "street": "Filterable Street"})
        end_transaction(self.env)
        partner.street = "Moved Street"
        end_transaction(self.env)
        partner_id = partner.id
        partner.unlink()
        self.env.flush_all()
        rows = self.aggregated_model.search(
            [("model_name", "=", "res.partner"), ("record_id", "=", partner_id), ("field_name", "=", "street")]
        )
        self.assertEqual(set(rows.mapped("change_type")), {"u", "d"})

    def test_aggregated_view_unique_ids_per_unnested_field(self):
        """Each unnested virtual row carries a unique id so the framework can
        treat them as independent records (form view navigation, selection).
        """
        partner_id = self._deleted_partner(
            {"name": "Unique-id probe", "email": "unique@example.com", "street": "Unique Street", "city": "Uniqueville"}
        )
        agg = self.aggregated_model.search([("model_name", "=", "res.partner"), ("record_id", "=", partner_id)])
        self.assertGreaterEqual(len(agg), 5, "the insert row and at least 4 unnested delete rows")
        ids = agg.mapped("id")
        self.assertEqual(len(ids), len(set(ids)), "All exploded row ids must be unique")

    def test_snapshot_with_html_strip_field(self):
        """HTML stripping (Measure 2) applies inside the snapshot for marked fields."""
        record = self.env["oteny.audit.test.html"].create(
            {
                "name": "Strip-in-snapshot probe",
                "body": "<p>Hello <b>from snapshot</b></p>",
                "note": "<p>Plain note</p>",
            }
        )
        end_transaction(self.env)
        record_id = record.id
        record.unlink()
        self.env.flush_all()
        snapshot_log = self.log_model.search(
            [
                ("model_name", "=", "oteny.audit.test.html"),
                ("record_id", "=", record_id),
                ("change_type", "=", "d"),
                ("field_name", "=", "__snapshot__"),
            ]
        )
        self.assertEqual(len(snapshot_log), 1)
        snapshot = snapshot_log.snapshot or {}

        # body is in the strip set: no tags remain.
        self.assertIn("body", snapshot)
        self.assertNotIn("<", snapshot["body"]["raw"])
        self.assertIn("Hello", snapshot["body"]["raw"])
        self.assertIn("from snapshot", snapshot["body"]["raw"])

        # note is NOT in the strip set: tags retained.
        self.assertIn("note", snapshot)
        self.assertIn("<p>", snapshot["note"]["raw"])
