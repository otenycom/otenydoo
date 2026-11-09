from odoo import _, api, fields, models


class Contact(models.Model):
    _inherit = "res.partner"

    team_ids = fields.One2many("riverflow.team", "partner_id", string="Teams")

    is_user = fields.Boolean(compute="_compute_is_user", store=True)
    is_riverflow_team = fields.Boolean(compute="_compute_is_riverflow_team", store=True)
    riverflow_team_id = fields.Many2one(
        "riverflow.team", string="Team", compute="_compute_riverflow_team_id", store=True
    )

    @api.depends("user_ids")
    def _compute_is_user(self):
        for record in self:
            record.is_user = len(record.user_ids.filtered(lambda u: u.has_group("base.group_user"))) > 0

    @api.depends("team_ids")
    def _compute_is_riverflow_team(self):
        for record in self:
            record.is_riverflow_team = len(record.team_ids) > 0

    @api.depends("team_ids")
    def _compute_riverflow_team_id(self):
        for record in self:
            record.riverflow_team_id = record.team_ids[0] if record.team_ids else False

    # ------------------------------------------------------------
    # Services on a top-level contact (plan riverflow/plans/chatter-send-message.md, § 5 D)
    # ------------------------------------------------------------

    service_ids = fields.One2many(
        "riverflow.service",
        compute="_compute_service_ids",
        string="Services",
        groups="riverflow.group_service_reader",
        help="The services of this contact, such as an e-mail sent with Send Email. A service "
        "of a person who belongs to a company goes on the company.",
    )
    service_count = fields.Integer(
        compute="_compute_service_ids", string="Services Count", groups="riverflow.group_service_reader"
    )

    def _compute_service_ids(self):
        Service = self.env["riverflow.service"]
        for partner in self:
            if not partner.id:
                partner.service_ids = Service
                partner.service_count = 0
                continue
            services = Service.search(
                [("res_model", "=", "res.partner"), ("res_id", "=", partner.id), ("active", "=", True)],
                order="id desc",
            )
            partner.service_ids = services
            partner.service_count = len(services)

    def _riverflow_chatter_email_mode(self):
        """A contact sends no ad-hoc e-mail from its own chatter (decision 12): Send Email
        makes a service on the top-level contact instead (decisions 14 and 15)."""
        return "send_email"

    def _riverflow_email_service_defaults(self):
        self.ensure_one()
        return dict(
            super()._riverflow_email_service_defaults(),
            default_res_model="res.partner",
            default_res_id=self.commercial_partner_id.id,
        )

    def action_add_service(self):
        """Add Service on the Services tab of a top-level contact."""
        self.ensure_one()
        return {
            "type": "ir.actions.act_window",
            "name": _("Add Service to: %s", self.display_name),
            "view_mode": "form",
            "res_model": "riverflow.start.service",
            "context": {
                "default_res_model": "res.partner",
                "default_res_id": self.commercial_partner_id.id,
                "default_use_project_deadline_from": "self",
            },
            "target": "new",
        }

