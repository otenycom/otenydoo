import random
from lxml import etree, html
from odoo import api, fields, models, tools, _
from odoo.tools import html2plaintext, html_escape, is_html_empty
from markupsafe import Markup
from odoo.tools.mail import email_split_and_format
from odoo.addons.riverflow.util import is_neutralized_or_development  # type: ignore
import logging
import re

_logger = logging.getLogger(__name__)


class MailThreadReviewMixin(models.AbstractModel):
    _name = "riverflow.mail.thread.review.mixin"
    _description = "Mail Thread Review Mixin"
    _inherit = ["mail.thread", "riverflow.highlight.row.mixin"]

    # -------------------------------------------------------------------------
    # FIELDS
    # -------------------------------------------------------------------------

    responsible_team_id = fields.Many2one(
        "res.partner",
        string="Responsible",
        help="Team or user who is assigned to this record. This team/user is also responsible for reviewing external messages.",
        index=True,
        tracking=True,
        # domain="['|', ('is_user', '=', True), ('is_riverflow_team', '=', True)]",
        domain="[('is_riverflow_team', '=', True)]",
    )

    current_user_signature = fields.Html(
        string="Current User Signature",
        compute="_compute_current_user_signature",
        store=False,
    )

    internal_note_ids = fields.Many2many(
        comodel_name="mail.message",
        column1="res_id",
        column2="message_id",
        string="Internal Notes",
        compute="_compute_internal_note_ids",
        store=False,
    )

    external_message_ids = fields.Many2many(
        comodel_name="mail.message",
        column1="res_id",
        column2="message_id",
        string="External Messages",
        compute="_compute_external_message_ids",
        store=False,
    )

    unreviewed_message_ids = fields.Many2many(
        comodel_name="mail.message",
        column1="res_id",
        column2="message_id",
        string="Unreviewed Messages",
        compute="_compute_unreviewed_message_ids",
        store=False,
    )

    # field to store messages specifically from external senders, which
    # will trigger a needs review flag
    message_from_external_sender_ids = fields.Many2many(
        comodel_name="mail.message",
        column1="res_id",
        column2="message_id",
        string="Messages from External Senders",
        compute="_compute_message_from_external_sender_ids",
        store=False,
    )

    # todo: make this a JSON field and render the summaries properly, maybe with a custom widget and a popover
    internal_notes_summary = fields.Text(
        string="Top 3 Internal Notes",
        help="Enter internal notes via the 'Log note' button in the Chatter",
        compute="_compute_latest_internal_notes",
        inverse="_inverse_internal_notes_summary",
        store=True,
        tracking=False,
        index="trigram",
    )

    last_external_message_review_time = fields.Datetime(
        string="External Messages Reviewed",
        tracking=True,
    )

    external_message_count = fields.Integer(
        string="External Message Count",
        compute="_compute_external_message_count",
        store=True,
    )

    unreviewed_message_count = fields.Integer(
        string="Review",
        help="Count of inbound messages that have not yet been reviewed",
        compute="_compute_unreviewed_message_count",
        store=True,
    )

    external_messages_summary = fields.Text(
        string="Top 3 External Messages",
        help="Send external messages via the 'Send message' button in the Chatter",
        compute="_compute_external_messages_summary",
        store=True,
        index="trigram",
    )

    most_recent_attachment_id = fields.Many2one(
        comodel_name="ir.attachment",
        string="Most Recent Attachment",
        compute="_compute_most_recent_attachment_id",
    )

    # -------------------------------------------------------------------------
    # HELPER METHODS
    # -------------------------------------------------------------------------

    def _send_notification_to_team_channel(
        self, team, message_body, message_type="notification", email_from=None, author_name=None
    ):
        """Send a notification to a team's discuss channel.

        :param team: res.partner record representing the team
        :param message_body: HTML string with the notification content
        :param message_type: Type of message to send ("notification" or "email")
        :param email_from: Original sender's email address
        :param author_name: Original sender's name
        :return: True if successful, False otherwise
        """
        try:
            # Find the discuss channel for the team
            channel_id = False
            if team.is_riverflow_team:
                channel_id = team.riverflow_team_id.discuss_channel_id
            elif team.is_user:
                channel_id = self.sudo().env["discuss.channel"].channel_get(team.ids)

            if not channel_id:
                _logger.info("No discuss channel found for team %s", team.name)
                return False

            # Prepare message posting parameters
            post_values = {
                "body": Markup(message_body),
                "message_type": message_type,
                "subtype_xmlid": "mail.mt_comment",
            }

            # Use original sender info for external messages, system user for notifications
            if email_from and message_type == "email":
                # Parse the email_from to extract just the email address
                if "<" in email_from and ">" in email_from:
                    post_values["email_from"] = email_from
                else:
                    # email_from is just an email address
                    if author_name:
                        post_values["email_from"] = f"{author_name} <{email_from}>"
                    else:
                        post_values["email_from"] = email_from
            else:
                # Use current user as author instead of system user
                post_values["author_id"] = self.env.user.partner_id.id

            # Send the message to the team's discuss channel
            channel_id.sudo().with_context(mail_create_nosubscribe=True).message_post(**post_values)
            return True

        except Exception:
            _logger.exception(
                "Failed to send notification to team %s for %s:%s",
                team.name if team else "Unknown",
                self._name,
                self.id,
            )
            return False

    # -------------------------------------------------------------------------
    # OVERRIDES
    # -------------------------------------------------------------------------

    def write(self, vals):
        """Override write to send notification when responsible team changes"""
        # Store original values before the write for comparison
        old_teams = {record.id: record.responsible_team_id for record in self}

        # Perform the actual write
        result = super().write(vals)

        # Check if responsible_team_id was changed and send notifications.
        # skip_responsible_team_notification: the first assignment of a team
        # (the bounce review copies the parent's team onto a new service) is
        # not a handover. The handover notice posts into the team channel,
        # and that post can email the channel members.
        if "responsible_team_id" in vals and not self.env.context.get("skip_responsible_team_notification"):
            for record in self:
                old_team = old_teams.get(record.id)
                new_team = record.responsible_team_id

                # Only notify if team actually changed and there's a new team
                if old_team != new_team and new_team:
                    record._send_team_change_notification()

        return result

    def _send_team_change_notification(self):
        """Send notification to new team's discuss channel when responsibility is transferred"""
        self.ensure_one()

        # Construct the record URL and display text
        base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
        record_url = f"{base_url}/web#id={self.id}&model={self._name}&view_type=form"

        display_text = html_escape(self.display_name)
        if hasattr(self, "res_name") and self.res_name:
            # show parent record (entry) and the service below it
            display_text = f"{html_escape(self.res_name)}<br/>⤷ {display_text}"

        # Create the transfer notification message with standardized format
        team_name = self.responsible_team_id.name
        message_body = (
            f'<div class="o_mail_notification">'
            f'<div style="margin-bottom: 8px;">'
            f'<a href="{record_url}">{display_text}</a></div>'
            f'<div style="font-size: 0.9em;">'
            f"Assigned to: <strong>{html_escape(team_name)}</strong>"
            f"</div>"
            f'<div style="border-top: 1px solid #a0a0a088; margin-top: 8px; padding-top: 8px;"></div>'
            f"</div>"
        )

        # Send the notification
        self._send_notification_to_team_channel(self.responsible_team_id, message_body)

    # -------------------------------------------------------------------------
    # INVERSE METHODS
    # -------------------------------------------------------------------------

    def _inverse_internal_notes_summary(self):
        for record in self:
            if record.internal_notes_summary:
                plain_text = record.internal_notes_summary
                record.message_post(
                    body=plain_text,
                    message_type="comment",
                    subtype_xmlid="mail.mt_note",
                )

    def _compute_most_recent_attachment_id(self):
        for record in self:
            attachments = record.message_ids.attachment_ids.sorted(key=lambda a: a.create_date, reverse=True)
            if len(attachments) > 0:
                record.most_recent_attachment_id = attachments[0]
            else:
                record.most_recent_attachment_id = False

    def _compute_current_user_signature(self):
        for record in self:
            record.current_user_signature = record.env.user.signature

    def _get_filtered_messages(self, select_internal):
        """
        Helper function to get message IDs based on message type and subtype internal flag.

        :param select_internal: Boolean, True to select internal messages, False for external
        :return: Recordset of filtered mail.message
        """
        valid_message_types = ["email", "comment", "email_outgoing"]
        return self.message_ids.filtered(
            lambda m: m.message_type in valid_message_types and bool(m.subtype_id.internal) == select_internal
        )

    @api.depends("message_ids")
    def _compute_internal_note_ids(self):
        for record in self:
            record.internal_note_ids = record._get_filtered_messages(select_internal=True)

    @api.depends("message_ids")
    def _compute_external_message_ids(self):
        for record in self:
            record.external_message_ids = record._get_filtered_messages(select_internal=False)

    @tools.ormcache()
    def _get_system_user_id(self):
        return self.env.ref("base.user_root").id  # always id 1, login name __system__

    @api.depends("message_ids", "external_message_ids")
    def _compute_message_from_external_sender_ids(self):
        """
        Compute method for message_from_external_sender_ids.
        This field contains messages that have a sender who is not an internal user of the system.
        """

        def _is_from_external_sender(message):
            # Also tried this, but if a reply is made from an email address linked to a user,
            #  it will not be considered an external message, which is not helpful for testing
            # return not message.author_id or not any(
            #     user.has_group("base.group_user")
            #     for user in message.author_id.user_ids
            # )

            # the inbound message robot posts customer messages as the system user
            is_created_by_system_user = message.create_uid.id == self._get_system_user_id()
            return is_created_by_system_user

        for record in self:
            if False and is_neutralized_or_development():
                # _logger.warning(
                #     "Running in development or neutralized mode, skipping external sender filtering"
                # )
                record.message_from_external_sender_ids = record.external_message_ids
            else:
                # _logger.info(
                #     "Running in production mode, filtering external sender messages"
                # )
                messages_from_external_senders = self.env["mail.message"]
                for message in record.external_message_ids:
                    if _is_from_external_sender(message):
                        messages_from_external_senders |= message

                record.message_from_external_sender_ids = messages_from_external_senders

    def _format_message_for_summary(self, message):
        text = message.body or message.subject or ""

        # Handle Markup objects by stripping HTML tags
        if hasattr(text, "striptags"):
            if is_html_empty(text):
                text = ""
            else:
                text = html2plaintext(text).replace("\n", " ")
        if len(text) > 100:
            text = text[:97] + "..."
        return text

    def _compute_messages_summary(self, messages, field_name):
        """Helper method to compute message summaries for both internal notes and external messages"""
        # Sort messages by creation date, newest first
        sorted_messages = messages.sorted(key=lambda m: m.create_date, reverse=True)
        formatted_messages = [self._format_message_for_summary(message) for message in sorted_messages[:3]]
        if len(formatted_messages) > 0:
            summary = "\n".join(formatted_messages)
            self[field_name] = summary
            self._update_user_write_date()
        else:
            self[field_name] = False  # needed for Odoo search

    @api.depends("message_ids.body")
    def _compute_latest_internal_notes(self):
        for record in self:
            record._compute_messages_summary(record.internal_note_ids, "internal_notes_summary")

    @api.depends("message_ids.body")
    def _compute_external_messages_summary(self):
        for record in self:
            record._compute_messages_summary(record.external_message_ids, "external_messages_summary")

    @api.depends("message_ids", "last_external_message_review_time")
    def _compute_unreviewed_message_ids(self):
        for record in self:
            record.unreviewed_message_ids = record.message_from_external_sender_ids.filtered(
                lambda m: not record.last_external_message_review_time
                or m.create_date > record.last_external_message_review_time
            )

    @api.depends("message_ids", "external_message_ids")
    def _compute_external_message_count(self):
        for record in self:
            record.external_message_count = len(record.external_message_ids)

    @api.depends("unreviewed_message_ids")
    def _compute_unreviewed_message_count(self):
        """Compute the count of unreviewed messages and notify teams if needed"""
        for record in self:
            record.unreviewed_message_count = len(record.unreviewed_message_ids)

    def action_mark_external_messages_reviewed(self):
        self.write({"last_external_message_review_time": fields.Datetime.now()})

    def action_view_external_messages(self):
        action = {
            "type": "ir.actions.act_window",
            "name": "External Messages",
            "res_model": "mail.message",
            "view_mode": "list,form",
            "domain": [("id", "in", self.mapped("external_message_ids").ids)],
            "context": {
                "default_model": self._name,
                "default_res_id": self.ids[0] if self.ids else False,
            },
        }
        if len(self) == 1:
            action["context"]["default_res_id"] = self.id
        return action

    def _message_create(self, values_list):
        """Override to notify team's discuss channel about new external messages"""
        messages = super()._message_create(values_list)

        # Early return if no messages
        if not messages:
            return messages

        # Group messages by record to avoid multiple notifications
        messages_by_record = {}
        for message in messages:
            # Skip internal notes and system notifications. mail.mt_note is
            # internal, so a log note is not outside mail and must not be
            # copied into the team channel (that copy is posted as an email).
            if message.message_type not in ("email", "comment"):
                continue
            if message.subtype_id and message.subtype_id.internal:
                continue

            # Skip messages from internal users: not sure if hiding these is actually helpful,
            # the main thing is they should not cause an 'unread' flag on the channel to avoid
            if message.author_id and message.author_id.user_ids.filtered(
                lambda u: u.has_group("base.group_user")
            ):
                continue

            # Get the record this message belongs to
            try:
                record = self.browse(message.res_id).exists()
                if (
                    not record
                    # Check if record has a responsible team and if that team has a discuss channel
                    # Using hasattr() to safely check if fields exist before accessing them
                    or not hasattr(record, "responsible_team_id")
                    or not record.responsible_team_id  # .riverflow_team_id
                ):
                    continue
            except Exception:
                _logger.exception("Failed to process message for team notification")
                continue

            messages_by_record.setdefault(record, []).append(message)

        # Send notifications for each record with new messages
        for record, new_messages in messages_by_record.items():
            try:
                base_url = self.env["ir.config_parameter"].sudo().get_param("web.base.url")
                record_url = f"{base_url}/web#id={record.id}&model={record._name}&view_type=form"

                # Get display name and res_name (subject of the service) if available
                display_text = html_escape(record.display_name)
                if hasattr(record, "res_name") and record.res_name:
                    display_text = f"{display_text} | {html_escape(record.res_name)})"

                responsible_partner_id = record.sudo().responsible_team_id

                # Post each original message to the team discuss channel
                for msg in new_messages:
                    # Get original sender information
                    sender_email = msg.email_from
                    sender_name = msg.author_id.name if msg.author_id else None

                    # If no name from author_id, try to extract from email_from
                    if not sender_name and sender_email:
                        # Parse "Name <email@domain.com>" format
                        parsed_email = email_split_and_format(sender_email)
                        if parsed_email:
                            sender_name = parsed_email[0].split(" <")[0] if " <" in parsed_email[0] else None

                    # Create a simple header with link to the record
                    header = (
                        f'<div style="margin-bottom: 8px; padding: 8px; background-color: #f8f9fa; border-left: 3px solid #007bff;">'
                        f'<a href="{record_url}">{display_text}</a>'
                        f"</div>"
                    )

                    # Combine header with original message body
                    full_message = f"{header}{msg.body or msg.preview or 'No content'}"

                    # Post the original message to the team discuss channel with original sender info
                    record._send_notification_to_team_channel(
                        responsible_partner_id,
                        full_message,
                        "email",
                        email_from=sender_email,
                        author_name=sender_name,
                    )

            except Exception:
                _logger.exception(
                    "Failed to send team notification for messages on %s:%s",
                    record._name,
                    record.id,
                )

        return messages

    def _send_dummy_external_message(self):
        """Send a dummy external message for testing purposes.
        The message will appear as if it came from an external sender."""
        self.ensure_one()

        # Create message as system user to simulate external sender
        test_number = str(random.randint(10000, 99999))
        message = self.with_user(self._get_system_user_id()).message_post(
            body=f"External test message #{test_number}",
            subject=f"Test Message #{test_number}",
            message_type="email",
            subtype_xmlid="mail.mt_comment",  # Non-internal subtype
            email_from="external@example.com",
        )

        # # Force update counters
        # self.invalidate_recordset(
        #     [
        #         "external_message_ids",
        #         "message_from_external_sender_ids",
        #         "unreviewed_message_ids",
        #         "external_messages_summary",
        #     ]
        # )

        return message
