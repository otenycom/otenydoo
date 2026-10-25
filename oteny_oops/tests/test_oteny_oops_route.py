from odoo.tests import HttpCase, JsonRpcException, new_test_user, tagged

from .test_oteny_oops_log import SERVER_TRACEBACK


@tagged("oteny_oops", "post_install", "-at_install")
class TestOtenyOopsRoute(HttpCase):
    """The error dialog sends its fault to /oteny_oops/log, which writes it to the
    Oops Log for an internal user and answers with the link of the record."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        new_test_user(cls.env, login="oops_internal", password="oops_internal_pw", groups="base.group_user")
        new_test_user(cls.env, login="oops_portal", password="oops_portal_pw", groups="base.group_portal")

    def _params(self, **values):
        return {
            "exception_name": "builtins.AttributeError",
            "message": "'bool' object has no attribute 'rstrip'",
            "traceback": SERVER_TRACEBACK,
            "error_type": "server",
            "model": "sample.note",
            "url": "http://localhost/odoo/notes/7",
            **values,
        }

    def test_internal_user_error_is_logged(self):
        self.authenticate("oops_internal", "oops_internal_pw")
        answer = self.make_jsonrpc_request("/oteny_oops/log", self._params())
        record = self.env["oteny.oops.log"].browse(answer["id"])
        self.assertEqual(answer["url"], f"/odoo/action-oteny_oops.action_oteny_oops_log/{record.id}")
        self.assertEqual(record.user_id.login, "oops_internal")
        self.assertEqual(record.company_id, record.user_id.company_id)
        self.assertEqual(record.url, "http://localhost/odoo/notes/7")
        self.assertEqual(record.model, "sample.note")
        self.assertEqual(record.traceback, SERVER_TRACEBACK)
        self.assertTrue(record.fingerprint)
        self.assertTrue(record.user_agent)

    def test_portal_user_is_refused(self):
        self.authenticate("oops_portal", "oops_portal_pw")
        with self.assertRaises(JsonRpcException) as caught:
            self.make_jsonrpc_request("/oteny_oops/log", self._params())
        self.assertEqual(str(caught.exception), "werkzeug.exceptions.Forbidden")
        self.assertFalse(self.env["oteny.oops.log"].search([]))

    def test_request_above_two_megabytes_is_refused(self):
        """A record holds at most 1 MiB; the route reads no request above 2 MiB."""
        self.authenticate("oops_internal", "oops_internal_pw")
        with self.assertRaises(JsonRpcException) as caught:
            self.make_jsonrpc_request("/oteny_oops/log", self._params(traceback="x" * (3 << 20)), timeout=60)
        self.assertEqual(str(caught.exception), "werkzeug.exceptions.RequestEntityTooLarge")
        self.assertFalse(self.env["oteny.oops.log"].search([]))
