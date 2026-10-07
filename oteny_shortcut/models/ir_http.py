from odoo import models


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        """Hand the Form Wide Toggle switch to the web client at page load.

        The form used to ask for the system parameter with an RPC on its first open
        and cache that request for the session. A user without the Settings right may
        not read system parameters, so the refusal was cached and every form failed for
        that user; and a cached request bound to the first form never settled when that
        form was destroyed while it ran. The session info is read once per page load
        on the server, as sudo, so the form needs no request at all (2026-10-07).
        """
        info = super().session_info()
        toggle = self.env["ir.config_parameter"].sudo().get_param("oteny_shortcut.form_wide_toggle", "True")
        info["oteny_form_wide_toggle"] = toggle != "False"
        return info
