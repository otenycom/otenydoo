from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestSessionInfo(HttpCase):
    """The session info sends the home screen's first and hidden apps, as lists
    of menu xmlids from the system parameters."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        new_test_user(cls.env, login="home_user", password="home_user_pw")

    def _home_screen_info(self):
        self.authenticate("home_user", "home_user_pw")
        return self.make_jsonrpc_request("/web/session/get_session_info")["oteny_home"]

    def test_the_parameters_become_lists_of_xmlids(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_home.first_apps", "base.menu_administration")
        params.set_param("oteny_home.hidden_apps", " base.menu_management , base.menu_tests,")
        self.assertEqual(self._home_screen_info(), {
            "first_apps": ["base.menu_administration"],
            "hidden_apps": ["base.menu_management", "base.menu_tests"],
        })

    def test_without_parameters_the_lists_are_empty(self):
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_home.first_apps", False)
        params.set_param("oteny_home.hidden_apps", False)
        self.assertEqual(self._home_screen_info(), {"first_apps": [], "hidden_apps": []})
