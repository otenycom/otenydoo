from odoo.tests import HttpCase, new_test_user, tagged


@tagged("post_install", "-at_install")
class TestColorScheme(HttpCase):
    """The server renders the web client in the user's Theme, or for System in
    the scheme of the color_scheme cookie, and stores the scheme it rendered in
    that cookie."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.user = new_test_user(cls.env, login="theme_user", password="theme_user_pw")

    def _open_web_client(self, theme, cookie=None):
        self.user.color_scheme = theme
        self.authenticate("theme_user", "theme_user_pw")
        if cookie:
            self.opener.cookies.set("color_scheme", cookie)
        response = self.url_open("/odoo")
        self.assertEqual(response.status_code, 200)
        return response

    def assertRenderedIn(self, response, scheme):
        self.assertEqual(response.cookies.get("color_scheme"), scheme)
        self.assertEqual("web.assets_web_dark" in response.text, scheme == "dark")

    def test_dark_theme(self):
        self.assertRenderedIn(self._open_web_client("dark"), "dark")

    def test_light_theme_wins_over_a_dark_cookie(self):
        self.assertRenderedIn(self._open_web_client("light", cookie="dark"), "light")

    def test_system_follows_the_cookie(self):
        self.assertRenderedIn(self._open_web_client("system", cookie="dark"), "dark")

    def test_system_without_cookie_is_light(self):
        self.assertRenderedIn(self._open_web_client("system"), "light")

    def test_user_can_set_own_theme(self):
        # The Preferences dialog writes the field as the user.
        self.user.with_user(self.user).write({"color_scheme": "dark"})
        self.assertEqual(self.user.res_users_settings_id.color_scheme, "dark")
