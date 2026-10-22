from odoo import models
from odoo.http import request

COLOR_SCHEMES = ("light", "dark")


class IrHttp(models.AbstractModel):
    _inherit = "ir.http"

    def color_scheme(self):
        """The colour scheme the web client is rendered in.

        A user who chose Light or Dark gets it. For System, the browser stores
        the scheme of the operating system in the ``color_scheme`` cookie (see
        the color_scheme service); without a cookie the page is light.
        """
        user = request.env.user
        if user and not user._is_public():
            user_scheme = user.res_users_settings_id.color_scheme
            if user_scheme in COLOR_SCHEMES:
                return user_scheme
            cookie_scheme = request.httprequest.cookies.get("color_scheme")
            if cookie_scheme in COLOR_SCHEMES:
                return cookie_scheme
        return super().color_scheme()
