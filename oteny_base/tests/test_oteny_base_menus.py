from odoo.tests import TransactionCase, tagged


@tagged("oteny_base", "post_install", "-at_install")
class TestOtenyBaseMenus(TransactionCase):
    """The section "Oteny" sits under Settings › Technical, where the Oteny
    modules put their menus."""

    def test_oteny_section_is_under_technical(self):
        self.assertEqual(self.env.ref("oteny_base.menu_oteny").parent_id, self.env.ref("base.menu_custom"))
