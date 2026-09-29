from odoo import fields, models


class ResUsers(models.Model):
    _inherit = "res.users"

    # The one team a staff user belongs to for message routing: a colleague of another team who
    # addresses this user in a chatter message also posts a copy in this team's Discuss channel
    # (see mail_thread.py), so a request to one person does not stay in one mailbox.
    #
    # Named home_team_id, not riverflow_team_id: res.users reaches every res.partner field
    # through _inherits, and res.partner.riverflow_team_id already means "the team whose contact
    # this partner is". The same name here would shadow it with another meaning.
    #
    # ondelete="set null" does two jobs. Deleting a team clears the field, and removing a person
    # from the team's Members list (a One2many unlink) clears the field instead of deleting the
    # user: Odoo deletes a One2many line only when the inverse Many2one cascades.
    home_team_id = fields.Many2one(
        "riverflow.team",
        string="Home Team",
        ondelete="set null",
        help="The team this staff user belongs to. When a staff member of another team sends this "
        "user a chatter message, a copy is posted in this team's Discuss channel.",
    )
