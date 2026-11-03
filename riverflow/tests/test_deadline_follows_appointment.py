from datetime import date

from lxml import etree

from odoo.tests import tagged
from odoo.tests.common import TransactionCase

# The fields that the service form hides for a deadline that follows an
# appointment (plan riverflow/plans/deadline-follows-appointment.md).
HIDDEN_FIELDS = (
    "deadline",
    "daily_prio",
    "use_project_deadline_from",
    "days_relative_to_project",
    "weekend_deadline_rule",
)


@tagged("post_install", "-at_install", "riverflow", "test_deadline_follows_appointment")
class TestDeadlineFollowsAppointment(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Service = cls.env["riverflow.service"]
        cls.root = Service.create(
            {
                "name": "Appointment Root",
                "project_deadline": date(2026, 11, 26),
                "supply_actual_date": date(2026, 11, 26),
            }
        )
        cls.child = Service.create(
            {
                "name": "Arrange Transport",
                "parent_id": cls.root.id,
                "use_project_deadline_from": "root_appointment",
                "days_relative_to_project": -2,
            }
        )
        cls.root_child = Service.create(
            {
                "name": "Follow-up",
                "parent_id": cls.root.id,
                "use_project_deadline_from": "root",
            }
        )

    def test_flag_set_for_top_level_appointment(self):
        self.assertTrue(self.child.is_deadline_from_appointment)

    def test_flag_not_set_for_other_modes(self):
        self.assertFalse(self.root.is_deadline_from_appointment)
        self.assertFalse(self.root_child.is_deadline_from_appointment)

    def test_flag_follows_a_change_of_mode(self):
        self.child.use_project_deadline_from = "self"
        self.assertFalse(self.child.is_deadline_from_appointment)

    def test_deadline_still_follows_the_appointment(self):
        """Hiding changes nothing in the deadline: it moves with the appointment."""
        self.assertEqual(self.child.deadline, date(2026, 11, 24))
        self.root.supply_actual_date = date(2026, 12, 3)
        self.assertEqual(self.child.deadline, date(2026, 12, 1))

    def test_form_hides_the_deadline_fields_outside_debug_mode(self):
        """Without debug mode one node per field can show, and it hides while
        the deadline follows an appointment. Odoo keeps the debug twin of a
        user without debug mode in the form, with invisible="1"."""
        arch = self.env["riverflow.service"].get_view(
            self.env.ref("riverflow.view_service_form").id, "form"
        )["arch"]
        form = etree.fromstring(arch)
        for name in HIDDEN_FIELDS:
            # Leave out the embedded lists (child services), which show the deadline as a column.
            nodes = form.xpath(f"//sheet//field[@name='{name}'][not(ancestor::list)]")
            can_show = [node for node in nodes if node.get("invisible") != "1"]
            self.assertEqual(len(can_show), 1, f"{name}: the debug twin must stay hidden without debug mode")
            self.assertIn("is_deadline_from_appointment", can_show[0].get("invisible", ""), name)
            self.assertNotIn("not is_deadline_from_appointment", can_show[0].get("invisible", ""), name)
