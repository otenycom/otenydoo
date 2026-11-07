from odoo.tests import TransactionCase, tagged


@tagged("post_install", "-at_install", "riverflow", "test_radar_new_menu")
class TestRadarNewMenu(TransactionCase):
    def test_the_radar_has_a_new_menu_with_new_service(self):
        """The Radar has one top menu "New"; its New Service opens the wizard of
        "New Service" in the Services app (Ries, 7-Nov-2026). App modules add
        their own items under it."""
        new = self.env.ref("riverflow.radar_menu_new")
        self.assertEqual(new.name, "New")
        self.assertEqual(new.parent_id, self.env.ref("riverflow.tracker_root"))
        service = self.env.ref("riverflow.radar_menu_new_service")
        self.assertEqual(service.parent_id, new)
        self.assertEqual(service.action, self.env.ref("riverflow.service_menu_new_service").action)
