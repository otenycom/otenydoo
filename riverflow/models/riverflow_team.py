from markupsafe import Markup

from odoo import fields, models, api


class RiverflowTeam(models.Model):
    _name = "riverflow.team"
    _inherits = {"res.partner": "partner_id"}
    _description = "Team"
    _order = "name"
    _inherit = ["mail.thread", "avatar.mixin"]

    # Link to the partner record that stores the team's contact information
    partner_id = fields.Many2one(
        "res.partner",
        required=True,
        ondelete="restrict",
        string="Related Partner",
        help="Partner record storing the team's contact information",
    )
    name = fields.Char(related="partner_id.name", inherited=True, readonly=False)

    # Team-specific fields
    discuss_channel_id = fields.Many2one(
        "discuss.channel",
        "Discuss Channel",
        required=False,
        help="This channel alerts the team of new unreviewed external messages, and receives a copy "
        "of every chatter message that a staff member of another team sends to a member of this team.",
    )
    # The staff users whose home team this is. The team form is the one place to manage who
    # belongs to which team (res.users.home_team_id is the stored side).
    member_ids = fields.One2many("res.users", "home_team_id", string="Members")

    @api.model_create_multi
    def create(self, vals_list):
        """Ensure partner is created as a company"""
        for vals in vals_list:
            vals["is_company"] = True
        return super().create(vals_list)

    def action_related_contact(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "res.partner",
            "res_id": self.partner_id.id,
            "view_mode": "form",
            "target": "main",
        }

    def _post_colleague_message_copy(self, message, recipients):
        """Post a copy of a staff member's chatter message in this team's Discuss channel.

        :param message: the mail.message on the record's chatter
        :param recipients: the res.partner recipients of the message that belong to this team

        The copy is a comment by the original sender, so it counts as unread for the channel
        members. It mentions nobody: every recipient already got the message itself, and with
        "Handle by Emails" a mention would email them a second time. A header links to the record,
        where people answer; attachments stay on the record.
        """
        self.ensure_one()
        record = self.env[message.model].browse(message.res_id)
        title = record.display_name
        if getattr(record, "res_name", False):
            # a service: show its subject (the log entry, employee or ship) next to its name
            title = f"{title} | {record.res_name}"
        header = Markup(
            '<div style="margin-bottom: 8px;">'
            "%(link)s%(subject)s<br/>%(to_label)s %(names)s</div>"
        ) % {
            "link": record._get_html_link(title=title),
            "subject": Markup("<br/><strong>%s</strong>") % message.subject if message.subject else "",
            "to_label": self.env._("To:"),
            "names": ", ".join(recipients.mapped("name")),
        }
        self.discuss_channel_id.sudo().with_context(mail_create_nosubscribe=True).message_post(
            body=header + (message.body or Markup()),
            author_id=message.author_id.id,
            message_type="comment",
            subtype_xmlid="mail.mt_comment",
        )
