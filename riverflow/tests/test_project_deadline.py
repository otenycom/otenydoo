from datetime import date

from odoo.tests import tagged
from odoo.tests.common import TransactionCase


@tagged("post_install", "-at_install", "riverflow", "test_project_deadline")
class TestProjectDeadlineRootMode(TransactionCase):
    """Tests for the 'root' use_project_deadline_from mode.

    These baseline tests cover existing behavior that was previously only
    tested implicitly via service tree tests. They ensure the root mode
    compute chain, options, and display helpers work correctly.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = cls.env["riverflow.service"].create(
            {
                "name": "PD Root",
                "project_deadline": date(2026, 6, 15),
            }
        )
        cls.child = cls.env["riverflow.service"].create(
            {
                "name": "PD Child",
                "parent_id": cls.root.id,
                "use_project_deadline_from": "root",
                "days_relative_to_project": -3,
            }
        )

    def test_root_mode_recomputes_child_on_root_deadline_change(self):
        """Changing root's project_deadline recomputes child deadline via root mode."""
        self.assertEqual(self.child.deadline, date(2026, 6, 12))
        self.root.project_deadline = date(2026, 7, 10)
        self.assertEqual(
            self.child.deadline,
            date(2026, 7, 7),
            "Child deadline should follow root's new deadline minus 3 days",
        )

    def test_root_mode_options_available_with_parent(self):
        """'root' appears in deadline-from options when service has a parent."""
        options = self.env["riverflow.service"].calculate_use_project_deadline_from_options_for_new_service(
            parent_id=self.root,
            res_model="riverflow.service",
            res_id=0,
            is_root_a_template=False,
        )
        self.assertIn("root", options)

    def test_root_mode_options_not_available_without_parent(self):
        """'root' is absent for root-level services without a parent."""
        options = self.env["riverflow.service"].calculate_use_project_deadline_from_options_for_new_service(
            parent_id=False,
            res_model="riverflow.service",
            res_id=0,
            is_root_a_template=False,
        )
        self.assertNotIn("root", options)

    def test_root_mode_is_days_relative_applicable(self):
        """is_days_relative_to_project_applicable is True for child, False for root-is-self."""
        self.assertTrue(
            self.child.is_days_relative_to_project_applicable,
            "Child in root mode should have relative days applicable",
        )
        # Root service defaults to 'self' mode — relative days not applicable
        self.assertFalse(
            self.root.is_days_relative_to_project_applicable,
            "Root in self mode should not have relative days applicable",
        )

    def test_root_mode_prefix(self):
        """relative_to_project_days_prefix returns 'Top-level service' for root mode."""
        self.assertEqual(self.child.relative_to_project_days_prefix(), "Top-level service")

    def test_prefix_falls_back_to_selection_label(self):
        """Modes without a bespoke short prefix (e.g. 'creation') fall back to
        the label from the selection definition, so the timing widget never
        shows an '(unknown: use_project_deadline_from)' warning."""
        self.child.use_project_deadline_from = "creation"
        self.assertEqual(self.child.relative_to_project_days_prefix(), "Creation Date")


