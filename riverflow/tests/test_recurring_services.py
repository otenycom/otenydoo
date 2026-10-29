"""Recurring services, phase 1 (plan riverflow/plans/recurring-services.md, A1 and A4).

- With recurring_only in the context, the start screen lists only the start
  transitions and the templates of a recurring workflow; without it, everything.
- The action Recurring Services finds the services of a recurring workflow, not
  a template and not a service of another workflow.
- The menus Recurring and Templates have the parents, sequences and groups of A4.
"""

from odoo.tests import tagged
from odoo.tests.common import TransactionCase
from odoo.tools.safe_eval import safe_eval


@tagged("riverflow", "post_install", "-at_install", "test_recurring_services")
class TestRecurringServices(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        State = cls.env["riverflow.state"]
        Transition = cls.env["riverflow.transition"]
        Workflow = cls.env["riverflow.workflow"]
        Service = cls.env["riverflow.service"]
        default_action = cls.env.ref("riverflow.transition_action_default")
        service_model = cls.env["ir.model"]._get("riverflow.service").id

        def make_workflow(name, is_recurring):
            workflow = Workflow.create({"name": name, "model_id": service_model, "is_recurring": is_recurring})
            state = State.create({"name": f"{name} state", "workflow_id": workflow.id, "sequence": 10})
            transition = Transition.create(
                {
                    "name": f"Start {name}",
                    "to_state_id": state.id,
                    "action_id": default_action.id,
                    "sequence": 10,
                }
            )
            template = Service.create(
                {
                    "name": f"Template {name}",
                    "is_this_a_template": True,
                    "workflow_id": workflow.id,
                    "state_id": state.id,
                }
            )
            service = Service.create(
                {"name": f"Service {name}", "workflow_id": workflow.id, "state_id": state.id}
            )
            return workflow, transition, template, service

        (cls.recurring_workflow, cls.recurring_transition, cls.recurring_template, cls.recurring_service) = (
            make_workflow("Recurring test", True)
        )
        (cls.other_workflow, cls.other_transition, cls.other_template, cls.other_service) = make_workflow(
            "Other test", False
        )

    def _start_screen(self, **context):
        return self.env["riverflow.start.service"].with_context(**context).create({})

    def _template_ids(self, wizard):
        return {
            button["context"]["template_service_id"]
            for button in wizard.transition_buttons_json["buttons"]
            if button.get("is_template")
        }

    def _transition_ids(self, wizard):
        return {
            button["context"]["transition_id"]
            for button in wizard.transition_buttons_json["buttons"]
            if not button.get("is_template")
        }

    def test_flag_defaults_to_false(self):
        workflow = self.env["riverflow.workflow"].create(
            {"name": "Default flag", "model_id": self.env["ir.model"]._get("riverflow.service").id}
        )
        self.assertFalse(workflow.is_recurring)

    def test_start_screen_recurring_only_lists_recurring_workflows(self):
        wizard = self._start_screen(recurring_only=True)
        self.assertIn(self.recurring_transition, wizard.start_transition_ids)
        self.assertNotIn(self.other_transition, wizard.start_transition_ids)
        self.assertIn(self.recurring_transition.id, self._transition_ids(wizard))
        self.assertNotIn(self.other_transition.id, self._transition_ids(wizard))
        self.assertIn(self.recurring_template.id, self._template_ids(wizard))
        self.assertNotIn(self.other_template.id, self._template_ids(wizard))

    def test_start_screen_without_the_key_lists_everything(self):
        wizard = self._start_screen()
        self.assertIn(self.recurring_transition.id, self._transition_ids(wizard))
        self.assertIn(self.other_transition.id, self._transition_ids(wizard))
        self.assertIn(self.recurring_template.id, self._template_ids(wizard))
        self.assertIn(self.other_template.id, self._template_ids(wizard))

    def test_recurring_services_action_domain(self):
        action = self.env.ref("riverflow.action_recurring_services")
        self.assertEqual(action.res_model, "riverflow.service")
        domain = safe_eval(action.domain)
        found = self.env["riverflow.service"].search(domain)
        self.assertIn(self.recurring_service, found)
        self.assertNotIn(self.recurring_template, found)
        self.assertNotIn(self.other_service, found)
        self.assertNotIn(self.other_template, found)

    def test_start_action_has_the_recurring_only_context(self):
        action = self.env.ref("riverflow.action_riverflow_start_recurring_service")
        self.assertEqual(action.res_model, "riverflow.start.service")
        self.assertEqual(action.target, "new")
        self.assertEqual(action.path, "start-recurring-service")
        self.assertEqual(safe_eval(action.context), {"recurring_only": True})

    def test_menu_structure(self):
        def menu(xmlid):
            return self.env.ref(f"riverflow.{xmlid}")

        root = menu("service_root")
        for xmlid, parent, sequence in (
            ("services_menu", root, 10),
            ("service_menu_new_service", root, 30),
            ("service_menu_recurring_root", root, 40),
            ("service_menu_templates_root", root, 50),
            ("services_configuration_menu", root, 90),
            ("service_menu_recurring_services", menu("service_menu_recurring_root"), 10),
            ("service_menu_start_recurring_service", menu("service_menu_recurring_root"), 20),
            ("services_menu_configuration_templates", menu("service_menu_templates_root"), 10),
            ("service_menu_start_template", menu("service_menu_templates_root"), 20),
        ):
            with self.subTest(menu=xmlid):
                self.assertEqual(menu(xmlid).parent_id, parent)
                self.assertEqual(menu(xmlid).sequence, sequence)
        self.assertEqual(
            menu("service_menu_start_recurring_service").group_ids,
            self.env.ref("riverflow.group_service_writer"),
        )
        self.assertEqual(
            menu("service_menu_recurring_services").action,
            self.env.ref("riverflow.action_recurring_services"),
        )
        # The templates left Configuration.
        configuration = menu("services_configuration_menu")
        self.assertNotIn(menu("services_menu_configuration_templates"), configuration.child_id)
        self.assertNotIn(menu("service_menu_start_template"), configuration.child_id)
