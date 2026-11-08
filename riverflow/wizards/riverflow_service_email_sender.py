from odoo import api, fields, models, tools, _
from odoo.exceptions import UserError
from odoo.fields import Command
from datetime import datetime, date, timedelta
import logging
import re

_logger = logging.getLogger(__name__)


class RiverflowServiceEmailSenderWizard(models.TransientModel):
    _name = "riverflow.service.email.sender.wizard"
    _inherit = [
        "riverflow.service.wizard",
        "mail.render.mixin",
        "riverflow.mail.send.mixin",
    ]
    _description = "Riverflow Service Email Sender Wizard"

    recipient_partner_ids = fields.Many2many(
        "res.partner",
        string="Recipients",
        help="The default recipients for the email can be set by adding followers to the chatter",
    )

    subject = fields.Char(string="Subject")
    # A follow-up in an e-mail conversation keeps its subject when the user switches the
    # template, so the recipient's mail program keeps it in one conversation (plan
    # riverflow/plans/chatter-send-message.md, decision 20). The transition's own template
    # still fills the subject when the screen opens.
    has_email_conversation = fields.Boolean(readonly=True)
    subject_filled = fields.Boolean(help="Set once a template has filled the subject.")
    body = fields.Html(
        string="Body",
        sanitize=False,
        render_engine="qweb",
        render_options={"post_process": True},
    )
    mail_template_id = fields.Many2one(
        "mail.template",
        string="Template",
        domain="[('model_id', '=', 'riverflow.service')]",
        required=False,
    )

    # New fields for rendered content
    subject_rendered = fields.Char(
        string="Rendered Subject",
        compute="_compute_rendered_content",
        store=False,
    )
    body_rendered = fields.Html(
        string="Rendered Content",
        compute="_compute_rendered_content",
        store=False,
        sanitize=False,
    )
    recipients_from_template = fields.Many2many(
        "res.partner",
        string="Recipients from Template",
        help="The recipients specified in the e-mail template",
        compute="_compute_rendered_content",
    )

    # New editable fields that mirror the rendered content
    subject_updatable = fields.Char(
        string="Message Subject",
        compute="_compute_updatable_content",
        inverse="_inverse_subject_updatable",
        store=True,
    )
    body_updatable = fields.Html(
        string="Message Content",
        compute="_compute_updatable_content",
        inverse="_inverse_body_updatable",
        store=True,
        sanitize=False,
    )

    attachment_ids = fields.Many2many(
        "ir.attachment",
        "riverflow_service_mail_attachments_rel",
        "wizard_id",
        "attachment_id",
        string="Attachments",
        compute="_compute_attachment_ids",
        readonly=False,
        store=True,
    )

    show_attachment_warning = fields.Boolean(
        compute="_compute_show_attachment_warning",
        store=False,
    )

    is_supply_order = fields.Boolean(
        "Is Supply Order",
        help="If checked, the service is a supply order, e.g a Taxi Order",
        required=False,
    )

    supplier_partner_id = fields.Many2one(
        "res.partner",
        string="Supplier",
        help="The partner that is supplying the service",
        required=False,
    )

    supplier_email_formatted = fields.Char(
        "Supplier Email",
        compute="_compute_supplier_email_formatted",
        store=False,
    )

    deadline = fields.Date(
        "Supply Date",
        help="The date the service is expected to be supplied",
        required=False,
    )

    supply_order_instructions = fields.Text(
        "Instructions for the supplier",
        help="Instructions for the supply order",
        required=False,
    )

    def get_visibility_defaults(self, transition_id):
        # normally the responsible team is hidden for end-states, but here it is visible
        # because we use it as the sender of the email
        default_values = super().get_visibility_defaults(transition_id)
        default_values["responsible_team_id_invisible"] = False
        return default_values

    @api.model
    def default_get_using_records(self, defaultValues, records_to_transition):
        # Hide deadline by default on email forms — email sender sets its own
        # deadline (tomorrow) in update_write_values. When followup_in_days is
        # active, the base wizard's default_get overrides this to False so the
        # user sees and can adjust the follow-up deadline.
        defaultValues["project_deadline_invisible"] = True

        super().default_get_using_records(defaultValues, records_to_transition)

        # Get default recipients from the first service record
        if records_to_transition:
            service = records_to_transition[0]
            default_recipients = self._followers_as_recipients(service)
            defaultValues["recipient_partner_ids"] = [Command.set(default_recipients.ids)]
            defaultValues["subject_updatable"] = service.name
            defaultValues["has_email_conversation"] = service._riverflow_has_email_conversation()

        # Resolve the email template: transition-level overrides service-level.
        # This allows different email transitions in the same workflow to use
        # different templates (e.g., AB appointment request vs. pickup request).
        transition_id = self.env.context.get("transition_id")
        if transition_id:
            transition = self.env["riverflow.transition"].browse(transition_id)
            if transition.mail_template_id:
                defaultValues["mail_template_id"] = transition.mail_template_id.id
                return
        if records_to_transition:
            service = records_to_transition[0]
            if service.mail_template_id:
                defaultValues["mail_template_id"] = service.mail_template_id.id

    def _inverse_subject_updatable(self):
        # This method allows manual updates to subject_updatable to persist
        pass

    def _inverse_body_updatable(self):
        # This method allows manual updates to body_updatable to persist
        pass

    @api.depends("supplier_partner_id")
    def _compute_supplier_email_formatted(self):
        for wizard in self:
            wizard.supplier_email_formatted = wizard.supplier_partner_id.email_formatted

    @api.depends("mail_template_id")
    def _compute_attachment_ids(self):
        for wizard in self:
            if wizard.mail_template_id.attachment_ids:
                wizard.attachment_ids = wizard.mail_template_id.attachment_ids
            else:
                wizard.attachment_ids = False

    @api.depends("attachment_ids", "body_updatable")
    def _compute_show_attachment_warning(self):
        """Warn when the email body mentions attachments but none are added.
        The regex pattern is configurable via system parameter
        riverflow.email_attachment_warning_regex (case-insensitive).
        """
        pattern = (
            self.env["ir.config_parameter"]
            .sudo()
            .get_param(
                "riverflow.email_attachment_warning_regex",
                r"attach|enclos|herewith|bijlage|bijge|ingesloten|meegezonden|anhang|anbei|beigefüg|beilieg|anliegend",
            )
        )
        for wizard in self:
            if wizard.attachment_ids or not wizard.body_updatable or not pattern:
                wizard.show_attachment_warning = False
                continue
            plain_text = tools.html2plaintext(wizard.body_updatable)
            wizard.show_attachment_warning = bool(
                re.search(pattern, plain_text, re.IGNORECASE)
            )

    def render(self):
        self._compute_rendered_content()
        self._compute_updatable_content()

    @api.depends("subject", "body", "records_to_transition_ids", "mail_template_id")
    def _compute_rendered_content(self):
        for wizard in self:
            if not wizard.records_to_transition_ids:
                wizard.subject_rendered = wizard.subject
                wizard.body_rendered = wizard.body
                # Update editable fields when template changes
                wizard.subject_updatable = wizard.subject
                wizard.body_updatable = wizard.body
                continue

            service = wizard.records_to_transition_ids[0]
            render_context = wizard._get_render_context(service, {})

            # Render subject
            wizard.subject_rendered = wizard._render_template(
                wizard.subject or self.mail_template_id.subject,
                "riverflow.service",
                [service.id],
                engine="inline_template",
                add_context=render_context,
            )[service.id]

            # Render body
            wizard.body_rendered = wizard._render_template(
                wizard.body or self.mail_template_id.body_html,
                "riverflow.service",
                [service.id],
                engine="qweb",
                add_context=render_context,
                options={"post_process": True},
            )[service.id]

            wizard.recipients_from_template = self._render_recipients_from_template(service)

            # Update editable fields when template changes
            if wizard.subject_rendered:
                wizard.subject_updatable = wizard.subject_rendered
            if wizard.body_rendered:
                wizard.body_updatable = wizard.body_rendered

    @api.depends("subject_rendered", "body_rendered", "subject", "body")
    def _compute_updatable_content(self):
        for wizard in self:
            # Update if empty or if template fields changed
            if not wizard.subject_updatable or wizard.subject != wizard._origin.subject:
                wizard.subject_updatable = wizard.subject_rendered
            if not wizard.body_updatable or wizard.body != wizard._origin.body:
                wizard.body_updatable = wizard.body_rendered

    def _followers_as_recipients(self, service):
        """The service's followers as default recipients, unless the transition's
        action context has template_recipients_only: then only the template's own
        recipients get the e-mail (radar: the unpaid leave letter e-mails go to the
        employee alone, not to the planner who follows the service). The writer is
        never a default recipient (plan chatter-send-message.md, decision 6)."""
        if self.env.context.get("template_recipients_only"):
            return self.env["res.partner"]
        return self._riverflow_without_writer(service._get_default_recipients())

    def _render_recipients_from_template(self, service):
        if not self.mail_template_id:
            return self.env["res.partner"]

        service_id = service.id.origin if isinstance(service.id, api.NewId) else service.id

        partner_ids_map = self.mail_template_id._generate_template_recipients(
            res_ids=[service_id],
            render_fields=["partner_to", "email_cc", "email_to"],
            find_or_create_partners=True,
        )
        partner_ids = partner_ids_map.get(service_id, {}).get("partner_ids", [])
        return self.env["res.partner"].browse(partner_ids)

    @api.onchange("mail_template_id")
    def onchange_email_template_id(self):
        if self.mail_template_id:
            service = self.records_to_transition_ids[0] if self.records_to_transition_ids else None
            if service:
                default_recipients = self._followers_as_recipients(service)
                template_recipients = self._render_recipients_from_template(service)
                self.recipient_partner_ids = default_recipients | template_recipients

            keep_subject = self.has_email_conversation and self.subject_filled and self.subject_updatable
            self.subject = self.subject_updatable if keep_subject else self.mail_template_id.subject
            self.subject_filled = True
            self.body = self.mail_template_id.body_html
            self.render()

    @api.onchange(
        "deadline",
        "responsible_team_id",
        "supplier_partner_id",
        "is_supply_order",
        "supply_order_instructions",
    )
    def onchange_supply_fields(self):
        self.render()

    def _get_render_context(self, service, vals):
        res_id = vals.get("res_id") or service.res_id

        write_vals = {}
        self.update_write_values(service, write_vals)
        if write_vals:
            # HACK: writing to the record to make sure the template has the latest data
            service.write(write_vals)

        context = {
            "service": service,
            "company": service.company_id,
        }
        context.update(self._render_context_extra(service, res_id))
        return context

    def _render_context_extra(self, service, res_id):
        """Hook: the app's own template variables for this service's subject.

        Empty here. An app module extends it to expose the subject record of
        ``res_id`` under the name its templates use. riverflow names no such
        model.
        """
        return {}

    def update_write_values(self, service, vals):
        super(RiverflowServiceEmailSenderWizard, self).update_write_values(service, vals)

        subject = self.subject_updatable
        # keep_service_name (from transition action_context): preserve the original
        # service name instead of overwriting it with the email subject. Used by
        # workflows where the service represents a multi-step process and the email
        # is just one step (e.g. Work Permit: "Arrange Work Permit at AB" should
        # not be renamed to the German AB request email subject).
        if not self.env.context.get("keep_service_name"):
            vals["name"] = subject

        if self.responsible_team_id:
            vals["responsible_team_id"] = self.responsible_team_id.id

        if self.is_supply_order:
            # Write supplier reference to service
            vals["supplier_partner_id"] = self.supplier_partner_id.id
            vals["supply_order_instructions"] = self.supply_order_instructions

            vals["use_project_deadline_from"] = "self"
            vals["project_deadline"] = self.deadline
        else:
            vals["use_project_deadline_from"] = "self"
            # default deadline for the reply to a normal email is tomorrow
            vals["project_deadline"] = date.today() + timedelta(days=1)

        # Optional snooze override for chase / reminder transitions. Set via
        # transition action_context: {'snooze_deadline_days': N} pushes the
        # service deadline to today + N. An optional 'snooze_deadline_cap_field'
        # (dotted path on the service) caps the snooze at that field's value —
        # e.g. AUV reminders cap at log_entry_id.start_date so the deadline
        # never slips past the crew change. Missing/unresolvable cap paths
        # silently fall through — the snooze still applies without a cap.
        snooze_days = self.env.context.get("snooze_deadline_days")
        if snooze_days:
            new_deadline = date.today() + timedelta(days=snooze_days)
            cap_field = self.env.context.get("snooze_deadline_cap_field")
            if cap_field:
                try:
                    cap_values = service.mapped(cap_field)
                except (KeyError, AttributeError):
                    cap_values = []
                if cap_values and cap_values[0]:
                    new_deadline = min(new_deadline, cap_values[0])
            vals["project_deadline"] = new_deadline

        # TODO: add ship contact names to the service
        # vals["ship_contact_names"] = service.log_entry.ship_contact_names

        # for leg in service.leg_ids:
        #     # HACK: if we access leg.service_id, we get the old data without the UI updates, this line would workaround it.
        #     # leg.service_id = service
        #     leg.supply_from = "New From"  # leg.supply_from

    def create_related_records(self, service):
        super(RiverflowServiceEmailSenderWizard, self).create_related_records(service)
        self._send_email(service)

    def _send_email(self, service):
        recipient_ids = self.recipient_partner_ids

        if self.is_supply_order:
            recipient_ids = recipient_ids.union(self.supplier_partner_id)

        self._riverflow_check_email_recipients(recipient_ids)

        # Use updatable content for sending
        safe_body = tools.html_sanitize(self.body_updatable)

        if not self.subject_updatable:
            raise ValueError(_("Subject is required"))

        if not self.body_updatable:
            raise ValueError(_("Body is required"))

        # Post the message, add recipients as followers to the chatter, and subscribe current user to the chatter
        service.with_context(
            mail_post_autofollow=True,
            mail_create_nosubscribe=False,
            email_notification_allow_footer=False,  # No 'Sent by Odoo' footer
        ).message_post(
            message_type="email",
            subject=self.subject_updatable,
            partner_ids=recipient_ids.ids,
            body=safe_body,
            subtype_id=self.env.ref("mail.mt_comment").id,
            email_add_signature=False,  # Makes it the same as the Chatter Send Message, does not add the User's signature for templates
            email_layout_xmlid=self.mail_template_id.email_layout_xmlid,
            attachment_ids=self.attachment_ids.ids,
        )
        self._move_uploads_to_service(service)

    def _move_uploads_to_service(self, service):
        """Move the files uploaded in this wizard to the service.

        An upload gets res_model = this wizard and res_id = 0. Odoo's
        message_post moves such a file only from its own mail composer, so
        without this the file in the chatter does not count as an attachment
        of the service, and a user who is not an administrator cannot read it
        with their own rights. The attachments of the mail template, and a file
        that an app already moved, belong to another model and stay as they are.
        """
        uploads = self.attachment_ids.filtered(lambda attachment: attachment.res_model == self._name)
        uploads.write({"res_model": service._name, "res_id": service.id})
