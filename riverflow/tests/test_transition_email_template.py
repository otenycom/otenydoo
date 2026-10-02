from datetime import timedelta

from odoo import fields
from odoo.tests.common import TransactionCase
from odoo.tests import tagged


def _create_email_test_data(cls):
    """Shared setup for email template and keep_service_name tests."""
    Service = cls.env["riverflow.service"]
    State = cls.env["riverflow.state"]
    Workflow = cls.env["riverflow.workflow"]
    Transition = cls.env["riverflow.transition"]
    service_model = cls.env["ir.model"]._get("riverflow.service")
    cls.email_action = cls.env.ref("riverflow.transition_action_email_sender")

    cls.workflow = Workflow.create({
        "model_id": service_model.id,
        "name": "Test Email Template WF",
    })
    cls.state_a = State.create({
        "workflow_id": cls.workflow.id,
        "name": "State A",
        "sequence": 10,
    })
    cls.state_b = State.create({
        "workflow_id": cls.workflow.id,
        "name": "State B",
        "sequence": 20,
    })

    cls.template_alpha = cls.env["mail.template"].create({
        "name": "Template Alpha",
        "model_id": service_model.id,
        "subject": "Alpha subject",
        "body_html": "<p>Alpha body</p>",
    })
    cls.template_beta = cls.env["mail.template"].create({
        "name": "Template Beta",
        "model_id": service_model.id,
        "subject": "Beta subject",
        "body_html": "<p>Beta body</p>",
    })

    cls.trans_with_template = Transition.create({
        "name": "Send Alpha",
        "from_state_id": cls.state_a.id,
        "to_state_id": cls.state_b.id,
        "action_id": cls.email_action.id,
        "mail_template_id": cls.template_alpha.id,
        "sequence": 10,
    })

    cls.trans_without_template = Transition.create({
        "name": "Send Fallback",
        "from_state_id": cls.state_a.id,
        "to_state_id": cls.state_b.id,
        "action_id": cls.email_action.id,
        "sequence": 20,
    })


