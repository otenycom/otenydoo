from datetime import datetime

from odoo import fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_template_note_copy")
class TestTemplateNoteCopy(TransactionCase):
    """A new service gets a copy of each note of its template (radar pipeline
    applicants plan, decision 160).

    The copy kept the template note's author and date, so a service made today
    showed the note as written by its author a year ago, as if that person had
    worked on the new service. The copy is now dated at the copy, by OdooBot, and
    its first line says that it comes from the service template. The template
    note itself does not change.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.subject = cls.env["res.partner"].create({"name": "Template Note Subject"})
        cls.author = cls.env["res.partner"].create({"name": "Template Note Author"})
        cls.template = cls.env["riverflow.service"].create(
            {"name": "Template Note Template", "is_this_a_template": True}
        )
        cls.template_note = cls.env["mail.message"].create(
            {
                "model": "riverflow.service",
                "res_id": cls.template.id,
                "message_type": "comment",
                "subtype_id": cls.env.ref("mail.mt_note").id,
                "author_id": cls.author.id,
                "date": datetime(2025, 10, 6, 14, 31),
                "body": "<p>Book the appointment first.</p>",
            }
        )

    def test_copy_is_dated_at_the_copy_by_odoobot_and_names_its_source(self):
        before = fields.Datetime.now().replace(microsecond=0)
        service = (
            self.env["riverflow.service"]
            .with_context(default_res_model="res.partner", default_res_id=self.subject.id)
            ._create_service_member_from_template(self.template)
        )

        copy = service.message_ids.filtered(lambda m: "Book the appointment first." in (m.body or ""))
        self.assertEqual(len(copy), 1, "one copy of the template note")
        self.assertEqual(copy.author_id, self.env.ref("base.partner_root"), "OdooBot made the copy")
        self.assertGreaterEqual(copy.date, before, "dated at the copy, not at the template note")
        self.assertTrue(
            copy.body.startswith("<p><em>From the service template</em></p>"),
            "the first line says where the note comes from",
        )
        self.assertEqual(copy.message_type, "auto_comment", "kept out of the top internal notes")

        self.assertEqual(self.template_note.author_id, self.author, "the template note keeps its author")
        self.assertEqual(self.template_note.date, datetime(2025, 10, 6, 14, 31))
