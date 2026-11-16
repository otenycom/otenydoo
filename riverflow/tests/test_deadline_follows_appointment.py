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
        cls.workflow = cls.env.ref("riverflow.workflow_service_task")
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
        cls.marker = Service.create(
            {
                "name": "Appointment",
                "parent_id": cls.root.id,
                "use_project_deadline_from": "root_appointment",
                "days_relative_to_project": 0,
            }
        )
        cls.root_child = Service.create(
            {
                "name": "Follow-up",
                "parent_id": cls.root.id,
                "use_project_deadline_from": "root",
            }
        )
        # A template tree: only the root carries is_this_a_template.
        cls.template_root = Service.create({"name": "Template Root", "is_this_a_template": True})
        cls.template_on_day = Service.create(
            {
                "name": "Template Appointment",
                "parent_id": cls.template_root.id,
                "workflow_id": cls.workflow.id,
                "use_project_deadline_from": "root_appointment",
                "days_relative_to_project": 0,
            }
        )
        cls.template_before = Service.create(
            {
                "name": "Template Arrange Transport",
                "parent_id": cls.template_root.id,
                "workflow_id": cls.workflow.id,
                "use_project_deadline_from": "root_appointment",
                "days_relative_to_project": -2,
            }
        )

    def _from_template(self, template, days, linked=True):
        """A child service of self.root cloned from the template, then set to days."""
        return self.env["riverflow.service"].create(
            {
                "name": template.name,
                "parent_id": self.root.id,
                "workflow_id": template.workflow_id.id,
                "template_service_id": template.id if linked else False,
                "use_project_deadline_from": "root_appointment",
                "days_relative_to_project": days,
            }
        )

    def test_flag_set_on_the_appointment_day(self):
        self.assertTrue(self.marker.is_deadline_on_appointment)
        self.assertTrue(self._from_template(self.template_on_day, 0).is_deadline_on_appointment)

    def test_flag_not_set_for_days_before_the_appointment(self):
        """Arrange Transport, two days before the appointment, keeps its deadline editable."""
        self.assertFalse(self.child.is_deadline_on_appointment)
        self.assertFalse(self._from_template(self.template_before, -2).is_deadline_on_appointment)

    def test_the_template_decides_when_the_service_is_set_to_day_0(self):
        """A user who sets the service to day 0 can still change it again: the
        template has days before the appointment."""
        self.assertFalse(self._from_template(self.template_before, 0).is_deadline_on_appointment)

    def test_a_service_without_template_link_finds_its_template_by_name(self):
        """Services cloned before template_service_id existed match the template
        of the same name and workflow in a template tree."""
        self.assertFalse(self._from_template(self.template_before, 0, linked=False).is_deadline_on_appointment)
        self.assertTrue(self._from_template(self.template_on_day, 0, linked=False).is_deadline_on_appointment)

    def test_a_template_on_another_mode_leaves_the_days_to_the_service(self):
        """A template due on the top-level deadline (day 0) does not hide a
        service that a state rule moved to 7 days before the appointment."""
        template = self.env["riverflow.service"].create(
            {
                "name": "Template Follow-up",
                "parent_id": self.template_root.id,
                "workflow_id": self.workflow.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": 0,
            }
        )
        self.assertFalse(self._from_template(template, -7).is_deadline_on_appointment)
        self.assertTrue(self._from_template(template, 0).is_deadline_on_appointment)

    def test_a_template_gives_its_own_days(self):
        self.assertTrue(self.template_on_day.is_deadline_on_appointment)
        self.assertFalse(self.template_before.is_deadline_on_appointment)

    def test_flag_not_set_for_other_modes(self):
        self.assertFalse(self.root.is_deadline_on_appointment)
        self.assertFalse(self.root_child.is_deadline_on_appointment)

    def test_flag_follows_a_change_of_mode(self):
        self.marker.use_project_deadline_from = "self"
        self.assertFalse(self.marker.is_deadline_on_appointment)

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
            self.assertIn("is_deadline_on_appointment", can_show[0].get("invisible", ""), name)
            self.assertNotIn("not is_deadline_on_appointment", can_show[0].get("invisible", ""), name)
