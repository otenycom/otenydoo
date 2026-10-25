from odoo.tests import TransactionCase, tagged


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreMenu(TransactionCase):
    """The inspection menu sits in the section "Oteny" under Settings › Technical."""

    def test_menu_is_in_oteny_section(self):
        self.assertEqual(
            self.env.ref("oteny_cloud_filestore.menu_cloud_filestore_cache").parent_id,
            self.env.ref("oteny_base.menu_oteny"),
        )