@tagged("post_install", "-at_install", "riverflow", "test_project_deadline")
class TestProjectDeadlineRootAppointmentMode(TransactionCase):
    """Tests for the 'root_appointment' use_project_deadline_from mode.

    Root appointment mode derives deadline from the root service's
    supply_actual_date (appointment date), falling back to the root's
    deadline when no appointment date is set. This keeps child service
    deadlines stable even as the root's deadline changes during workflow
    progression (e.g. followup_in_days, set_deadline_to_today).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.root = cls.env["riverflow.service"].create(
            {
                "name": "PD Appt Root",
                "project_deadline": date(2026, 6, 15),
                "supply_actual_date": date(2026, 7, 20),
            }
        )
        cls.child = cls.env["riverflow.service"].create(
            {
                "name": "PD Appt Child",
                "parent_id": cls.root.id,
                "use_project_deadline_from": "root_appointment",
                "days_relative_to_project": 0,
            }
        )

    def test_root_appointment_uses_supply_actual_date(self):
        """Child deadline comes from root's supply_actual_date, not root's deadline."""
        self.assertEqual(
            self.child.deadline,
            date(2026, 7, 20),
            "Child should use root's appointment date (supply_actual_date), not root's deadline",
        )

    def test_root_appointment_falls_back_to_deadline(self):
        """When root has no supply_actual_date, child falls back to root's deadline."""
        root = self.env["riverflow.service"].create(
            {
                "name": "PD Appt Root No Date",
                "project_deadline": date(2026, 8, 1),
            }
        )
        child = self.env["riverflow.service"].create(
            {
                "name": "PD Appt Child Fallback",
                "parent_id": root.id,
                "use_project_deadline_from": "root_appointment",
                "days_relative_to_project": 0,
            }
        )
        self.assertEqual(
            child.deadline,
            date(2026, 8, 1),
            "Without appointment date, child should fall back to root's deadline",
        )

    def test_root_appointment_with_days_offset(self):
        """days_relative_to_project offset applies to the appointment date."""
        child_offset = self.env["riverflow.service"].create(
            {
                "name": "PD Appt Child Offset",
                "parent_id": self.root.id,
                "use_project_deadline_from": "root_appointment",
                "days_relative_to_project": -2,
            }
        )
        self.assertEqual(
            child_offset.deadline,
            date(2026, 7, 18),
            "Deadline should be appointment date (Jul 20) minus 2 days",
        )

    def test_root_appointment_time_follows_root(self):
        """The child shows the root's appointment time next to its date, so
        nobody retypes it in a note that can then disagree with the root."""
        self.root.supply_time_of_day = 10.25
        self.assertEqual(self.child.root_service_appointment_time, 10.25)
        self.root.supply_time_of_day = 14.5
        self.assertEqual(self.child.root_service_appointment_time, 14.5)

    def test_root_appointment_recomputes_on_supply_date_change(self):
        """Changing root's supply_actual_date recomputes child's deadline."""
        self.assertEqual(self.child.deadline, date(2026, 7, 20))
        self.root.supply_actual_date = date(2026, 9, 5)
        self.assertEqual(
            self.child.deadline,
            date(2026, 9, 5),
            "Child deadline should track root's new appointment date",
        )

    def test_root_appointment_ignores_root_deadline_change(self):
        """Root deadline changes don't affect child when supply_actual_date is set."""
        self.assertEqual(self.child.deadline, date(2026, 7, 20))
        # Simulate workflow progression changing root's deadline (followup_in_days)
        self.root.project_deadline = date(2026, 4, 10)
        self.assertEqual(
            self.child.deadline,
            date(2026, 7, 20),
            "Child should still use appointment date, ignoring root deadline change",
        )

    def test_root_appointment_fallback_switches_on_supply_date_set(self):
        """Child switches from deadline-based to supply-date-based when root gets appointment."""
        root = self.env["riverflow.service"].create(
            {
                "name": "PD Appt Root Switch",
                "project_deadline": date(2026, 8, 1),
            }
        )
        child = self.env["riverflow.service"].create(
            {
                "name": "PD Appt Child Switch",
                "parent_id": root.id,
                "use_project_deadline_from": "root_appointment",
                "days_relative_to_project": 0,
            }
        )
        # Initially uses fallback (root deadline)
        self.assertEqual(child.deadline, date(2026, 8, 1))
        # Root gets an appointment date — child should switch to it
        root.supply_actual_date = date(2026, 9, 15)
        self.assertEqual(
            child.deadline,
            date(2026, 9, 15),
            "Child should switch to appointment date once root gets one",
        )

    def test_root_appointment_is_days_relative_applicable(self):
        """is_days_relative_to_project_applicable is True for root_appointment child."""
        self.assertTrue(
            self.child.is_days_relative_to_project_applicable,
            "Child in root_appointment mode should have relative days applicable",
        )

    def test_root_appointment_prefix(self):
        """relative_to_project_days_prefix returns 'Appointment' for root_appointment mode."""
        self.assertEqual(self.child.relative_to_project_days_prefix(), "Appointment")

    def test_root_appointment_in_options(self):
        """'root_appointment' appears in options alongside 'root' when parent is set."""
        options = self.env["riverflow.service"].calculate_use_project_deadline_from_options_for_new_service(
            parent_id=self.root,
            res_model="riverflow.service",
            res_id=0,
            is_root_a_template=False,
        )
        self.assertIn("root_appointment", options)
        self.assertIn("root", options)
