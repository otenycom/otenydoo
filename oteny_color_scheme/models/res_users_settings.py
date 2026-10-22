from odoo import fields, models


class ResUsersSettings(models.Model):
    _inherit = "res.users.settings"

    color_scheme = fields.Selection(
        [("system", "System"), ("light", "Light"), ("dark", "Dark")],
        string="Theme",
        default="system",
        required=True,
        help="System follows the colour scheme of the operating system.",
    )
