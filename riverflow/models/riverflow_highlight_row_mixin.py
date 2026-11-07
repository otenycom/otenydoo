from odoo import api, fields, models
from datetime import timedelta


class RiverflowHighlightRowMixin(models.AbstractModel):
    _name = "riverflow.highlight.row.mixin"
    _description = """Used to highlight rows in a list view that have been recently modified by a user.

This helps the user to visually focus on a row after they have just added or updated it.
The highlighting is temporary and only lasts for 10 seconds."""
    # The audit log skips the time of the last user write: it changes with every user write
    # and repeats the time of the audit row itself (oteny_audit, cut I).
    _oteny_audit_ignore_fields = {"user_write_date"}

    user_write_date = fields.Datetime(
        "User Write Date",
        help="Timestamp of the last user-initiated modification (excludes automated computed field updates).",
        readonly=True,
    )

    highlight_row = fields.Boolean(
        "Highlight Row",
        compute="_compute_highlight_row",
        store=False,
    )

    highlight_row_type = fields.Selection(
        [
            ("info", "Info"),
            ("warning", "Warning"),
            ("success", "Success"),
            ("danger", "Danger"),
            ("muted", "Muted"),
            ("primary", "Primary"),
        ],
        "Highlight Row Type",
        compute="_compute_highlight_row_type",
        store=False,
    )

    @api.depends("user_write_date")
    def _compute_highlight_row(self):
        """Highlight the row if it was modified by a user in the last 10 seconds."""
        # The threshold for highlighting is 10 seconds ago from the current time in UTC.
        highlight_threshold = fields.Datetime.now() - timedelta(seconds=10)
        for record in self:
            if record.user_write_date:
                record.highlight_row = record.user_write_date > highlight_threshold
            else:
                record.highlight_row = False

    def _compute_highlight_row_type(self):
        """Set the default highlight color. This can be overridden in inheriting models."""
        for record in self:
            record.highlight_row_type = "info"

    def _update_user_write_date(self):
        """Call this from compute methods which reflect actual user changes."""
        self.user_write_date = self.env.cr.now()

    @api.model_create_multi
    def create(self, vals_list):
        """Override create to set user_write_date on record creation."""
        # Use a single transaction timestamp for all records in the batch for consistency.
        now = self.env.cr.now()
        for vals in vals_list:
            vals["user_write_date"] = now
        return super().create(vals_list)

    def write(self, vals):
        """
        Override write to update user_write_date only for user-driven changes.

        This prevents highlighting when the system updates compute-only fields.
        A 'user write' is one that contains at least one field that is not a
        'compute-only' field (a field with a compute method but no inverse).
        """
        if vals:
            is_user_write = any(
                not self._fields.get(fname) or not self._fields[fname].compute or self._fields[fname].inverse
                for fname in vals
                if fname != "user_write_date"
            )
            if is_user_write:
                vals["user_write_date"] = self.env.cr.now()
        return super().write(vals)
