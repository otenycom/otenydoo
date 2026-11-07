# test_audit_log_ordering.py
from odoo.tests import TransactionCase
from odoo.tests import tagged

from .common import end_transaction


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditLogOrdering(TransactionCase):
    """Test that audit logs are created in the correct chronological order"""

    def setUp(self):
        super().setUp()
        self.parent_model = self.env["oteny.audit.test.parent"]
        self.child_model = self.env["oteny.audit.test.child"]
        self.log_model = self.env["oteny.audit.log"]
        self.ref_model = self.env["oteny.audit.log.ref"]
        self.aggregated_model = self.env["oteny.audit.log.aggregated"]

    def test_create_then_write_ordering(self):
        """Create then immediately write: a single insert snapshot precedes per-field updates."""
        parent = self.parent_model.create({"name": "Initial Name"})
        end_transaction(self.env)
        parent.write({"name": "Updated Name"})
        parent.flush_recordset()

        logs = self.log_model.search(
            [
                ("model_name", "=", self.parent_model._name),
                ("record_id", "=", parent.id),
            ],
            order="id ASC",
        )

        insert_logs = logs.filtered(lambda l: l.change_type == "i")
        update_logs = logs.filtered(lambda l: l.change_type == "u")

        # One tombstone snapshot for the create.
        self.assertEqual(len(insert_logs), 1, "Should have a single insert snapshot")
        self.assertEqual(insert_logs.field_name, "__snapshot__")
        snapshot = insert_logs.snapshot or {}
        self.assertEqual(snapshot.get("name", {}).get("raw"), "Initial Name")

        # Per-field update for the subsequent write to 'name'.
        name_updates = update_logs.filtered(lambda l: l.field_name == "name")
        self.assertEqual(len(name_updates), 1, "Should have one update log for 'name'")
        self.assertEqual(name_updates.old_value, "Initial Name")
        self.assertEqual(name_updates.new_value, "Updated Name")

        # The snapshot row precedes any update rows (id-ascending).
        self.assertLess(insert_logs.id, name_updates.id, "Insert snapshot must precede updates")

    def test_child_create_update_parent_ordering(self):
        """Child create + update: snapshot insert + per-field update, both visible in parent audit trail."""
        parent = self.parent_model.create({"name": "Parent Record"})
        # Clear parent's own creation logs to focus on child logs
        self.log_model.search(
            [
                ("model_name", "=", self.parent_model._name),
                ("record_id", "=", parent.id),
            ]
        ).unlink()

        child = self.child_model.create({"name": "Initial Child Name", "parent_id": parent.id})
        end_transaction(self.env)
        child.write({"name": "Updated Child Name"})
        child.flush_recordset()

        child_logs = self.log_model.search(
            [
                ("model_name", "=", self.child_model._name),
                ("record_id", "=", child.id),
            ],
            order="id ASC",
        )

        # One snapshot for the create, plus a per-field update for 'name'.
        snapshot_logs = child_logs.filtered(lambda l: l.field_name == "__snapshot__")
        self.assertEqual(len(snapshot_logs), 1, "Should have one tombstone snapshot for child create")
        self.assertEqual(snapshot_logs.change_type, "i")
        snapshot = snapshot_logs.snapshot or {}
        self.assertEqual(snapshot.get("name", {}).get("raw"), "Initial Child Name")
        self.assertIn("parent_id", snapshot, "parent_id must be in the create snapshot")

        name_update = child_logs.filtered(lambda l: l.field_name == "name" and l.change_type == "u")
        self.assertEqual(len(name_update), 1)
        self.assertEqual(name_update.old_value, "Initial Child Name")
        self.assertEqual(name_update.new_value, "Updated Child Name")

        # In parent's aggregated view, both the snapshot and update show up as child logs.
        parent_view_logs = self.aggregated_model.search(
            [
                ("model_name", "=", parent._name),
                ("record_id", "=", parent.id),
                ("is_child_log", "=", True),
            ],
        )
        child_change_types = set(parent_view_logs.mapped("change_type"))
        self.assertIn("i", child_change_types, "Parent should see the child insert (snapshot)")
        self.assertIn("u", child_change_types, "Parent should see the child update")

    def test_multiple_writes_before_flush(self):
        """Multiple writes without intermediate flush coalesce into a single update log."""
        parent = self.parent_model.create({"name": "Name 1"})
        end_transaction(self.env)
        parent.write({"name": "Name 2"})
        parent.write({"name": "Name 3"})
        parent.write({"name": "Name 4"})
        parent.flush_recordset()

        logs = self.log_model.search(
            [
                ("model_name", "=", self.parent_model._name),
                ("record_id", "=", parent.id),
            ],
            order="id ASC",
        )

        snapshot_logs = logs.filtered(lambda l: l.field_name == "__snapshot__")
        self.assertEqual(len(snapshot_logs), 1, "Should have a single insert snapshot")
        self.assertEqual(snapshot_logs.change_type, "i")
        self.assertEqual(snapshot_logs.snapshot.get("name", {}).get("raw"), "Name 1")

        name_updates = logs.filtered(lambda l: l.field_name == "name" and l.change_type == "u")
        self.assertEqual(len(name_updates), 1, "Multiple writes before flush coalesce to one update")
        self.assertEqual(name_updates.old_value, "Name 1")
        self.assertEqual(name_updates.new_value, "Name 4")

    def test_writes_with_intermediate_flushes(self):
        """Writes with intermediate flushes create one update log per flushed transition."""
        parent = self.parent_model.create({"name": "Name 1"})
        end_transaction(self.env)

        parent.write({"name": "Name 2"})
        parent.flush_recordset()
        parent.write({"name": "Name 3"})
        parent.flush_recordset()
        parent.write({"name": "Name 4"})
        parent.flush_recordset()

        all_logs = self.log_model.search(
            [
                ("model_name", "=", self.parent_model._name),
                ("record_id", "=", parent.id),
            ],
            order="id ASC",
        )

        # Insert snapshot first.
        snapshot_logs = all_logs.filtered(lambda l: l.field_name == "__snapshot__")
        self.assertEqual(len(snapshot_logs), 1)
        self.assertEqual(snapshot_logs.change_type, "i")
        self.assertEqual(snapshot_logs.snapshot.get("name", {}).get("raw"), "Name 1")

        # Three name updates, in order, each carrying its old/new transition.
        name_updates = all_logs.filtered(
            lambda l: l.field_name == "name" and l.change_type == "u"
        ).sorted(key=lambda l: l.id)
        self.assertEqual(len(name_updates), 3)
        self.assertEqual(name_updates[0].old_value, "Name 1")
        self.assertEqual(name_updates[0].new_value, "Name 2")
        self.assertEqual(name_updates[1].old_value, "Name 2")
        self.assertEqual(name_updates[1].new_value, "Name 3")
        self.assertEqual(name_updates[2].old_value, "Name 3")
        self.assertEqual(name_updates[2].new_value, "Name 4")

        # Snapshot precedes all updates.
        self.assertLess(snapshot_logs.id, name_updates[0].id)

    def test_computed_field_during_create_ordering(self):
        """Insert snapshot precedes any per-field update — even for late-arriving compute results.

        Background: previously, computed fields (e.g. company_id derived from
        employee_id) could write back during creation and produce 'u' rows
        that appeared before the 'i' rows. With Measure 3, the create event
        is a single tombstone snapshot row. Compute side-effects whose values
        only flush after the snapshot read are recorded as ordinary post-create
        update rows, never before the snapshot.
        """
        parent = self.parent_model.create({"name": "Test Name"})
        parent.flush_recordset()

        logs = self.log_model.search(
            [
                ("model_name", "=", self.parent_model._name),
                ("record_id", "=", parent.id),
            ],
            order="id ASC",
        )

        # The create event yields exactly one tombstone snapshot row.
        snapshot_logs = logs.filtered(
            lambda l: l.field_name == "__snapshot__" and l.change_type == "i"
        )
        self.assertEqual(len(snapshot_logs), 1, "Create should yield one tombstone snapshot row")
        self.assertEqual(snapshot_logs.snapshot.get("name", {}).get("raw"), "Test Name")

        # No 'u' row may precede the snapshot 'i' row.
        earliest_update = logs.filtered(lambda l: l.change_type == "u").sorted(key=lambda l: l.id)
        if earliest_update:
            self.assertGreater(
                earliest_update[0].id,
                snapshot_logs.id,
                "No update row may precede the tombstone snapshot for a create event",
            )

        # The compute result may surface either inside the snapshot (if the
        # ORM flushed it before patched_create's SQL read) or as a subsequent
        # 'u' row. Either way the value must be reachable from the audit log.
        computed_in_snapshot = (snapshot_logs.snapshot or {}).get("computed_field")
        computed_update = logs.filtered(
            lambda l: l.field_name == "computed_field"
            and l.change_type == "u"
            and l.new_value == "Computed: Test Name"
        )
        self.assertTrue(
            (computed_in_snapshot and computed_in_snapshot.get("raw") == "Computed: Test Name")
            or computed_update,
            "Computed field result must be captured either in snapshot or as a subsequent update",
        )

        # Now write to the record in a later transaction; subsequent changes are 'u'.
        end_transaction(self.env)
        parent.write({"name": "Updated After Creation"})
        parent.flush_recordset()

        all_logs_after_update = self.log_model.search(
            [
                ("model_name", "=", self.parent_model._name),
                ("record_id", "=", parent.id),
            ],
            order="id ASC",
        )

        name_update_logs = all_logs_after_update.filtered(
            lambda l: l.field_name == "name" and l.change_type == "u"
        )
        self.assertEqual(len(name_update_logs), 1)
        self.assertEqual(name_update_logs.old_value, "Test Name")
        self.assertEqual(name_update_logs.new_value, "Updated After Creation")

        # The cascading computed_field change is also logged as an update.
        computed_update_logs = all_logs_after_update.filtered(
            lambda l: l.field_name == "computed_field"
            and l.new_value == "Computed: Updated After Creation"
        )
        if computed_update_logs:
            self.assertEqual(computed_update_logs.change_type, "u")
