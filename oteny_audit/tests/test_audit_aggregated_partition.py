# test_audit_aggregated_partition.py
# Guards the UNION ALL split of the oteny_audit_log_aggregated VIEW.
#
# The view was rewritten from a single SELECT (LATERAL + CASE for every row)
# into a UNION ALL of two disjoint branches:
#   * Branch A (non-snapshot): plain log columns — the fast value-search path.
#   * Branch B (snapshot): LATERAL jsonb_each unnesting tombstone snapshots.
# These tests assert the split is row-for-row equivalent to the old view: the
# partition is exhaustive and disjoint, the '__snapshot__' sentinel only leaks
# for empty snapshots, and value searches find BOTH plain-update values
# (branch A) and snapshot-captured values (branch B).
from odoo.tests import TransactionCase, tagged

from .common import end_transaction


@tagged("oteny_audit", "post_install", "-at_install", "partition")
class TestAuditAggregatedPartition(TransactionCase):
    """Verify the aggregated-view UNION ALL split preserves semantics."""

    def setUp(self):
        super().setUp()
        self.parent_model = self.env["oteny.audit.test.parent"]
        self.log_model = self.env["oteny.audit.log"]
        self.ref_model = self.env["oteny.audit.log.ref"]
        self.aggregated_model = self.env["oteny.audit.log.aggregated"]

    def _expected_agg_count(self, model_name, record_ids):
        """Recompute the expected aggregated row count straight from the base
        tables: every log row of the records themselves (direct rows) and every
        parent ref pointing to them (child rows) yields len(snapshot) rows when
        its log is a non-empty snapshot tombstone, else exactly one row."""
        def rows_of(log):
            snap = log.snapshot or {}
            return len(snap) if log.field_name == "__snapshot__" and snap else 1

        own_logs = self.log_model.search(
            [("model_name", "=", model_name), ("record_id", "in", record_ids)]
        )
        child_refs = self.ref_model.search(
            [
                ("target_model_name", "=", model_name),
                ("target_record_id", "in", record_ids),
                ("is_direct", "=", False),
            ]
        )
        return sum(rows_of(log) for log in own_logs) + sum(rows_of(ref.audit_log_id) for ref in child_refs)

    def test_partition_exhaustive_and_disjoint(self):
        """Aggregated row count equals the sum, over every ref, of the snapshot
        key count (non-empty snapshot) or 1 (everything else) — proving the two
        branches together reproduce exactly the old view's row set."""
        # Branch B: multi-field snapshot insert (name + computed_field captured).
        p_multi = self.parent_model.create({"name": "Partition multi"})
        # Branch A: plain update(s) in a later transaction (name change also
        # recomputes computed_field).
        end_transaction(self.env)
        p_multi.name = "Partition multi v2"
        # Branch B: snapshot delete.
        p_del = self.parent_model.create({"name": "Partition del"})
        del_id = p_del.id
        p_del.unlink()
        # Branch A: an insert, which since 19.0.1.748 is one plain row with the id.
        p_empty = self.parent_model.create({})
        empty_id = p_empty.id

        # The view reads the physical audit tables, not the ORM cache, so the
        # audit rows written by the flush hook must be materialised first.
        self.env.flush_all()

        record_ids = [p_multi.id, del_id, empty_id]
        expected = self._expected_agg_count("oteny.audit.test.parent", record_ids)
        actual = self.aggregated_model.search_count(
            [
                ("model_name", "=", "oteny.audit.test.parent"),
                ("record_id", "in", record_ids),
            ]
        )
        self.assertEqual(
            actual,
            expected,
            "Aggregated row count must equal the per-ref expectation "
            "(snapshot key count for non-empty snapshots, else 1)",
        )

        rows = self.aggregated_model.search(
            [
                ("model_name", "=", "oteny.audit.test.parent"),
                ("record_id", "in", record_ids),
            ]
        )
        # ids stay collision-free across both branches.
        ids = rows.mapped("id")
        self.assertEqual(len(ids), len(set(ids)), "All aggregated row ids must be unique")

        # The '__snapshot__' sentinel never surfaces for these rows: the inserts are id
        # rows (branch A), and the non-empty delete snapshot expands to real field names.
        self.assertFalse(rows.filtered(lambda r: r.field_name == "__snapshot__"))
        empty_rows = rows.filtered(lambda r: r.record_id == empty_id)
        self.assertEqual(empty_rows.mapped("field_name"), ["id"])

    def test_value_search_matches_old_semantics(self):
        """A value search must find both a plain-update value (branch A, new value) and a
        snapshot-captured value (branch B, the old value of a delete snapshot)."""
        # Branch A: the new value lives directly on the update log row.
        p_plain = self.parent_model.create({"name": "Zphirae_Init"})
        end_transaction(self.env)
        p_plain.name = "Zphirae_Plainval_8471"
        # Branch B: the value is captured inside the delete snapshot.
        p_snap = self.parent_model.create({"name": "Zphirae_Snapval_9352"})
        snap_id = p_snap.id
        p_snap.unlink()

        self.env.flush_all()

        plain_rows = self.aggregated_model.search(
            [("new_value_display_name", "ilike", "Zphirae_Plainval_8471")]
        )
        self.assertTrue(
            plain_rows, "Plain-update value must be findable via new_value_display_name (branch A)"
        )
        self.assertIn(p_plain.id, plain_rows.mapped("record_id"))

        snap_rows = self.aggregated_model.search(
            [("old_value_display_name", "ilike", "Zphirae_Snapval_9352")]
        )
        self.assertTrue(
            snap_rows, "Snapshot-captured value must be findable via old_value_display_name (branch B)"
        )
        self.assertIn(snap_id, snap_rows.mapped("record_id"))

    def test_default_order_is_by_time_over_direct_and_child_rows(self):
        """The list sorts by time. A direct row takes its id from the log sequence and a child
        row from the reference sequence; an order by id put every child row above every direct
        row, whatever its time (log entry 8780 on the production backup of 18-Nov-2026)."""
        parent = self.parent_model.create({"name": "Order parent"})
        child = self.env["oteny.audit.test.child"].create({"name": "Order child", "parent_id": parent.id})
        end_transaction(self.env)
        # Push the reference sequence far above the log sequence, as on production.
        self.env.cr.execute("SELECT setval('oteny_audit_log_ref_id_seq', nextval('oteny_audit_log_ref_id_seq') + 1000000)")
        child.name = "Order child 2"  # a child row, older than the next change
        end_transaction(self.env)
        self.env.cr.execute(
            "UPDATE oteny_audit_log SET create_date = create_date - interval '1 hour' "
            "WHERE model_name = 'oteny.audit.test.child' AND record_id = %s",
            [child.id],
        )
        parent.name = "Order parent 2"  # a direct row, the newest
        self.env.flush_all()
        rows = self.aggregated_model.search(
            [("model_name", "=", parent._name), ("record_id", "=", parent.id)]
        )
        self.assertFalse(rows[0].is_child_log, "the newest change comes first")
        keys = [(row.create_date, row.log_id) for row in rows]
        self.assertEqual(keys, sorted(keys, reverse=True))

    def test_a_read_by_id_returns_the_rows_of_the_search(self):
        """A read by id is narrowed by the log row ids (`_search`); it must give the same
        rows and values as the search, for direct and child rows, plain and snapshot."""
        parent = self.parent_model.create({"name": "Read parent"})
        child = self.env["oteny.audit.test.child"].create({"name": "Read child", "parent_id": parent.id})
        end_transaction(self.env)
        child.name = "Read child 2"
        end_transaction(self.env)
        child.unlink()
        self.env.flush_all()
        domain = [("model_name", "=", parent._name), ("record_id", "=", parent.id)]
        fields = ["is_child_log", "field_name", "old_value", "new_value", "change_type", "audit_log_id"]
        searched = self.aggregated_model.search_read(domain, fields)
        self.assertTrue({row["is_child_log"] for row in searched} == {True, False})
        self.assertTrue(any(row["change_type"] == "d" for row in searched))
        self.env.invalidate_all()
        ids = [row["id"] for row in searched]
        read = self.aggregated_model.browse(ids).read(fields)
        self.assertEqual(read, searched)
