from odoo.http import request, route

from odoo.addons.web.controllers import home as web_home


class Home(web_home.Home):
    @route()
    def web_client(self, s_action=None, **kw):
        # The cookie tells the browser which scheme this page was rendered in,
        # so the color_scheme service can reload when the user's choice differs.
        response = super().web_client(s_action, **kw)
        if response.status_code == 200:
            response.set_cookie("color_scheme", request.env["ir.http"].color_scheme())
        return response
