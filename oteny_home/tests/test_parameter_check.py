from odoo.exceptions import ValidationError
from odoo.tests import TransactionCase, tagged

FIRST_APPS = "oteny_home.first_apps"
HIDDEN_APPS = "oteny_home.hidden_apps"


@tagged("post_install", "-at_install")
class TestParameterCheck(TransactionCase):
    """A home screen parameter holds the xmlids of apps. Any other entry does
    nothing on the home screen, so it cannot be saved."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.params = cls.env["ir.config_parameter"].sudo()

    def _assert_refused(self, value, entry):
        for key in (FIRST_APPS, HIDDEN_APPS):
            with self.subTest(key=key), self.assertRaises(ValidationError) as caught:
                self.params.set_param(key, value)
            self.assertIn(key, str(caught.exception))
            self.assertIn(entry, str(caught.exception))

    def test_the_xmlids_of_apps_are_accepted(self):
        for key in (FIRST_APPS, HIDDEN_APPS):
            self.params.set_param(key, " base.menu_management , base.menu_tests,")
            self.assertEqual(self.params.get_param(key), " base.menu_management , base.menu_tests,")

    def test_an_entry_without_its_module_is_refused(self):
        self._assert_refused("base.menu_tests,menu_management", "menu_management")

    def test_an_unknown_xmlid_is_refused(self):
        self._assert_refused("base.menu_no_such_app", "base.menu_no_such_app")

    def test_a_child_menu_is_refused(self):
        self._assert_refused("base.menu_custom", "base.menu_custom")

    def test_a_record_that_is_not_a_menu_is_refused(self):
        self._assert_refused("base.main_company", "base.main_company")

    def test_a_new_parameter_is_checked_too(self):
        self.params.set_param(HIDDEN_APPS, False)
        with self.assertRaises(ValidationError):
            self.params.set_param(HIDDEN_APPS, "menu_management")

    def test_the_check_leaves_other_parameters_alone(self):
        self.params.set_param("oteny_home.test_other", "menu_management")
        self.assertEqual(self.params.get_param("oteny_home.test_other"), "menu_management")
