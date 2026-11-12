from odoo import _, models
from odoo.exceptions import UserError


class RiverflowMailSendMixin(models.AbstractModel):
    """The rules every riverflow e-mail screen applies to its recipients.

    The Send Email wizard of a transition and the reply wizard of the chatter share them, so
    a later change to how recipients are collected is made in one place (plan
    riverflow/plans/chatter-send-message.md, decisions 6 and 11).
    """

    _name = "riverflow.mail.send.mixin"
    _description = "Riverflow e-mail recipient rules"

    def _riverflow_writer_partner(self):
        return self.env.user.partner_id

    def _riverflow_without_writer(self, partners):
        """The writer is never a default recipient: Odoo does not e-mail the author of a
        message, so a "To" that holds only the writer reaches no one."""
        return partners - self._riverflow_writer_partner()

    def _riverflow_outside_contacts(self, partners):
        """The partners that are not staff: no internal user and no team contact. A colleague
        gets a Log note with an @mention, not an e-mail (decision 5)."""
        return partners.filtered(
            lambda partner: not partner.user_ids.filtered(lambda user: not user.share) and not partner.team_ids
        )

    def _riverflow_check_email_recipients(self, partners):
        """Refuse a send that reaches no one, a recipient without an e-mail address, and a
        recipient outside riverflow.restrict_email_recipients_to."""
        if not self._riverflow_without_writer(partners):
            raise UserError(_("Please select at least one recipient other than yourself."))

        recipients_without_email = partners.filtered(
            lambda partner: not partner.email or not partner.email.strip()
        )
        if recipients_without_email:
            raise UserError(
                _(
                    "The following recipients do not have email addresses: %s. "
                    "Please enter email addresses for these contacts.",
                    ", ".join(recipients_without_email.mapped("name")),
                )
            )

        allowed_domains = [
            domain.strip().lower()
            for domain in self.env["ir.config_parameter"]
            .sudo()
            .get_param("riverflow.restrict_email_recipients_to", "")
            .split(",")
            if domain.strip()
        ]
        if allowed_domains:
            invalid_recipients = partners.filtered(
                lambda partner: partner.email
                and not any(partner.email.lower().endswith(f"@{domain}") for domain in allowed_domains)
            )
            if invalid_recipients:
                raise UserError(
                    _(
                        "Email sending is restricted to specific domains (%(domains)s). "
                        "The following recipients have invalid email domains: %(recipients)s",
                        domains=", ".join(allowed_domains),
                        recipients=", ".join(invalid_recipients.mapped("email_formatted")),
                    )
                )
