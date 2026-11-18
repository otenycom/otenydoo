# test_audit_log_html_strip.py
# Coverage for Measure 2: a model can declare _oteny_audit_html_strip_fields
# to compress HTML payloads to plain text in the audit log. This applies to
# both raw values (old_value / new_value) and display values, on every change
# type (insert / update / delete).
from odoo.tests import TransactionCase, tagged


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditLogHtmlStrip(TransactionCase):
    """Verify HTML-stripping for fields listed in _oteny_audit_html_strip_fields."""

    def setUp(self):
        super().setUp()
        self.model = self.env["oteny.audit.test.html"]
        self.log_model = self.env["oteny.audit.log"]

    def _logs_for(self, record, field_name=None, change_type=None):
        domain = [
            ("model_name", "=", self.model._name),
            ("record_id", "=", record.id),
        ]
        if field_name is not None:
            domain.append(("field_name", "=", field_name))
        if change_type is not None:
            domain.append(("change_type", "=", change_type))
        return self.log_model.search(domain)

    def _snapshot_value(self, record, field_name):
        """Helper: delete `record` and return a field's entry of its delete snapshot. Since
        19.0.1.747 the delete snapshot is the only snapshot (an insert holds the id only)."""
        record_id = record.id
        record.unlink()
        self.env.flush_all()
        snap_logs = self.log_model.search([
            ("model_name", "=", self.model._name), ("record_id", "=", record_id),
            ("field_name", "=", "__snapshot__"), ("change_type", "=", "d"),
        ])
        self.assertEqual(len(snap_logs), 1, "Expected one delete snapshot row")
        return (snap_logs.snapshot or {}).get(field_name)

    def test_an_insert_holds_no_html(self):
        """Since 19.0.1.747 an insert row holds the record's id and no field value, so no
        HTML reaches it at all."""
        record = self.model.create(
            {
                "name": "Insert probe",
                "body": "<p>Hello <b>world</b></p><div>Second line</div>",
            }
        )
        record.flush_recordset()
        insert = self._logs_for(record, change_type="i")
        self.assertEqual((insert.field_name, insert.new_value, insert.snapshot), ("id", str(record.id), False))

    def test_html_field_stripped_on_update(self):
        """Update logs strip both old and new HTML values."""
        record = self.model.create(
            {
                "name": "Update probe",
                "body": "<p>Original <i>body</i></p>",
            }
        )
        # Drop creation logs to focus on the update.
        self._logs_for(record).unlink()

        record.body = "<p>Modified <b>body</b></p>"
        record.flush_recordset()

        body_update = self._logs_for(record, field_name="body", change_type="u")
        self.assertEqual(len(body_update), 1)
        self.assertNotIn("<", body_update.old_value)
        self.assertNotIn("<", body_update.new_value)
        self.assertIn("Original", body_update.old_value)
        self.assertIn("body", body_update.old_value)
        self.assertIn("Modified", body_update.new_value)

    def test_html_field_stripped_on_delete(self):
        """Delete tombstone strips the old HTML value of marked fields."""
        record = self.model.create(
            {
                "name": "Delete probe",
                "body": "<p>To be <b>deleted</b></p>",
            }
        )
        record_id = record.id
        record.unlink()

        delete_logs = self.log_model.search(
            [
                ("model_name", "=", self.model._name),
                ("record_id", "=", record_id),
                ("change_type", "=", "d"),
                ("field_name", "=", "__snapshot__"),
            ]
        )
        self.assertEqual(len(delete_logs), 1)
        body_entry = (delete_logs.snapshot or {}).get("body")
        self.assertIsNotNone(body_entry)
        self.assertNotIn("<", body_entry["raw"])
        self.assertIn("To be", body_entry["raw"])
        self.assertIn("deleted", body_entry["raw"])

    def test_non_stripped_html_field_preserves_markup(self):
        """An HTML field NOT listed for stripping retains its full markup.

        This proves the strip is per-field opt-in, not a global setting.
        """
        record = self.model.create(
            {
                "name": "Non-strip probe",
                "note": "<p>Keep <b>tags</b></p>",
            }
        )
        note_entry = self._snapshot_value(record, "note")
        self.assertIsNotNone(note_entry)
        self.assertIn("<p>", note_entry["raw"])
        self.assertIn("<b>", note_entry["raw"])

    def test_html_entities_decoded(self):
        """Common HTML entities are decoded so stripped text reads naturally."""
        record = self.model.create(
            {
                "name": "Entities probe",
                "body": "<p>Mr. Smith said &quot;hello&quot; &amp; goodbye&nbsp;to all.</p>",
            }
        )
        body_entry = self._snapshot_value(record, "body")
        self.assertIsNotNone(body_entry)
        # Entities decoded.
        self.assertIn('"hello"', body_entry["raw"])
        self.assertIn("&", body_entry["raw"])
        self.assertNotIn("&amp;", body_entry["raw"])
        self.assertNotIn("&quot;", body_entry["raw"])
        self.assertNotIn("&nbsp;", body_entry["raw"])

    def test_strip_collapses_whitespace(self):
        """Run-on whitespace from removed tags is collapsed to single spaces."""
        record = self.model.create(
            {
                "name": "Whitespace probe",
                "body": "<div>   <p>  Hello  </p>\n\n\n<p>  World  </p>   </div>",
            }
        )
        body_entry = self._snapshot_value(record, "body")
        self.assertIsNotNone(body_entry)
        # No multi-space runs.
        self.assertNotIn("  ", body_entry["raw"])
        self.assertEqual(body_entry["raw"], "Hello World")

    def test_strip_significantly_reduces_size(self):
        """An HTML-heavy chatter body shrinks meaningfully after stripping.

        Production sampling showed 89-95% reduction on real mail bodies. This
        fixture is similar in shape (Odoo chatter body with inline styles,
        CSS classes, anchors and an Odoo data attribute set), so we expect
        a comparable reduction here.
        """
        # Loosely mirrors a real Odoo chatter body: lots of style/class/data
        # attributes per element, plus a few anchors and an embedded image.
        big_html = (
            '<div style="margin-bottom:8px; padding:8px; background-color:#f8f9fa;'
            ' border-left:4px solid #2196F3; border-radius:4px;"'
            ' data-oe-version="2.0">'
            '<a href="https://erp.example.com/web#id=23986&amp;model=riverflow.service&amp;'
            'view_type=form" class="o_view_link" style="color:#2196F3;">Service link</a>'
            '<p style="margin:0px; padding:0px; font-size:13px" data-oe-version="2.0">'
            "Dear Mr. Sajol,</p>"
            '<p style="margin:0px; padding:0px; font-size:13px">&nbsp;</p>'
            '<p style="margin:0px; padding:0px; font-size:13px">'
            "Please note tomorrow the taxi will pick you up from "
            '<b style="font-weight:bold;">Suezmax</b> at 15:00 o clock and bring you to '
            '<a href="https://example.com/route" class="o_link" '
            'style="color:#2196F3;">Rotterdam station</a>.</p>'
            '<img src="https://example.com/logo.png" style="width:120px;height:40px;" '
            'alt="logo" data-oe-version="2.0"/>'
            '<p style="margin:0px;">Take the train as described in attachment ROUTE.</p>'
            "</div>"
        )
        record = self.model.create({"name": "Size probe", "body": big_html})

        body_entry = self._snapshot_value(record, "body")
        self.assertIsNotNone(body_entry)
        # Be conservative on the lower bound for synthetic fixtures: 50%+ is
        # plenty to prove the win; production bodies average ~90%.
        self.assertLess(
            len(body_entry["raw"]),
            len(big_html) * 0.5,
            f"Strip should remove at least 50% of an HTML-heavy body; "
            f"got {len(body_entry['raw'])} bytes vs original {len(big_html)} bytes",
        )
        self.assertIn("Rotterdam station", body_entry["raw"])
        self.assertIn("Suezmax", body_entry["raw"])
