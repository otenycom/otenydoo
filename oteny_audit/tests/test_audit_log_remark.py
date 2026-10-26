from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("oteny_audit", "post_install", "-at_install")
class TestAuditLogRemark(TransactionCase):
    """A remark (change type "r") is a line of text that says what a person or a
    process did, for example the transition a user ran. It is written like every
    other audit row, but it holds no field value."""

    def setUp(self):
        super().setUp()
        self.log_model = self.env["oteny.audit.log"]
        self.ref_model = self.env["oteny.audit.log.ref"]
        self.aggregated_model = self.env["oteny.audit.log.aggregated"]
        self.parent = self.env["oteny.audit.test.parent"].create({"name": "Remark Parent"})
        self.child = self.env["oteny.audit.test.child"].create({"name": "Remark Child", "parent_id": self.parent.id})

    def _remarks(self, record):
        return self.log_model.search(
            [("model_name", "=", record._name), ("record_id", "=", record.id), ("change_type", "=", "r")]
        )

    def _aggregated(self, record, domain=()):
        self.aggregated_model.invalidate_model()
        return self.aggregated_model.search(
            [("model_name", "=", record._name), ("record_id", "=", record.id), *domain], order="id desc"
        )

    def test_remark_row_holds_key_label_and_text(self):
        self.child.write({"name": "Renamed Child"})
        self.child.flush_recordset()

        self.log_model._log_remark(self.child, "transition", "Transition", "Received already")

        remark = self._remarks(self.child)
        self.assertEqual(len(remark), 1)
        self.assertEqual(remark.field_name, "transition")
        self.assertEqual(remark.field_display_name, "Transition")
        self.assertEqual(remark.new_value, "Received already")
        self.assertEqual(remark.new_value_display_name, "Received already")
        self.assertFalse(remark.old_value)
        self.assertFalse(remark.snapshot)
        self.assertEqual(remark.record_display_name, "Renamed Child")
        self.assertEqual(remark.create_uid, self.env.user)
        update = self.log_model.search(
            [("model_name", "=", self.child._name), ("record_id", "=", self.child.id), ("field_name", "=", "name")]
        )
        self.assertEqual(remark.transaction_id, update.transaction_id)

    def test_one_remark_per_record(self):
        other = self.env["oteny.audit.test.child"].create({"name": "Other Child", "parent_id": self.parent.id})

        self.log_model._log_remark(self.child | other, "process", "Process", "Done with parent")

        self.assertEqual(len(self._remarks(self.child)), 1)
        self.assertEqual(len(self._remarks(other)), 1)

    def test_remark_of_a_child_shows_in_the_parent_audit_log(self):
        self.log_model._log_remark(self.child, "transition", "Transition", "Upload")

        refs = self.ref_model.search([("audit_log_id", "=", self._remarks(self.child).id)])
        parent_ref = refs.filtered(lambda ref: not ref.is_direct)
        self.assertEqual(parent_ref.target_model_name, self.parent._name)
        self.assertEqual(parent_ref.target_record_id, self.parent.id)
        self.assertEqual(
            self._aggregated(self.parent, [("change_type", "=", "r")]).mapped("new_value_display_name"), ["Upload"]
        )

    def test_caption_has_its_own_remark_header(self):
        self.child.write({"name": "Renamed Child"})
        self.child.flush_recordset()
        self.log_model._log_remark(self.child, "transition", "Transition", "Received already")

        rows = self._aggregated(
            self.child, [("field_name", "in", ["transition", "name"]), ("change_type", "in", ["r", "u"])]
        )
        remark, update = rows
        self.assertEqual(remark.change_type, "r")
        self.assertIn("Remark ", remark.caption)
        self.assertIn("Transition: <b>Received already</b>", remark.caption)
        self.assertIn("Update ", update.caption, "The update after the remark starts its own header.")

    def test_remark_text_is_escaped_even_under_an_html_field_name(self):
        record = self.env["oteny.audit.test.html"].create({"name": "Html"})

        self.log_model._log_remark(record, "note", "Note", "<b>bold</b>")

        [remark] = self._aggregated(record, [("change_type", "=", "r")])
        self.assertIn("&lt;b&gt;bold&lt;/b&gt;", remark.caption)
        self.assertNotIn("oteny-audit-html-field", remark.caption)

    def test_remarks_filter_finds_the_remark(self):
        self.log_model._log_remark(self.child, "transition", "Transition", "Upload")

        self.aggregated_model.invalidate_model()
        found = self.aggregated_model.search([("change_type", "=", "r"), ("record_id", "=", self.child.id)])
        self.assertIn("Upload", found.mapped("new_value_display_name"))

    def test_no_remark_when_audit_is_ignored(self):
        self.log_model.with_context(oteny_audit_ignore=True)._log_remark(
            self.child, "transition", "Transition", "Upload"
        )
        self.assertFalse(self._remarks(self.child))

    def test_no_remark_for_an_ignored_model(self):
        settings = self.env["res.config.settings"].create({})

        self.log_model._log_remark(settings, "transition", "Transition", "Upload")

        self.assertFalse(self._remarks(settings))

    def test_no_remark_for_no_records(self):
        self.log_model._log_remark(self.child.browse(), "transition", "Transition", "Upload")
        self.assertFalse(self.log_model.search([("change_type", "=", "r")]))
