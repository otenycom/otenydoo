# test_audit_log_tombstone.py
# Coverage for Measure 3: insert and delete events are recorded as a single
# tombstone-style snapshot log row per record, with all non-default field
# values folded into the `snapshot` JSON. Updates remain per-field so that
# old/new transitions are individually queryable. Together this enables full
# record-state reconstruction from log + (still-existing related records)
# until log expiry.
from odoo.tests import TransactionCase, tagged


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditLogTombstone(TransactionCase):
    """Verify tombstone snapshot rows for inserts and deletes."""

    def setUp(self):
        super().setUp()
        self.parent_model = self.env["oteny.audit.test.parent"]
        self.child_model = self.env["oteny.audit.test.child"]
        self.log_model = self.env["oteny.audit.log"]
        self.aggregated_model = self.env["oteny.audit.log.aggregated"]

    def test_insert_emits_single_snapshot_row(self):
        """A create event is captured as exactly one snapshot row, not N per-field rows."""
        partner = self.env["res.partner"].create(
            {
                "name": "Tombstone insert probe",
                "email": "tombstone@example.com",
                "street": "Snapshot Street",
                "city": "Tombstoneville",
            }
        )

        insert_logs = self.log_model.search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner.id),
                ("change_type", "=", "i"),
            ]
        )
        # Exactly one insert row regardless of how many fields were populated.
        self.assertEqual(len(insert_logs), 1, "Insert event must produce exactly one snapshot row")
        self.assertEqual(insert_logs.field_name, "__snapshot__")

        snapshot = insert_logs.snapshot or {}
        # All four populated fields are present in the snapshot.
        for fname, expected in [
            ("name", "Tombstone insert probe"),
            ("email", "tombstone@example.com"),
            ("street", "Snapshot Street"),
            ("city", "Tombstoneville"),
        ]:
            self.assertIn(fname, snapshot, f"{fname} must be in the snapshot")
            self.assertEqual(snapshot[fname]["raw"], expected)

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
        """Insert snapshot + update transitions + delete snapshot enable full reconstruction.

        Replay invariant for fields that the create snapshot captured: the
        delete snapshot's value for a tracked field must equal the result of
        applying any subsequent updates to the create-snapshot value.

        Note: the delete snapshot is intentionally more inclusive than the
        create snapshot — it captures every non-blank field at delete time
        (even fields that equalled their default at create time and so were
        omitted from the create snapshot). Those default-valued fields are
        not part of the replay invariant for reconstruction; they are an
        additive completeness signal on the delete tombstone.
        """
        # 1. Create
        partner = self.env["res.partner"].create(
            {"name": "Replay probe", "email": "v1@example.com"}
        )
        # 2. Update
        partner.write({"email": "v2@example.com"})
        partner.flush_recordset()
        # 3. Update again
        partner.write({"name": "Replay probe v2", "email": "v3@example.com"})
        partner.flush_recordset()

        partner_id = partner.id

        # 4. Delete
        partner.unlink()

        all_logs = self.log_model.search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner_id),
            ],
            order="id ASC",
        )

        # Build state by replaying create snapshot + updates.
        state = {}
        tracked_fields = set()  # Fields that were in the insert snapshot or any update.
        delete_snapshot = None
        for log in all_logs:
            if log.change_type == "i" and log.field_name == "__snapshot__":
                state = {fname: entry["raw"] for fname, entry in (log.snapshot or {}).items()}
                tracked_fields.update(state.keys())
            elif log.change_type == "u":
                state[log.field_name] = log.new_value
                tracked_fields.add(log.field_name)
            elif log.change_type == "d" and log.field_name == "__snapshot__":
                delete_snapshot = {
                    fname: entry["raw"] for fname, entry in (log.snapshot or {}).items()
                }

        self.assertIsNotNone(delete_snapshot, "Delete must produce a snapshot row")

        # The replay invariant: every TRACKED field in the delete snapshot must
        # match the replayed state. Untracked fields (defaults at create time)
        # may also be in the delete snapshot but are not subject to replay.
        for fname in tracked_fields:
            if fname in delete_snapshot:
                self.assertEqual(
                    state.get(fname),
                    delete_snapshot[fname],
                    f"Replayed state for '{fname}' does not match delete snapshot",
                )

        # Sanity: the final values reflect the third write.
        self.assertEqual(state.get("name"), "Replay probe v2")
        self.assertEqual(state.get("email"), "v3@example.com")

    def test_snapshot_records_parent_refs(self):
        """The single snapshot row gets its parent ref, like per-field rows do."""
        parent = self.parent_model.create({"name": "Parent for ref test"})
        # Drop parent's own logs to focus on the child.
        self.log_model.search(
            [("model_name", "=", self.parent_model._name), ("record_id", "=", parent.id)]
        ).unlink()

        child = self.child_model.create({"name": "Child for ref test", "parent_id": parent.id})

        snapshot_log = self.log_model.search(
            [
                ("model_name", "=", self.child_model._name),
                ("record_id", "=", child.id),
                ("change_type", "=", "i"),
                ("field_name", "=", "__snapshot__"),
            ]
        )
        self.assertEqual(len(snapshot_log), 1)

        refs = self.env["oteny.audit.log.ref"].search([("audit_log_id", "=", snapshot_log.id)])
        # One parent ref; no direct ref since 19.0.1.712 (the view reads the log itself).
        self.assertEqual(len(refs), 1)
        self.assertFalse(refs.filtered(lambda r: r.is_direct))
        parent_ref = refs.filtered(lambda r: not r.is_direct)
        self.assertEqual(len(parent_ref), 1)
        self.assertEqual(parent_ref.target_model_name, self.parent_model._name)
        self.assertEqual(parent_ref.target_record_id, parent.id)

    def test_aggregated_caption_renders_snapshot_lines(self):
        """The aggregated view caption expands a snapshot into one line per captured field."""
        partner = self.env["res.partner"].create(
            {
                "name": "Caption probe",
                "email": "caption@example.com",
                "street": "Caption Street",
            }
        )

        # Find the aggregated row for this snapshot insert.
        agg = self.aggregated_model.search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner.id),
                ("change_type", "=", "i"),
            ]
        )
        self.assertTrue(agg, "Aggregated view should expose the snapshot row")
        # Caption should mention each captured field name (Name / Email / Street).
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
        work the same way they did before the tombstone shape was introduced.
        """
        partner = self.env["res.partner"].create(
            {
                "name": "Exploded probe",
                "email": "exploded@example.com",
                "street": "Exploded Street",
            }
        )
        # The underlying audit log holds ONE tombstone snapshot row.
        snapshot_logs = self.log_model.search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner.id),
                ("change_type", "=", "i"),
                ("field_name", "=", "__snapshot__"),
            ]
        )
        self.assertEqual(len(snapshot_logs), 1)

        # The aggregated view exposes one virtual row per captured field.
        agg = self.aggregated_model.search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner.id),
                ("change_type", "=", "i"),
            ]
        )
        agg_field_names = set(agg.mapped("field_name"))
        # Sentinel does not surface — only real field names do.
        self.assertNotIn("__snapshot__", agg_field_names)
        # Every captured field is a row.
        self.assertIn("name", agg_field_names)
        self.assertIn("email", agg_field_names)
        self.assertIn("street", agg_field_names)

        name_row = agg.filtered(lambda a: a.field_name == "name")
        self.assertEqual(len(name_row), 1)
        # For an insert, the captured value lands in new_value (old_value empty).
        self.assertEqual(name_row.new_value, "Exploded probe")
        self.assertEqual(name_row.old_value, "")
        self.assertEqual(name_row.new_value_display_name, "Exploded probe")

    def test_aggregated_view_filter_by_field_name_finds_snapshot_rows(self):
        """A `('field_name', '=', 'street')` filter must match snapshot-derived
        rows for inserts/deletes — the whole point of unnesting them in the view.
        """
        partner = self.env["res.partner"].create(
            {"name": "Filter probe", "street": "Filterable Street"}
        )
        partner_id = partner.id
        partner.unlink()

        # Filter by field_name should find both the insert AND the delete row
        # for "street", even though both came from snapshots.
        rows = self.aggregated_model.search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner_id),
                ("field_name", "=", "street"),
            ]
        )
        change_types = set(rows.mapped("change_type"))
        self.assertIn("i", change_types, "Snapshot insert street row must be findable by field_name")
        self.assertIn("d", change_types, "Snapshot delete street row must be findable by field_name")

    def test_aggregated_view_unique_ids_per_unnested_field(self):
        """Each unnested virtual row carries a unique id so the framework can
        treat them as independent records (form view navigation, selection).
        """
        partner = self.env["res.partner"].create(
            {
                "name": "Unique-id probe",
                "email": "unique@example.com",
                "street": "Unique Street",
                "city": "Uniqueville",
            }
        )
        agg = self.aggregated_model.search(
            [
                ("model_name", "=", "res.partner"),
                ("record_id", "=", partner.id),
                ("change_type", "=", "i"),
            ]
        )
        self.assertGreaterEqual(len(agg), 4, "Expected at least 4 unnested rows (name/email/street/city)")
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

        snapshot_log = self.log_model.search(
            [
                ("model_name", "=", "oteny.audit.test.html"),
                ("record_id", "=", record.id),
                ("change_type", "=", "i"),
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
