# test_audit_purge.py
# The purge of the stored rows that the coverage of 19.0.1.723 would not write, and the
# aggregated view without direct references (plan oteny-audit/plans/audit-storage-reduction.md,
# cut F and § 11). The old rows are made with SQL, in the shape the old code wrote them.
import json

from odoo.tests import TransactionCase, tagged
from odoo.tools import SQL

from .common import end_transaction


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditPurge(TransactionCase):
    def setUp(self):
        super().setUp()
        self.log_model = self.env["oteny.audit.log"]
        self.ref_model = self.env["oteny.audit.log.ref"]
        self.aggregated = self.env["oteny.audit.log.aggregated"]

    def _old_row(self, model_name, record_id, field_name, change_type, txid, snapshot=None,
                 new_value="", label=None):
        self.env.cr.execute(SQL(
            """INSERT INTO oteny_audit_log
                   (model_name, record_id, record_display_name, field_name, field_display_name,
                    old_value, new_value, old_value_display_name, new_value_display_name,
                    change_type, snapshot, transaction_id, create_date, create_uid)
               VALUES (%s, %s, %s, %s, %s, '', %s, '', %s, %s, %s::jsonb, %s, now(), %s)
               RETURNING id""",
            model_name, record_id, f"Old {record_id}", field_name, label or field_name,
            new_value, new_value, change_type, json.dumps(snapshot) if snapshot is not None else None,
            txid, self.env.uid,
        ))
        log_id = self.env.cr.fetchone()[0]
        return log_id

    def _old_ref(self, log_id, model_name, record_id, is_direct):
        self.env.cr.execute(SQL(
            """INSERT INTO oteny_audit_log_ref
                   (audit_log_id, target_model_name, target_record_id, target_display_name, is_direct, create_date)
               VALUES (%s, %s, %s, 'Old', %s, now())""",
            log_id, model_name, record_id, is_direct,
        ))

    def _exists(self, log_id):
        self.env.cr.execute(SQL("SELECT snapshot FROM oteny_audit_log WHERE id = %s", log_id))
        row = self.env.cr.fetchone()
        return row is not None, row and row[0]

    def test_purge_removes_what_the_coverage_would_not_write(self):
        entry = {"raw": "x", "display": "x", "label": "X"}
        coverage = self.env["oteny.audit.test.coverage"].create({"name": "Purge target"})
        end_transaction(self.env)
        rid = coverage.id
        # Rows of an ignored model, of a removed model, and of an ignored field.
        ignored = self._old_row("ir.cron.trigger", 1, "__snapshot__", "i", 900, {"cron_id": entry})
        removed = self._old_row("documents.document", 1, "__snapshot__", "i", 901, {"name": entry})
        ignored_field = self._old_row("oteny.audit.test.coverage", rid, "skipped_note", "u", 902, new_value="n")
        # A per-record rule: an asset bundle.
        bundle = self._old_row("ir.attachment", 777001, "__snapshot__", "i", 903,
                               {"url": {"raw": "/web/assets/1/x.js", "display": "", "label": "Url"}})
        # An insert with an update in its transaction, a parent ref on the update, and a
        # direct ref; keys that the rules now leave out (N1, N2, an ignored field).
        insert = self._old_row("oteny.audit.test.coverage", rid, "__snapshot__", "i", 904, {
            "name": {"raw": "Old name", "display": "Old name", "label": "Name"},
            "count": {"raw": "0", "display": "0", "label": "Count"},
            "flag_on": {"raw": "False", "display": "False", "label": "Flag On"},
            "parent_path": {"raw": "1/", "display": "1/", "label": "Parent Path"},
            "skipped_note": entry,
        })
        self._old_ref(insert, "oteny.audit.test.coverage", rid, True)
        update = self._old_row("oteny.audit.test.coverage", rid, "name", "u", 904, new_value="Folded name", label="Name")
        self._old_ref(update, "res.partner", 1, False)
        later = self._old_row("oteny.audit.test.coverage", rid, "name", "u", 905, new_value="Later name", label="Name")
        self._old_ref(later, "oteny.audit.test.coverage", rid, True)

        self.log_model._purge_all_now()

        for gone in (ignored, removed, ignored_field, bundle, update):
            self.assertFalse(self._exists(gone)[0], gone)
        self.assertTrue(self._exists(later)[0], "an update in a later transaction stays")
        found, snapshot = self._exists(insert)
        self.assertTrue(found)
        self.assertEqual(snapshot["name"]["raw"], "Folded name", "the update of its transaction folds in")
        self.assertNotIn("count", snapshot, "N1: an empty value without a default leaves")
        self.assertEqual(snapshot["flag_on"]["raw"], "False", "an empty value with a default stays")
        self.assertNotIn("parent_path", snapshot)
        self.assertNotIn("skipped_note", snapshot)
        refs = self.ref_model.search([("audit_log_id", "in", [insert, later])])
        self.assertEqual(
            [(ref.audit_log_id.id, ref.target_model_name, ref.is_direct) for ref in refs],
            [(insert, "res.partner", False)],
            "the parent ref of the folded update moves to the insert; direct refs go",
        )

        # A second run changes nothing.
        self.env.cr.execute("SELECT count(*) FROM oteny_audit_log")
        count = self.env.cr.fetchone()[0]
        self.log_model._purge_all_now()
        self.env.cr.execute("SELECT count(*) FROM oteny_audit_log")
        self.assertEqual(self.env.cr.fetchone()[0], count)
        self.assertEqual(self._exists(insert)[1], snapshot)

    def test_view_reads_direct_rows_from_the_log_and_ignores_old_direct_refs(self):
        """Cut F: one view row per change, also while an old direct ref still exists."""
        parent = self.env["oteny.audit.test.parent"].create({"name": "View parent"})
        child = self.env["oteny.audit.test.child"].create({"name": "View child", "parent_id": parent.id})
        end_transaction(self.env)
        child.write({"name": "View child 2"})
        self.env.flush_all()
        update = self.log_model.search(
            [("model_name", "=", child._name), ("record_id", "=", child.id), ("change_type", "=", "u")]
        )
        self._old_ref(update.id, child._name, child.id, True)  # as the old code wrote it

        own = self.aggregated.search([("model_name", "=", child._name), ("record_id", "=", child.id)])
        self.assertEqual(len(own.filtered(lambda row: row.change_type == "u")), 1)
        self.assertFalse(own.filtered("is_child_log"))
        under_parent = self.aggregated.search(
            [("model_name", "=", parent._name), ("record_id", "=", parent.id), ("is_child_log", "=", True)]
        )
        self.assertEqual(set(under_parent.mapped("change_type")), {"i", "u"})
        self.assertEqual(set(under_parent.mapped("child_record_id")), {child.id})
        ids = (own | under_parent).ids
        self.assertEqual(len(ids), len(set(ids)))
        self.assertTrue(all(row_id % 2 == 0 for row_id in own.ids), "direct rows have even ids")
        self.assertTrue(all(row_id % 2 == 1 for row_id in under_parent.ids), "child rows have odd ids")

    def test_switch_off_leaves_a_running_cron_to_its_own_deactivate(self):
        """Odoo refuses a write on a cron that is running; inside a cron run the purge leaves
        the switch-off to `_commit_progress(deactivate=True)`."""
        cron = self.env.ref("oteny_audit.ir_cron_audit_log_purge")
        cron.active = True
        self.log_model.with_context(cron_id=cron.id)._purge_switch_off()
        self.assertTrue(cron.active, "no write on the running cron")
        self.log_model._purge_switch_off()
        self.assertFalse(cron.active, "outside a cron run the purge switches the action off")
