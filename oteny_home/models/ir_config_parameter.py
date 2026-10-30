from odoo import api, models
from odoo.exceptions import ValidationError

from .ir_http import FIRST_APPS_PARAM, HIDDEN_APPS_PARAM, _xmlids


class IrConfigParameter(models.Model):
    _inherit = "ir.config_parameter"

    @api.constrains("key", "value")
    def _check_home_screen_apps(self):
        """Refuse a home screen parameter with an entry that names no app.

        The home screen compares each entry with the xmlids of the top-level
        menus. Any other entry does nothing, and without this check the app
        stays where it was and nobody sees why.
        """
        for param in self.filtered(lambda p: p.key in (FIRST_APPS_PARAM, HIDDEN_APPS_PARAM)):
            not_apps = [xmlid for xmlid in _xmlids(param.value) if not self._is_app(xmlid)]
            if not_apps:
                raise ValidationError(self.env._(
                    "%(key)s: not an app: %(entries)s. Use the xmlid of a top-level menu "
                    "with its module, for example base.menu_management.",
                    key=param.key,
                    entries=", ".join(not_apps),
                ))

    def _is_app(self, xmlid):
        record = self.env.ref(xmlid, raise_if_not_found=False)
        return record is not None and record._name == "ir.ui.menu" and not record.parent_id
