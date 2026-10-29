"""The transition keys next_deadline_weekday and ask_deadline (plan
riverflow/plans/recurring-services.md, A2).

A transition of a recurring service sets the deadline itself, with a key in its
action_context:

- next_deadline_weekday: the screen proposes the first such weekday strictly
  after the current deadline of the service, never counted from today (only a
  service without a deadline counts from today);
- ask_deadline: the screen shows the deadline field empty and required.

In both cases the date on the screen is written as a fixed date, so the Deadline
on Entry of the target state does not overwrite it, and the e-mail sender ends
with that date instead of tomorrow.
"""

from datetime import date, timedelta

from freezegun import freeze_time

from odoo.exceptions import UserError
from odoo.tests import Form, tagged
from odoo.tests.common import TransactionCase

MONDAY = date(2026, 10, 26)


@tagged("riverflow", "post_install", "-at_install", "test_step_deadline_weekday")
class TestStepDeadlineWeekday(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        service_model = cls.env["ir.model"]._get("riverflow.service")
        cls.default_action = cls.env.ref("riverflow.transition_action_default")
        cls.email_action = cls.env.ref("riverflow.transition_action_email_sender")
        cls.workflow = cls.env["riverflow.workflow"].create({"name": "Weekday keys", "model_id": service_model.id})
        State = cls.env["riverflow.state"]
        cls.state_a = State.create({"name": "A", "workflow_id": cls.workflow.id, "sequence": 10})
        cls.state_b = State.create({"name": "B", "workflow_id": cls.workflow.id, "sequence": 20})
        # A state that sets the deadline to today on every entry.
        cls.state_today = State.create(
            {"name": "Today", "workflow_id": cls.workflow.id, "sequence": 30, "deadline_on_entry": "today"}
        )
        cls.recipient = cls.env["res.partner"].create({"name": "Recipient", "email": "recipient@example.com"})
        cls.template = cls.env["mail.template"].create(
            {"name": "Weekday keys", "model_id": service_model.id, "subject": "Subject", "body_html": "<p>Body</p>"}
        )

    def _transition(self, context, from_state=None, to_state=None, action=None):
        return self.env["riverflow.transition"].create(
            {
                "name": "Step %s" % context,
                "from_state_id": (from_state or self.state_a).id,
                "to_state_id": (to_state or self.state_b).id,
                "action_id": (action or self.default_action).id,
                "action_context": context,
                "workflow_id": self.workflow.id,
                "mail_template_id": self.template.id if action == self.email_action else False,
            }
        )

    def _service(self, deadline, state=None):
        return self.env["riverflow.service"].create(
            {
                "name": "Weekly",
                "workflow_id": self.workflow.id,
                "state_id": (state or self.state_a).id,
                "use_project_deadline_from": "self",
                "project_deadline": deadline,
            }
        )

    def _screen(self, service, transition, values=None):
        """The transition screen as the button opens it: the action, then the wizard
        record that the client creates from the defaults and the user's changes."""
        action = service.with_context(transition_id=transition.id).prepare_transition_action()
        Wizard = self.env[action["res_model"]].with_context(**action["context"])
        return action, Wizard.create(values or {})

    def _run(self, service, transition, values=None):
        action, wizard = self._screen(service, transition, values)
        wizard.with_context(**action["context"]).action_save()
        return service

    def test_proposes_the_next_weekday_strictly_after_the_deadline(self):
        """For a current deadline on each of the seven weekdays, 'friday' and
        'monday' propose the first such weekday strictly after it. The result does
        not depend on the day of the click: a day before and a day after the
        deadline give the same date."""
        friday = self._transition("{'next_deadline_weekday': 'friday'}")
        monday = self._transition("{'next_deadline_weekday': 'monday'}")
        expected = {
            # (deadline offset from Monday 26-Oct-2026): (next friday, next monday)
            0: (date(2026, 10, 30), date(2026, 11, 2)),
            1: (date(2026, 10, 30), date(2026, 11, 2)),
            2: (date(2026, 10, 30), date(2026, 11, 2)),
            3: (date(2026, 10, 30), date(2026, 11, 2)),
            4: (date(2026, 11, 6), date(2026, 11, 2)),
            5: (date(2026, 11, 6), date(2026, 11, 2)),
            6: (date(2026, 11, 6), date(2026, 11, 2)),
        }
        for offset, (next_friday, next_monday) in expected.items():
            deadline = MONDAY + timedelta(days=offset)
            service = self._service(deadline)
            for click_day in (deadline - timedelta(days=3), deadline + timedelta(days=3)):
                with freeze_time(click_day):
                    for transition, want in ((friday, next_friday), (monday, next_monday)):
                        _action, wizard = self._screen(service, transition)
                        self.assertEqual(
                            wizard.project_deadline,
                            want,
                            "deadline %s clicked on %s with %s" % (deadline, click_day, transition.action_context),
                        )
                        self.assertFalse(wizard.project_deadline_invisible)
                        self.assertEqual(wizard.use_project_deadline_from, "self")

    def test_the_issue_examples(self):
        """Mon 26-Oct with 'friday' gives Fri 30-Oct; Fri 30-Oct with 'monday' gives
        Mon 2-Nov; Mon 26-Oct with 'monday' gives Mon 2-Nov."""
        friday = self._transition("{'next_deadline_weekday': 'friday'}")
        monday = self._transition("{'next_deadline_weekday': 'monday'}")
        self.assertEqual(self._screen(self._service(MONDAY), friday)[1].project_deadline, date(2026, 10, 30))
        self.assertEqual(self._screen(self._service(date(2026, 10, 30)), monday)[1].project_deadline, date(2026, 11, 2))
        self.assertEqual(self._screen(self._service(MONDAY), monday)[1].project_deadline, date(2026, 11, 2))

    def test_a_service_without_a_deadline_counts_from_today(self):
        monday = self._transition("{'next_deadline_weekday': 'monday'}")
        service = self._service(False)
        with freeze_time("2026-10-28"):  # a Wednesday
            self._run(service, monday)
        self.assertEqual(service.state_id, self.state_b)
        self.assertEqual(service.project_deadline, date(2026, 11, 2))
        # Today is a Monday: strictly after means the next Monday.
        service = self._service(False)
        with freeze_time("2026-10-26"):
            self._run(service, monday)
        self.assertEqual(service.project_deadline, date(2026, 11, 2))

    def test_two_finished_weeks_in_a_row_move_two_weeks(self):
        """Two Upload steps in a row from a Monday deadline give the Monday a week
        later and then the Monday after that, never the same week twice, also when
        both are clicked on the same day."""
        there = self._transition("{'next_deadline_weekday': 'monday'}", self.state_a, self.state_b)
        back = self._transition("{'next_deadline_weekday': 'monday'}", self.state_b, self.state_a)
        service = self._service(MONDAY)
        with freeze_time("2026-10-27"):
            self._run(service, there)
            self.assertEqual(service.project_deadline, date(2026, 11, 2))
            self._run(service, back)
            self.assertEqual(service.project_deadline, date(2026, 11, 9))

    def test_a_date_typed_on_the_screen_wins(self):
        monday = self._transition("{'next_deadline_weekday': 'monday'}")
        service = self._service(MONDAY)
        typed = date(2026, 11, 4)
        self._run(service, monday, {"project_deadline": typed})
        self.assertEqual(
            (service.use_project_deadline_from, service.project_deadline, service.days_relative_to_project),
            ("self", typed, 0),
        )

    def test_the_target_state_does_not_overwrite_the_date(self):
        """A target state with Deadline on Entry = today does not overwrite the key's date."""
        monday = self._transition("{'next_deadline_weekday': 'monday'}", to_state=self.state_today)
        service = self._service(MONDAY)
        with freeze_time("2026-10-27"):
            self._run(service, monday)
        self.assertEqual(service.state_id, self.state_today)
        self.assertEqual(service.project_deadline, date(2026, 11, 2))

    def test_a_wizard_without_a_deadline_field_gets_the_proposal(self):
        """A custom action has no deadline field on its screen: the key still sets the date."""
        friday = self._transition("{'next_deadline_weekday': 'friday'}")
        service = self._service(MONDAY)
        action, wizard = self._screen(service, friday, {"project_deadline": False})
        wizard.with_context(**action["context"]).action_save()
        self.assertEqual(service.project_deadline, date(2026, 10, 30))

    def test_an_unknown_weekday_names_the_key(self):
        bad = self._transition("{'next_deadline_weekday': 'someday'}")
        service = self._service(MONDAY)
        with self.assertRaisesRegex(UserError, "next_deadline_weekday"):
            self._screen(service, bad)

    def test_followup_in_days_still_counts_from_today(self):
        followup = self._transition("{'followup_in_days': 3}")
        service = self._service(MONDAY)
        with freeze_time("2026-11-10"):
            self._run(service, followup)
        self.assertEqual(service.project_deadline, date(2026, 11, 13))

    def test_ask_deadline_shows_an_empty_required_field(self):
        ask = self._transition("{'ask_deadline': True}")
        service = self._service(date(2026, 10, 30))
        _action, wizard = self._screen(service, ask)
        self.assertFalse(wizard.project_deadline)
        self.assertFalse(wizard.project_deadline_invisible)
        self.assertTrue(wizard.project_deadline_required)
        self.assertEqual(wizard.use_project_deadline_from, "self")

    def test_ask_deadline_writes_the_date_and_refuses_an_empty_one(self):
        ask = self._transition("{'ask_deadline': True}", to_state=self.state_today)
        service = self._service(date(2026, 10, 30))
        with self.assertRaises(UserError):
            self._run(service, ask)
        self.assertEqual(service.state_id, self.state_a, "a refused save leaves the service where it was")
        chosen = date(2026, 11, 5)
        self._run(service, ask, {"project_deadline": chosen})
        self.assertEqual(service.state_id, self.state_today)
        self.assertEqual(
            (service.use_project_deadline_from, service.project_deadline, service.days_relative_to_project),
            ("self", chosen, 0),
            "the target state's Deadline on Entry does not overwrite the date",
        )

    def test_ask_deadline_form_requires_the_date(self):
        """The view makes the field required: the form refuses to save without it."""
        ask = self._transition("{'ask_deadline': True}")
        service = self._service(date(2026, 10, 30))
        action = service.with_context(transition_id=ask.id).prepare_transition_action()
        Wizard = self.env[action["res_model"]].with_context(**action["context"])
        form = Form(Wizard, view=action["views"][0][0])
        if not form.responsible_team_id:  # required on this screen, unrelated to the deadline
            form.responsible_team_id = self.env["res.partner"].search([("is_riverflow_team", "=", True)], limit=1)
        with self.assertRaisesRegex(AssertionError, "project_deadline is a required field"):
            form.save()
        form.project_deadline = date(2026, 11, 5)
        form.save()

    def _email_run(self, service, transition, values=None):
        action = service.with_context(transition_id=transition.id).prepare_transition_action()
        Wizard = self.env[action["res_model"]].with_context(**action["context"])
        wizard = Wizard.create({"recipient_partner_ids": [(6, 0, [self.recipient.id])], **(values or {})})
        wizard.onchange_email_template_id()
        wizard.recipient_partner_ids = self.recipient
        wizard.with_context(**action["context"]).action_save()
        return wizard

    def test_the_email_sender_writes_the_proposed_date(self):
        send = self._transition("{'next_deadline_weekday': 'friday', 'keep_service_name': True}", action=self.email_action)
        service = self._service(MONDAY)
        action = service.with_context(transition_id=send.id).prepare_transition_action()
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create({})
        self.assertFalse(wizard.project_deadline_invisible, "the Follow-up deadline field is shown")
        self.assertEqual(wizard.project_deadline, date(2026, 10, 30))
        with freeze_time("2026-10-27"):
            self._email_run(service, send)
        self.assertEqual(service.state_id, self.state_b)
        self.assertEqual(service.project_deadline, date(2026, 10, 30), "not tomorrow")

    def test_the_email_sender_with_ask_deadline_is_required(self):
        send = self._transition("{'ask_deadline': True, 'keep_service_name': True}", action=self.email_action)
        service = self._service(MONDAY)
        action = service.with_context(transition_id=send.id).prepare_transition_action()
        wizard = self.env[action["res_model"]].with_context(**action["context"]).create({})
        self.assertFalse(wizard.project_deadline)
        self.assertTrue(wizard.project_deadline_required)
        with self.assertRaises(UserError):
            self._email_run(service, send)
        chosen = date(2026, 11, 5)
        self._email_run(service, send, {"project_deadline": chosen})
        self.assertEqual(service.project_deadline, chosen)

    def test_the_email_sender_without_a_key_writes_tomorrow(self):
        send = self._transition("{'keep_service_name': True}", action=self.email_action)
        service = self._service(MONDAY)
        with freeze_time("2026-10-27"):
            self._email_run(service, send)
        self.assertEqual(service.project_deadline, date(2026, 10, 28))
