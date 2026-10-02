from odoo.tests.common import TransactionCase
from odoo.tests import tagged


@tagged("post_install", "-at_install", "riverflow", "test_deferred_children")
class TestDeferredChildrenContextIsolation(TransactionCase):
    """Test that deferred children don't inherit parent field values via context.

    The transition mixin copies ALL parent fields as default_* context keys
    for wizard form pre-population. _create_deferred_children must isolate
    child creation from these leaked defaults — children should get values
    from the template only, not from the parent service.

    supply_unit_price is used as the test proxy: it's a stored field on
    riverflow.service that _create_service_member_from_template does NOT
    explicitly set in its vals dict, making it vulnerable to context leaks.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        Service = cls.env["riverflow.service"]
        State = cls.env["riverflow.state"]
        Workflow = cls.env["riverflow.workflow"]
        Transition = cls.env["riverflow.transition"]
        service_model = cls.env["ir.model"]._get("riverflow.service")
        default_action = cls.env.ref("riverflow.transition_action_default")

        cls.workflow = Workflow.create({
            "model_id": service_model.id,
            "name": "Test Deferred WF",
        })
        cls.state_initial = State.create({
            "workflow_id": cls.workflow.id,
            "name": "Initial",
            "sequence": 10,
        })
        cls.state_booked = State.create({
            "workflow_id": cls.workflow.id,
            "name": "Booked",
            "sequence": 20,
        })

        cls.child_workflow = Workflow.create({
            "model_id": service_model.id,
            "name": "Test Child WF",
        })
        cls.child_state = State.create({
            "workflow_id": cls.child_workflow.id,
            "name": "Not Started",
            "sequence": 10,
        })

        cls.trans_book = Transition.create({
            "name": "Book",
            "workflow_id": cls.workflow.id,
            "from_state_id": cls.state_initial.id,
            "to_state_id": cls.state_booked.id,
            "action_id": default_action.id,
            "sequence": 10,
        })

        # Template with a deferred child
        cls.template = Service.create({
            "name": "Test Parent Template",
            "workflow_id": cls.workflow.id,
            "state_id": cls.state_initial.id,
            "is_this_a_template": True,
            "company_id": cls.env.company.id,
        })
        cls.child_template = Service.create({
            "name": "Deferred Child Template",
            "parent_id": cls.template.id,
            "workflow_id": cls.child_workflow.id,
            "state_id": cls.child_state.id,
            "create_on_state_id": cls.state_booked.id,
            "company_id": cls.env.company.id,
        })

        # Use res.partner as the generic subject
        cls.subject = cls.env.company.partner_id

    def _create_service_from_template(self):
        """Clone the template into a service instance linked to a subject."""
        service = self.env["riverflow.service"].with_context(
            default_res_model="res.partner",
            default_res_id=self.subject.id,
        )._create_services_from_template(self.template.id)
        return service

    def _fire_transition(self, service, transition):
        """Execute a transition through the full mixin flow.

        Calls _prepare_transition_action on the service to get the action
        context (which copies ALL service fields as default_* keys), then
        creates and executes the wizard in that context. This reproduces
        the real-world flow where the mixin's defaults leak into action_save.
        """
        action = service.with_context(
            transition_id=transition.id,
        )._prepare_transition_action()

        wizard_ctx = action["context"]
        wizard_ctx["active_model"] = "riverflow.service"
        wizard_ctx["active_ids"] = service.ids

        Wizard = self.env[action["res_model"]].with_context(**wizard_ctx)
        defaults = Wizard.default_get(Wizard.fields_get().keys())
        wizard = Wizard.create(defaults)
        wizard.action_save()

    def test_deferred_child_does_not_inherit_parent_supply_unit_price(self):
        """Context default for supply_unit_price must not leak to deferred children.

        The parent service has supply_unit_price=999. The transition mixin
        copies this as default_supply_unit_price=999 into context. The
        deferred child template has supply_unit_price=0 (the default).
        Without context isolation, the child would inherit 999 from context.
        """
        service = self._create_service_from_template()
        self.assertFalse(service.child_ids, "Deferred children skipped on initial clone")

        # Set a distinctive value on the parent — the mixin will copy it as
        # default_supply_unit_price into context when opening the wizard.
        service.supply_unit_price = 999.0

        self._fire_transition(service, self.trans_book)

        children = service.child_ids.filtered("active")
        self.assertEqual(len(children), 1, "Deferred child should be created")
        self.assertEqual(
            children[0].supply_unit_price,
            0.0,
            "Deferred child must get supply_unit_price from template (0), "
            "not from parent's leaked context default (999)",
        )

    def test_deferred_child_gets_correct_subject_link(self):
        """Deferred children must be linked to the same subject as the parent."""
        service = self._create_service_from_template()
        self._fire_transition(service, self.trans_book)

        children = service.child_ids.filtered("active")
        self.assertEqual(len(children), 1)
        self.assertEqual(
            children[0].res_model,
            "res.partner",
            "Deferred child should inherit res_model from parent",
        )
        self.assertEqual(
            children[0].res_id,
            self.subject.id,
            "Deferred child should inherit res_id from parent",
        )

    def test_deferred_clone_recurses_into_great_grandchildren(self):
        """Cloning a deferred subtree must walk the full template descendant
        tree, not just one level of grandchildren.

        Before this was fixed, `_create_deferred_children` cloned only the
        immediate children of the deferred template. Any template node living
        two or more levels below the deferred root was silently dropped on
        materialisation — making patterns like
        "AB Appointment (deferred) → AB Arrange Transport → Inform Employee
        Travel Plan" impossible to express.

        Setup: add a grandchild template under the existing deferred child,
        fire the booking transition, and assert the grandchild materialised.
        """
        Service = self.env["riverflow.service"]
        grandchild_template = Service.create(
            {
                "name": "Deferred Grandchild Template",
                "parent_id": self.child_template.id,
                "workflow_id": self.child_workflow.id,
                "state_id": self.child_state.id,
                "company_id": self.env.company.id,
            }
        )

        service = self._create_service_from_template()
        self._fire_transition(service, self.trans_book)

        children = service.child_ids.filtered("active")
        self.assertEqual(len(children), 1, "Deferred child should be cloned")
        grandchildren = children.child_ids.filtered("active")
        self.assertEqual(
            len(grandchildren),
            1,
            "Deferred grandchild template must clone recursively under the "
            "deferred child — recursive _clone_template_children is required",
        )
        self.assertEqual(grandchildren.name, grandchild_template.name)
        self.assertFalse(grandchildren.is_this_a_template)


@tagged("post_install", "-at_install", "riverflow", "test_deferred_children")
class TestDeferredChildrenTemplateLink(TransactionCase):
    """Deferred children are deduplicated by the template they came from,
    not by their name, and a cancelled child does not block a fresh one.

    The email step renames a child to its email subject, so a name match
    made a parent that came back to the state create a duplicate. A back
    step with cancel_children cancels the open work underneath; when the
    parent returns, it needs fresh children rather than the cancelled ones.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        Service = cls.env["riverflow.service"]
        State = cls.env["riverflow.state"]
        Workflow = cls.env["riverflow.workflow"]
        Transition = cls.env["riverflow.transition"]
        service_model = cls.env["ir.model"]._get("riverflow.service")
        default_action = cls.env.ref("riverflow.transition_action_default")

        workflow = Workflow.create({"model_id": service_model.id, "name": "Test Link WF"})
        cls.state_initial = State.create({"workflow_id": workflow.id, "name": "Initial", "sequence": 10})
        cls.state_waiting = State.create({"workflow_id": workflow.id, "name": "Waiting", "sequence": 20})
        cls.trans_wait = Transition.create({
            "name": "Wait",
            "from_state_id": cls.state_initial.id,
            "to_state_id": cls.state_waiting.id,
            "action_id": default_action.id,
            "sequence": 10,
        })
        cls.trans_wait_again = Transition.create({
            "name": "Wait again",
            "from_state_id": cls.state_waiting.id,
            "to_state_id": cls.state_waiting.id,
            "action_id": default_action.id,
            "sequence": 10,
        })
        cls.trans_back = Transition.create({
            "name": "Back",
            "from_state_id": cls.state_waiting.id,
            "to_state_id": cls.state_initial.id,
            "action_id": default_action.id,
            "sequence": 20,
            "action_context": "{'cancel_children': True}",
        })

        child_workflow = Workflow.create({"model_id": service_model.id, "name": "Test Link Child WF"})
        cls.child_open = State.create({"workflow_id": child_workflow.id, "name": "Open", "sequence": 10})
        cls.child_done = State.create({
            "workflow_id": child_workflow.id,
            "name": "Done",
            "sequence": 20,
            "is_end_state": True,
        })
        cls.child_cancelled = State.create({
            "workflow_id": child_workflow.id,
            "name": "Cancelled",
            "sequence": 30,
            "is_end_state": True,
            "is_cancelled_state": True,
        })

        cls.template = Service.create({
            "name": "Test Link Parent Template",
            "workflow_id": workflow.id,
            "state_id": cls.state_initial.id,
            "is_this_a_template": True,
            "company_id": cls.env.company.id,
        })
        cls.child_template_a = Service.create({
            "name": "Child A",
            "parent_id": cls.template.id,
            "workflow_id": child_workflow.id,
            "state_id": cls.child_open.id,
            "create_on_state_id": cls.state_waiting.id,
            "company_id": cls.env.company.id,
        })
        cls.child_template_b = Service.create({
            "name": "Child B",
            "parent_id": cls.template.id,
            "workflow_id": child_workflow.id,
            "state_id": cls.child_open.id,
            "create_on_state_id": cls.state_waiting.id,
            "company_id": cls.env.company.id,
        })
        cls.subject = cls.env.company.partner_id

    def _create_service(self):
        return self.env["riverflow.service"].with_context(
            default_res_model="res.partner",
            default_res_id=self.subject.id,
        )._create_services_from_template(self.template.id)

    def _fire(self, service, transition):
        action = service.with_context(transition_id=transition.id)._prepare_transition_action()
        ctx = dict(action["context"], active_model="riverflow.service", active_ids=service.ids)
        Wizard = self.env[action["res_model"]].with_context(**ctx)
        Wizard.create(Wizard.default_get(Wizard.fields_get().keys())).action_save()

    def _children(self, service):
        return service.child_ids.filtered("active")

    def test_link_and_dedup_by_template(self):
        """Every clone remembers its template. A renamed child still blocks a
        duplicate, and a parent with a new name still finds its template."""
        service = self._create_service()
        self.assertEqual(service.template_service_id, self.template)
        service.name = "Renamed by hand"

        self._fire(service, self.trans_wait)
        children = self._children(service)
        self.assertEqual(len(children), 2, "The template link finds the template of a renamed parent")
        self.assertEqual(children.template_service_id, self.child_template_a | self.child_template_b)

        children.filtered(lambda c: c.template_service_id == self.child_template_a).name = "Email subject"
        self._fire(service, self.trans_wait_again)
        self.assertEqual(self._children(service), children, "A self-loop and a renamed child create nothing new")

    def test_cancel_children_then_fresh_children(self):
        """A back step with cancel_children cancels the open children and
        leaves a Done child alone. Coming back creates a fresh child only for
        the cancelled one."""
        service = self._create_service()
        self._fire(service, self.trans_wait)
        child_a = self._children(service).filtered(lambda c: c.template_service_id == self.child_template_a)
        child_b = self._children(service) - child_a
        child_a.state_id = self.child_done

        self._fire(service, self.trans_back)
        self.assertEqual(child_a.state_id, self.child_done, "A Done child stays Done")
        self.assertEqual(child_b.state_id, self.child_cancelled, "An open child is cancelled")

        self._fire(service, self.trans_wait)
        new_children = self._children(service) - child_a - child_b
        self.assertEqual(len(new_children), 1, "Only the cancelled child is cloned again")
        self.assertEqual(new_children.template_service_id, self.child_template_b)
        self.assertEqual(new_children.state_id, self.child_open)

    def test_back_without_cancel_children_leaves_children_open(self):
        """Without the key, a back step does not touch the children."""
        self.trans_back.action_context = "{}"
        service = self._create_service()
        self._fire(service, self.trans_wait)
        self._fire(service, self.trans_back)
        self.assertEqual(self._children(service).state_id, self.child_open)