@tagged("post_install", "-at_install", "riverflow", "test_transition_email_template")
class TestTransitionEmailTemplate(TransactionCase):
    """Test mail_template_id on riverflow.transition and fallback logic
    in the email sender wizard.

    The transition-level template allows different email transitions within
    the same workflow to use different templates (e.g., AB appointment request
    vs. pickup request in the Work Permit workflow).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_email_test_data(cls)

        cls.service = cls.env["riverflow.service"].create({
            "name": "Test Service",
            "state_id": cls.state_a.id,
            "company_id": cls.env.company.id,
            "mail_template_id": cls.template_beta.id,
        })

    def test_transition_mail_template_id_field(self):
        """The mail_template_id field exists on riverflow.transition."""
        self.assertIn("mail_template_id", self.env["riverflow.transition"]._fields)
        self.assertEqual(self.trans_with_template.mail_template_id, self.template_alpha)
        self.assertFalse(self.trans_without_template.mail_template_id)

    def _wizard_defaults(self, transition, service):
        """Open the email sender wizard as if the transition button was clicked."""
        Wizard = self.env["riverflow.service.email.sender.wizard"]
        return Wizard.with_context(
            transition_id=transition.id,
            active_model="riverflow.service",
            active_ids=service.ids,
        ).default_get(Wizard._fields.keys())

    def test_email_wizard_prefers_transition_template(self):
        """When the transition has a mail_template_id, the wizard uses it
        instead of the service's template."""
        defaults = self._wizard_defaults(self.trans_with_template, self.service)

        self.assertEqual(
            defaults.get("mail_template_id"),
            self.template_alpha.id,
            "Wizard should use the transition's template (Alpha), not the service's (Beta)",
        )

    def test_email_wizard_falls_back_to_service_template(self):
        """When the transition has no mail_template_id, the wizard falls
        back to the service's mail_template_id."""
        defaults = self._wizard_defaults(self.trans_without_template, self.service)

        self.assertEqual(
            defaults.get("mail_template_id"),
            self.template_beta.id,
            "Wizard should fall back to the service's template (Beta)",
        )

    def test_email_wizard_no_template_anywhere(self):
        """When neither transition nor service has a template, wizard
        should not set mail_template_id."""
        service_no_tpl = self.env["riverflow.service"].create({
            "name": "No Template Service",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        defaults = self._wizard_defaults(self.trans_without_template, service_no_tpl)

        self.assertFalse(
            defaults.get("mail_template_id"),
            "Wizard should not set mail_template_id when neither transition nor service has one",
        )


@tagged("post_install", "-at_install", "riverflow", "test_transition_email_template")
class TestKeepServiceName(TransactionCase):
    """Test that keep_service_name in action_context prevents the email sender
    wizard from overwriting the service name with the email subject.

    Multi-step workflows (e.g. Work Permit) use email transitions as intermediate
    steps — the service name should stay descriptive ("Arrange Work Permit at AB")
    rather than being overwritten with the German email subject line.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_email_test_data(cls)

        Transition = cls.env["riverflow.transition"]

        cls.recipient = cls.env["res.partner"].create({
            "name": "Test Recipient",
            "email": "test@example.com",
        })

        cls.trans_keep_name = Transition.create({
            "name": "Send Keep Name",
            "from_state_id": cls.state_a.id,
            "to_state_id": cls.state_b.id,
            "action_id": cls.email_action.id,
            "mail_template_id": cls.template_alpha.id,
            "action_context": "{'keep_service_name': True}",
            "sequence": 30,
        })

    def _get_write_vals(self, service, transition):
        """Open the email sender wizard and collect the write vals that
        update_write_values would apply to the service, without actually
        sending the email. Tests the name-setting logic in isolation.

        Merges the transition's action_context into the wizard context
        (mimicking what _prepare_action_context does in the real flow).
        """
        import ast
        extra_ctx = {}
        if transition.action_context:
            extra_ctx = ast.literal_eval(transition.action_context) or {}

        ctx = {
            "transition_id": transition.id,
            "active_model": "riverflow.service",
            "active_ids": service.ids,
            **extra_ctx,
        }
        Wizard = self.env["riverflow.service.email.sender.wizard"].with_context(**ctx)
        defaults = Wizard.default_get(Wizard._fields.keys())
        defaults["recipient_partner_ids"] = [(6, 0, [self.recipient.id])]
        wizard = Wizard.create(defaults)

        vals = {}
        wizard.update_write_values(service, vals)
        return vals

    def test_keep_service_name_preserves_name(self):
        """With keep_service_name in action_context, update_write_values does
        NOT set vals["name"] — the service name stays as originally set."""
        service = self.env["riverflow.service"].create({
            "name": "Arrange Work Permit at AB",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        vals = self._get_write_vals(service, self.trans_keep_name)

        self.assertNotIn(
            "name", vals,
            "vals should not contain 'name' when keep_service_name is set",
        )

    def test_default_email_transition_renames_service(self):
        """Without keep_service_name, update_write_values sets vals["name"]
        to the email subject (existing behavior)."""
        service = self.env["riverflow.service"].create({
            "name": "Original Name",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        vals = self._get_write_vals(service, self.trans_with_template)

        self.assertIn(
            "name", vals,
            "vals should contain 'name' when keep_service_name is not set",
        )

    def test_email_message_uses_template_subject(self):
        """The mail.message created by _send_email must carry the rendered
        template subject, not the service name.

        Bug: message_post was called without subject=, causing Odoo to fall
        back to the record's display_name (service name) as the email subject.
        With keep_service_name the service name stays "Arrange Work Permit at AB"
        but the outgoing email should still use the template's subject line.
        """
        service = self.env["riverflow.service"].create({
            "name": "Arrange Work Permit at AB",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })

        import ast
        extra_ctx = ast.literal_eval(self.trans_keep_name.action_context)
        ctx = {
            "transition_id": self.trans_keep_name.id,
            "active_model": "riverflow.service",
            "active_ids": service.ids,
            **extra_ctx,
        }
        Wizard = self.env["riverflow.service.email.sender.wizard"].with_context(**ctx)
        defaults = Wizard.default_get(Wizard._fields.keys())
        defaults["recipient_partner_ids"] = [(6, 0, [self.recipient.id])]
        wizard = Wizard.create(defaults)
        # Simulate the UI: onchange populates subject/body from the template
        wizard.onchange_email_template_id()
        wizard.recipient_partner_ids = self.recipient
        wizard.action_save()

        # The service name must be preserved (keep_service_name)
        self.assertEqual(service.name, "Arrange Work Permit at AB")

        # The mail.message subject must be the template subject, not the service name
        message = service.message_ids.filtered(
            lambda m: m.message_type == "email"
        )
        self.assertTrue(message, "An email message should have been posted")
        self.assertEqual(
            message[0].subject,
            "Alpha subject",
            "mail.message subject should be the rendered template subject, "
            "not the service name",
        )


@tagged("post_install", "-at_install", "riverflow", "test_transition_email_template")
class TestFollowupInDays(TransactionCase):
    """Test that followup_in_days in action_context pre-fills the deadline
    and overrides the email sender's default of tomorrow.

    HR workflows (e.g. Work Permit) need configurable follow-up periods
    per transition — 7 days for AB requests (weekly visit rhythm), 14 days
    for AT Applied (preparation time for pickup request).
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_email_test_data(cls)

        Transition = cls.env["riverflow.transition"]
        cls.default_action = cls.env.ref("riverflow.transition_action_default")

        cls.trans_followup_7 = Transition.create({
            "name": "Default with followup 7",
            "from_state_id": cls.state_a.id,
            "to_state_id": cls.state_b.id,
            "action_id": cls.default_action.id,
            "action_context": "{'followup_in_days': 7}",
            "sequence": 40,
        })

        cls.trans_email_followup_14 = Transition.create({
            "name": "Email with followup 14",
            "from_state_id": cls.state_a.id,
            "to_state_id": cls.state_b.id,
            "action_id": cls.email_action.id,
            "mail_template_id": cls.template_alpha.id,
            "action_context": "{'keep_service_name': True, 'followup_in_days': 14}",
            "sequence": 50,
        })

        cls.recipient = cls.env["res.partner"].create({
            "name": "Test Recipient",
            "email": "test@example.com",
        })

    def _get_wizard_defaults(self, service, transition):
        """Get wizard defaults with action_context merged into context."""
        import ast
        extra_ctx = {}
        if transition.action_context:
            extra_ctx = ast.literal_eval(transition.action_context) or {}

        ctx = {
            "transition_id": transition.id,
            "active_model": "riverflow.service",
            "active_ids": service.ids,
            **extra_ctx,
        }
        Wizard = self.env["riverflow.service.wizard"].with_context(**ctx)
        return Wizard.default_get(Wizard._fields.keys())

    def test_followup_in_days_prefills_project_deadline(self):
        """When followup_in_days is in context, project_deadline is pre-filled
        with today + N days and the deadline field is visible."""
        service = self.env["riverflow.service"].create({
            "name": "Test Service",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        defaults = self._get_wizard_defaults(service, self.trans_followup_7)

        expected_date = fields.Date.today() + timedelta(days=7)
        self.assertEqual(
            defaults.get("project_deadline"),
            expected_date,
            "project_deadline should be today + 7 days",
        )
        self.assertFalse(
            defaults.get("project_deadline_invisible"),
            "project_deadline should be visible when followup_in_days is set",
        )

    def test_followup_in_days_sets_service_deadline(self):
        """Firing a default-action transition with followup_in_days sets the
        service deadline to today + N days."""
        service = self.env["riverflow.service"].create({
            "name": "Test Service",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        import ast
        extra_ctx = ast.literal_eval(self.trans_followup_7.action_context)
        ctx = {
            "transition_id": self.trans_followup_7.id,
            "active_model": "riverflow.service",
            "active_ids": service.ids,
            **extra_ctx,
        }
        Wizard = self.env["riverflow.service.wizard"].with_context(**ctx)
        defaults = Wizard.default_get(Wizard._fields.keys())
        wizard = Wizard.create(defaults)
        wizard.action_save()

        expected_date = fields.Date.today() + timedelta(days=7)
        self.assertEqual(service.state_id, self.state_b)
        self.assertEqual(service.project_deadline, expected_date)

    def test_followup_in_days_without_wizard_deadline(self):
        """A wizard with no project_deadline value (e.g. a custom action whose
        wizard has no deadline field) still sets today + N. Before, the key
        silently did nothing there."""
        service = self.env["riverflow.service"].create({
            "name": "Test Service",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        import ast
        extra_ctx = ast.literal_eval(self.trans_followup_7.action_context)
        ctx = {
            "transition_id": self.trans_followup_7.id,
            "active_model": "riverflow.service",
            "active_ids": service.ids,
            **extra_ctx,
        }
        Wizard = self.env["riverflow.service.wizard"].with_context(**ctx)
        defaults = Wizard.default_get(Wizard._fields.keys())
        defaults["project_deadline"] = False
        wizard = Wizard.create(defaults)
        wizard.action_save()

        self.assertEqual(service.state_id, self.state_b)
        self.assertEqual(service.use_project_deadline_from, "self")
        self.assertEqual(service.project_deadline, fields.Date.today() + timedelta(days=7))

    def test_followup_in_days_overrides_email_sender_default(self):
        """When an email transition has followup_in_days, the deadline is
        today + N (not the email sender's default of tomorrow)."""
        service = self.env["riverflow.service"].create({
            "name": "Test Service",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        import ast
        extra_ctx = ast.literal_eval(self.trans_email_followup_14.action_context)
        ctx = {
            "transition_id": self.trans_email_followup_14.id,
            "active_model": "riverflow.service",
            "active_ids": service.ids,
            **extra_ctx,
        }
        Wizard = self.env["riverflow.service.email.sender.wizard"].with_context(**ctx)
        defaults = Wizard.default_get(Wizard._fields.keys())
        defaults["recipient_partner_ids"] = [(6, 0, [self.recipient.id])]
        wizard = Wizard.create(defaults)

        # Check that defaults show the followup date, not tomorrow
        expected_date = fields.Date.today() + timedelta(days=14)
        self.assertEqual(
            defaults.get("project_deadline"),
            expected_date,
            "Email sender defaults should show followup date, not tomorrow",
        )
        self.assertFalse(
            defaults.get("project_deadline_invisible"),
            "Follow-up deadline should be visible on the email sender form",
        )

    def test_email_sender_hides_deadline_by_default(self):
        """Without followup_in_days, the email sender hides project_deadline."""
        service = self.env["riverflow.service"].create({
            "name": "Test Service",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        ctx = {
            "transition_id": self.trans_with_template.id,
            "active_model": "riverflow.service",
            "active_ids": service.ids,
        }
        Wizard = self.env["riverflow.service.email.sender.wizard"].with_context(**ctx)
        defaults = Wizard.default_get(Wizard._fields.keys())

        self.assertTrue(
            defaults.get("project_deadline_invisible"),
            "project_deadline should be hidden for normal email transitions",
        )


@tagged("post_install", "-at_install", "riverflow", "test_email_sender_snooze")
class TestEmailSenderSnooze(TransactionCase):
    """Generic deadline-snooze for reminder / chase transitions.

    The base email sender wizard reads two action_context keys:
      - snooze_deadline_days (int): push project_deadline to today + N
      - snooze_deadline_cap_field (str): dotted path on the service
        whose value caps the snooze (e.g. log_entry_id.start_date)

    Used by the AUV Send Reminder transition; documented as a generic
    primitive so future chase transitions (A1 reminder, work permit
    reminder) can opt in via action_context alone.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_email_test_data(cls)

        cls.recipient = cls.env["res.partner"].create({
            "name": "Snooze Recipient",
            "email": "snooze@example.com",
        })

    def _open_snooze_wizard(self, service, snooze_ctx):
        ctx = {
            "transition_id": self.trans_with_template.id,
            "active_model": "riverflow.service",
            "active_ids": service.ids,
            **snooze_ctx,
        }
        Wizard = self.env["riverflow.service.email.sender.wizard"].with_context(**ctx)
        defaults = Wizard.default_get(Wizard._fields.keys())
        defaults["recipient_partner_ids"] = [(6, 0, [self.recipient.id])]
        return Wizard.create(defaults)

    def test_snooze_pushes_deadline_by_n_days(self):
        """snooze_deadline_days=3 with no cap → project_deadline = today + 3."""
        service = self.env["riverflow.service"].create({
            "name": "Snooze Service",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        wizard = self._open_snooze_wizard(service, {"snooze_deadline_days": 3})
        vals = {}
        wizard.update_write_values(service, vals)
        self.assertEqual(
            vals.get("project_deadline"),
            fields.Date.today() + timedelta(days=3),
            "Snooze must override the default 'tomorrow' to today + N",
        )

    def test_snooze_noop_when_no_context(self):
        """Without snooze_deadline_days, project_deadline stays at the
        existing email-sender default of tomorrow."""
        service = self.env["riverflow.service"].create({
            "name": "Snooze Service No Ctx",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        wizard = self._open_snooze_wizard(service, {})
        vals = {}
        wizard.update_write_values(service, vals)
        self.assertEqual(
            vals.get("project_deadline"),
            fields.Date.today() + timedelta(days=1),
            "Without snooze context, project_deadline stays at tomorrow",
        )

    def test_snooze_skips_cap_when_field_unresolvable(self):
        """When snooze_deadline_cap_field points at a non-existent path
        on the service, the cap silently no-ops — snooze still applies."""
        service = self.env["riverflow.service"].create({
            "name": "Snooze No Cap Field",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        wizard = self._open_snooze_wizard(
            service,
            {
                "snooze_deadline_days": 3,
                "snooze_deadline_cap_field": "nonexistent_field_id.start_date",
            },
        )
        vals = {}
        # Must not raise.
        wizard.update_write_values(service, vals)
        self.assertEqual(
            vals.get("project_deadline"),
            fields.Date.today() + timedelta(days=3),
        )


@tagged("post_install", "-at_install", "riverflow", "test_email_sender_snooze")
class TestSetDeadlineRelative(TransactionCase):
    """set_deadline_relative writes the two relative fields only.

    It drops the email sender's today + 1 write. It does not write
    project_deadline. It does not touch weekend_deadline_rule.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        _create_email_test_data(cls)
        Transition = cls.env["riverflow.transition"]
        cls.trans_relative = Transition.create({
            "name": "Send Relative",
            "from_state_id": cls.state_a.id,
            "to_state_id": cls.state_b.id,
            "action_id": cls.email_action.id,
            "mail_template_id": cls.template_alpha.id,
            "action_context": (
                "{'keep_service_name': True, "
                "'set_deadline_relative': {'from': 'creation', 'days': 3}}"
            ),
            "sequence": 60,
        })
        cls.recipient = cls.env["res.partner"].create({
            "name": "Relative Recipient",
            "email": "relative@example.com",
        })

    def test_set_deadline_relative_writes_from_and_days(self):
        service = self.env["riverflow.service"].create({
            "name": "Relative Service",
            "state_id": self.state_a.id,
            "company_id": self.env.company.id,
        })
        import ast
        extra_ctx = ast.literal_eval(self.trans_relative.action_context)
        ctx = {
            "transition_id": self.trans_relative.id,
            "active_model": "riverflow.service",
            "active_ids": service.ids,
            **extra_ctx,
        }
        Wizard = self.env["riverflow.service.email.sender.wizard"].with_context(**ctx)
        defaults = Wizard.default_get(Wizard._fields.keys())
        defaults["recipient_partner_ids"] = [(6, 0, [self.recipient.id])]
        defaults["subject_updatable"] = "Relative subject"
        defaults["body_updatable"] = "<p>Relative body</p>"
        wizard = Wizard.create(defaults)
        wizard.action_save()
        self.assertEqual(service.state_id, self.state_b)
        self.assertEqual(service.use_project_deadline_from, "creation")
        self.assertEqual(service.days_relative_to_project, 3)
        self.assertNotEqual(
            service.project_deadline,
            fields.Date.today() + timedelta(days=1),
        )
