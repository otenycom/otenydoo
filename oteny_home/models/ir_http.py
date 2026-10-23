from odoo import models

# System parameters with menu xmlids, comma-separated: the apps that the home
# screen shows first, in this order, and the apps that it does not show.
FIRST_APPS_PARAM = "oteny_home.first_apps"
HIDDEN_APPS_PARAM = "oteny_home.hidden_apps"


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def session_info(self):
        """Send the order and the hidden apps of the home screen to the web client.

        The home screen shows the apps of ``oteny_home.first_apps`` first, in
        that order, then the other apps by name, and leaves out the apps of
        ``oteny_home.hidden_apps``. The order is the same for every user.
        """
        info = super().session_info()
        params = self.env["ir.config_parameter"].sudo()
        info["oteny_home"] = {
            "first_apps": _xmlids(params.get_param(FIRST_APPS_PARAM)),
            "hidden_apps": _xmlids(params.get_param(HIDDEN_APPS_PARAM)),
        }
        return info


def _xmlids(value):
    return [xmlid.strip() for xmlid in (value or "").split(",") if xmlid.strip()]
