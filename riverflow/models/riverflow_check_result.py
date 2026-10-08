from odoo import fields, models, api


class CheckResult(models.Model):
    _name = "riverflow.check.result"
    _inherit = ["riverflow.tombstone.mixin"]
    _description = "Base Check Result"
    _oteny_audit_ignore = True

    name = fields.Char(string="Name", store=True)
    check_type = fields.Selection([], string="Check Type", required=True)
    severity = fields.Selection(
        [
            ("info", "Info"),
            ("warning", "Warning"),
            ("error", "Error"),
        ],
        string="Severity",
        required=True,
    )
    color = fields.Integer(string="Color", compute="_compute_color", store=False)

    state_record_id = fields.Many2one(
        comodel_name="riverflow.state.record",
        string="State Record",
        required=False,
        ondelete="cascade",
        compute="_compute_state_record_id",
        store=True,
    )

    # A check result can hang on a service (riverflow.service.check_result_ids). An app
    # module adds its own anchors (log entry, employee, ...) and routes those itself.
    service_id = fields.Many2one(
        comodel_name="riverflow.service",
        string="Service",
        ondelete="cascade",
        index=True,
    )

    @api.depends("service_id", "service_id.state_record_id")
    def _compute_state_record_id(self):
        """A result on a service shows on that service's Radar row.

        The Radar lists state records, and a check result reaches the Issues column and
        the "Has Issues" filter through its state record. Results on other anchors are
        routed by the module that adds the anchor.
        """
        for record in self:
            if record.service_id:
                record.state_record_id = record.service_id.state_record_id

    @api.depends("severity")
    def _compute_color(self):
        for record in self:
            if record.severity == "info":
                record.color = 4  # Blue
            elif record.severity == "warning":
                record.color = 3  # Yellow
            elif record.severity == "error":
                record.color = 1  # Red
            else:
                record.color = 0  # Gray (default)

    def compare(self, other):
        """
        Base compare method for check results.
        Compare basic fields common to all check results.
        """
        if isinstance(other, dict):
            return (
                self.name == other.get("name")
                and self.check_type == other.get("check_type")
                and self.severity == other.get("severity")
            )
        elif isinstance(other, type(self)):
            return (
                self.name == other.name
                and self.check_type == other.check_type
                and self.severity == other.severity
            )
        return False
