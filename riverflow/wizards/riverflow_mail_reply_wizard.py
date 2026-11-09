from odoo import _, api, fields, models, tools
from odoo.exceptions import UserError


class RiverflowMailReplyWizard(models.TransientModel):
    """The reply to an incoming e-mail, from the chatter (plan
    riverflow/plans/chatter-send-message.md, § 5 B).

    It is not a transition: the state and the deadline of the record stay as they are. It
    answers the sender and the known contacts in the To and Cc of the e-mail, posts the reply
    as an e-mail in the same thread, and marks the incoming e-mails up to the replied one as
    reviewed (decision 4)."""

    _name = "riverflow.mail.reply.wizard"
    _inherit = ["riverflow.mail.send.mixin"]
    _description = "Reply to an incoming e-mail"

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Many2oneReference(model_field="res_model", required=True, readonly=True)
    parent_message_id = fields.Many2one("mail.message", string="Reply to", required=True, readonly=True)
    parent_message_body = fields.Html(related="parent_message_id.body", string="E-mail")
    recipient_partner_ids = fields.Many2many(
        "res.partner",
        "riverflow_mail_reply_wizard_partner_rel",
        "wizard_id",
        "partner_id",
        string="To",
        compute="_compute_recipient_partner_ids",
        store=True,
        readonly=False,
    )
    subject = fields.Char(compute="_compute_subject", store=True, readonly=False)
    body = fields.Html(string="Message")
    attachment_ids = fields.Many2many(
        "ir.attachment",
        "riverflow_mail_reply_wizard_attachment_rel",
        "wizard_id",
        "attachment_id",
        string="Attachments",
    )

    @api.depends("parent_message_id")
    def _compute_recipient_partner_ids(self):
        for wizard in self:
            wizard.recipient_partner_ids = wizard._riverflow_reply_recipients(wizard.parent_message_id)

    @api.depends("parent_message_id")
    def _compute_subject(self):
        for wizard in self:
            message = wizard.parent_message_id
            subject = message.subject or message.record_name or ""
            if subject and not subject.lower().startswith("re:"):
                subject = _("Re: %s", subject)
            wizard.subject = subject

    def _riverflow_reply_recipients(self, message):
        """The sender and the known contacts in the To and Cc of the e-mail. Staff, team
        contacts and the writer are left out: a colleague gets a Log note with an @mention
        (decision 5), and the writer is never a recipient (decision 6)."""
        Partner = self.env["res.partner"]
        if not message:
            return Partner
        emails = tools.email_split((message.incoming_email_to or "") + "," + (message.incoming_email_cc or ""))
        normalized = {tools.email_normalize(email) for email in emails} - {False}
        known = Partner
        if normalized:
            for partner in Partner.search([("email_normalized", "in", list(normalized))], order="id"):
                if partner.email_normalized not in known.mapped("email_normalized"):
                    known |= partner
        candidates = message.author_id | known
        candidates = candidates.filtered(
            lambda partner: not partner.user_ids.filtered(lambda user: not user.share)
            and not partner.team_ids
        )
        return self._riverflow_without_writer(candidates)

    def action_send(self):
        self.ensure_one()
        record = self.env[self.res_model].browse(self.res_id)
        record.check_access("write")
        self._riverflow_check_email_recipients(self.recipient_partner_ids)
        if not self.body or tools.is_html_empty(self.body):
            raise UserError(_("Write the reply first."))

        uploads = self.attachment_ids.filtered(lambda a: a.res_model == self._name)
        uploads.write({"res_model": record._name, "res_id": record.id})
        record.with_context(
            mail_post_autofollow=True,
            mail_create_nosubscribe=False,
            email_notification_allow_footer=False,
        ).message_post(
            message_type="email",
            subtype_xmlid="mail.mt_comment",
            subject=self.subject,
            body=self.body,
            partner_ids=self.recipient_partner_ids.ids,
            parent_id=self.parent_message_id.id,
            attachment_ids=self.attachment_ids.ids,
        )
        self._riverflow_mark_reviewed_up_to(record, self.parent_message_id)
        return {"type": "ir.actions.act_window_close"}

    def _riverflow_mark_reviewed_up_to(self, record, message):
        """The reply shows that the writer read the incoming e-mails up to the replied one."""
        if "last_external_message_review_time" not in record._fields:
            return
        reviewed_until = record.last_external_message_review_time
        if not reviewed_until or reviewed_until < message.create_date:
            record.last_external_message_review_time = message.create_date
            record._bus_send_unreviewed_message_count()
