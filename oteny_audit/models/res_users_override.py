from odoo import models


class ResUsers(models.Model):
    """Audit-log integration for users (plan oteny-audit/plans/audit-storage-reduction.md,
    the exclusions of cut I)."""

    _inherit = "res.users"

    # Read-only fields that Odoo writes by itself: the OAuth token changes at every OAuth
    # login (auth_oauth), and the OdooBot onboarding state (mail_bot). A name that a module
    # does not install is simply never met.
    _oteny_audit_ignore_fields = {"oauth_access_token", "odoobot_state", "odoobot_failed"}
