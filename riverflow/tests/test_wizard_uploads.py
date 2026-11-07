"""Files uploaded in a wizard: the email sender wizard moves them to the service,
and a garbage collection deletes the ones that nothing uses (riverflow plan
wizard-attachments.md)."""

from datetime import timedelta

from odoo import Command, fields
from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools import SQL

EMAIL_WIZARD = "riverflow.service.email.sender.wizard"


@tagged("post_install", "-at_install", "riverflow", "test_wizard_uploads")
class TestWizardUploads(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        cls.workflow = cls.env["riverflow.workflow"].create({
            "model_id": service_model.id,
            "name": "Upload WF",
        })
        cls.state_open = cls.env["riverflow.state"].create({
            "workflow_id": cls.workflow.id,
            "name": "Open",
            "sequence": 10,
        })
        cls.state_sent = cls.env["riverflow.state"].create({
            "workflow_id": cls.workflow.id,
            "name": "Sent",
            "sequence": 20,
        })
        cls.template = cls.env["mail.template"].create({
            "name": "Upload Template",
            "model_id": service_model.id,
            "subject": "Upload subject",
            "body_html": "<p>Upload body</p>",
        })
        cls.template_file = cls.env["ir.attachment"].create({
            "name": "terms.txt",
            "raw": b"terms",
            "res_model": "mail.template",
            "res_id": cls.template.id,
        })
        cls.template.attachment_ids = [Command.set(cls.template_file.ids)]
        cls.transition = cls.env["riverflow.transition"].create({
            "name": "Send",
            "from_state_id": cls.state_open.id,
            "to_state_id": cls.state_sent.id,
            "action_id": cls.env.ref("riverflow.transition_action_email_sender").id,
            "mail_template_id": cls.template.id,
            "sequence": 10,
        })
        cls.writer = cls._make_user("upload_writer", "riverflow.group_service_writer")
        cls.reader = cls._make_user("upload_reader", "riverflow.group_service_reader")
        cls.recipient = cls.env["res.partner"].create({
            "name": "Upload Recipient",
            "email": "upload-recipient@example.com",
        })
        cls.service = cls.env["riverflow.service"].create({
            "name": "Upload service",
            "workflow_id": cls.workflow.id,
            "state_id": cls.state_open.id,
            "company_id": cls.env.company.id,
        })

    @classmethod
    def _make_user(cls, login, group_xmlid):
        return cls.env["res.users"].create({
            "name": login,
            "login": login,
            "email": f"{login}@example.com",
            "group_ids": [Command.set([
                cls.env.ref("base.group_user").id,
                cls.env.ref(group_xmlid).id,
            ])],
        })

    def _upload(self, res_model=EMAIL_WIZARD, age_days=2, **values):
        """An upload as the web client makes it in a wizard, made age_days ago."""
        attachment = self.env["ir.attachment"].create({
            "name": "upload.txt",
            "raw": b"upload",
            "res_model": res_model,
            "res_id": 0,
            **values,
        })
        # create_date and write_date are set by the ORM; only SQL can age them.
        attachment.flush_recordset()
        moment = fields.Datetime.now() - timedelta(days=age_days)
        self.env.cr.execute(SQL(
            "UPDATE ir_attachment SET create_date = %s, write_date = %s WHERE id = %s",
            moment, moment, attachment.id,
        ))
        attachment.invalidate_recordset(["create_date", "write_date"])
        return attachment

    # 4.1 ------------------------------------------------------------------

    def test_send_moves_the_upload_to_the_service(self):
        writer_env = self.env(user=self.writer)
        upload = writer_env["ir.attachment"].create({
            "name": "ticket.txt",
            "raw": b"ticket",
            "res_model": EMAIL_WIZARD,
            "res_id": 0,
        })
        service = self.service.with_user(self.writer)
        action = service.with_context(transition_id=self.transition.id).prepare_transition_action()
        Wizard = writer_env[action["res_model"]].with_context(**action["context"])
        wizard = Wizard.create(Wizard.default_get(list(Wizard._fields)))
        self.assertEqual(wizard.attachment_ids, self.template_file)
        wizard.recipient_partner_ids = self.recipient
        # The web client fills the message from the template through an onchange.
        wizard.subject_updatable = "Upload subject"
        wizard.body_updatable = "<p>Upload body</p>"
        wizard.attachment_ids = [Command.link(upload.id)]
        wizard.action_save()

        self.assertEqual(self.service.state_id, self.state_sent)
        self.assertEqual((upload.res_model, upload.res_id), ("riverflow.service", self.service.id))
        self.assertEqual(
            (self.template_file.res_model, self.template_file.res_id),
            ("mail.template", self.template.id),
            "the file of the mail template stays on the template",
        )
        # A user who can read the service but is no administrator reads the file.
        upload.with_user(self.reader).check_access("read")
        # The paperclip counter message_attachment_count counts by res_model and
        # res_id. It is not stored and was computed during the send, so assert
        # the search that it makes (testing-guidelines.md, non-stored compute).
        service_files = self.env["ir.attachment"].search([
            ("res_model", "=", "riverflow.service"),
            ("res_id", "=", self.service.id),
        ])
        self.assertEqual(service_files, upload)

    # 4.2 ------------------------------------------------------------------

    def test_gc_deletes_an_old_upload_that_nothing_uses(self):
        old = self._upload(age_days=2)
        young = self._upload(age_days=0)
        self.env["ir.attachment"]._gc_wizard_uploads()
        self.assertFalse(old.exists(), "an old upload that nothing uses is deleted")
        self.assertTrue(young.exists(), "an upload of a wizard that may still be open stays")

    def test_gc_keeps_an_upload_that_a_record_holds(self):
        on_message = self._upload()
        self.service.message_post(body="With a file", attachment_ids=on_message.ids)
        self.assertEqual(on_message.res_model, EMAIL_WIZARD, "message_post does not move it")
        on_template = self._upload()
        self.template.attachment_ids = [Command.link(on_template.id)]
        pasted_image = self._upload(access_token="pasted-image-token")
        not_a_wizard = self._upload(res_model="riverflow.service")

        self.env["ir.attachment"]._gc_wizard_uploads()

        self.assertTrue(on_message.exists(), "a chatter message holds it")
        self.assertTrue(on_template.exists(), "a mail template holds it")
        self.assertTrue(pasted_image.exists(), "a URL with its access token can be anywhere")
        self.assertTrue(not_a_wizard.exists(), "only uploads of a wizard are collected")

    def test_links_leave_out_the_tables_of_a_wizard(self):
        Attachment = self.env["ir.attachment"]
        transient = [name for name, model in self.env.registry.items() if model._transient]
        links = Attachment._wizard_upload_links(transient)
        self.assertIn(("message_attachment_rel", "attachment_id"), links)
        self.assertIn(("email_template_attachment_rel", "attachment_id"), links)
        tables = {table for table, _column in links}
        self.assertNotIn(
            "riverflow_service_mail_attachments_rel", tables,
            "the Many2many of the email sender wizard does not keep a file",
        )
        self.assertNotIn(
            "mail_compose_message_ir_attachments_rel", tables,
            "the Many2many of Odoo's mail composer does not keep a file",
        )
