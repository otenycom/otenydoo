from odoo.tests.common import TransactionCase
from odoo.tests import tagged


@tagged("post_install", "-at_install", "riverflow", "test_auto_add")
class TestAutoAddServiceDedup(TransactionCase):
    """Test that auto_add_services ignores inactive rules even when
    active_test=False leaks into the environment context.

    The ORM sets active_test=False during trigger traversal
    (models.py _modified -> _modified_triggers). This context can leak
    into compute methods, causing searches to return inactive records.
    The auto_add_services rule search must be immune to this leak.
    """

    @classmethod
    def setUpClass(cls):
        super().setUpClass()

        # Use res.partner as a simple subject model that exists everywhere
        cls.partner_model = cls.env["ir.model"].search(
            [("model", "=", "res.partner")], limit=1
        )

        # Create a domain condition that matches partners with a specific name
        cls.auto_add_domain = cls.env["riverflow.named.domain"].create(
            {
                "name": "Test domain for auto-add dedup",
                "applies_to_model_id": cls.partner_model.id,
                "domain": "[('name', 'ilike', 'AutoAddTestSubject')]",
            }
        )

        # Create a service template
        cls.service_template = cls.env["riverflow.service"].create(
            {
                "name": "Test Auto-Add Template",
                "is_this_a_template": True,
            }
        )

        # Create the auto-add rule
        cls.auto_add_rule = cls.env["riverflow.auto.add.service"].create(
            {
                "domain_id": cls.auto_add_domain.id,
                "service_template_id": cls.service_template.id,
            }
        )

        # Create a matching subject partner
        cls.subject_partner = cls.env["res.partner"].create(
            {"name": "AutoAddTestSubject"}
        )

    def _count_services_for_subject(self):
        return self.env["riverflow.service"].with_context(active_test=False).search_count(
            [
                ("res_id", "=", self.subject_partner.id),
                ("res_model", "=", "res.partner"),
                ("created_by_auto_add_service_id", "!=", False),
            ]
        )

    def test_auto_add_creates_service(self):
        """Baseline: auto_add_services creates a service for a matching subject."""
        AutoAdd = self.env["riverflow.auto.add.service"]
        subjects = self.subject_partner

        AutoAdd.auto_add_services(subjects)

        self.assertEqual(
            self._count_services_for_subject(),
            1,
            "auto_add_services should create exactly one service",
        )

    def test_auto_add_dedup_prevents_duplicate(self):
        """The dedup check prevents creating the same service twice."""
        AutoAdd = self.env["riverflow.auto.add.service"]
        subjects = self.subject_partner

        AutoAdd.auto_add_services(subjects)
        AutoAdd.auto_add_services(subjects)

        self.assertEqual(
            self._count_services_for_subject(),
            1,
            "Running auto_add_services twice should still produce one service",
        )

    def test_inactive_rule_ignored_with_active_test_false(self):
        """Deactivated rules must not create services, even when
        active_test=False leaks into the environment from the ORM's
        trigger traversal (the root cause of the A1 duplicate bug)."""
        AutoAdd = self.env["riverflow.auto.add.service"]
        subjects = self.subject_partner

        # Let the rule create the initial service
        AutoAdd.auto_add_services(subjects)
        self.assertEqual(self._count_services_for_subject(), 1)

        # Deactivate the rule (simulates the consolidation hook archiving the old rule)
        self.auto_add_rule.active = False

        # Simulate the ORM context leak: call auto_add_services with active_test=False
        AutoAdd.with_context(active_test=False).auto_add_services(subjects)

        self.assertEqual(
            self._count_services_for_subject(),
            1,
            "Inactive rule must not create a duplicate service even when "
            "active_test=False leaks into the context",
        )

    def test_inactive_rule_ignored_with_second_active_rule(self):
        """When a renamed rule creates a second DB record (same logical rule,
        different id), deactivating the old rule and having active_test=False
        leak must not create a duplicate via the old rule.

        This reproduces the exact A1 bug scenario: rule 9 (old, inactive) and
        rule 46 (new, active) both match, but the dedup key (rule_id, res_id)
        only catches rule 46's match because service was created by rule 46."""
        AutoAdd = self.env["riverflow.auto.add.service"]
        subjects = self.subject_partner

        # Create a second rule pointing to the same domain (simulates the XML ID rename)
        new_rule = self.env["riverflow.auto.add.service"].create(
            {
                "domain_id": self.auto_add_domain.id,
                "service_template_id": self.service_template.id,
            }
        )

        # Let the new rule create the initial service
        AutoAdd.auto_add_services(subjects)
        # Both rules matched, two services created (one per rule)
        self.assertEqual(self._count_services_for_subject(), 2)

        # Delete one of the two services and deactivate old rule
        # (simulates the consolidation: reassign service, deactivate old rule)
        old_rule_service = self.env["riverflow.service"].search(
            [
                ("res_id", "=", self.subject_partner.id),
                ("res_model", "=", "res.partner"),
                ("created_by_auto_add_service_id", "=", self.auto_add_rule.id),
            ]
        )
        old_rule_service.unlink()
        self.auto_add_rule.active = False
        self.assertEqual(self._count_services_for_subject(), 1)

        # Now simulate the context leak
        AutoAdd.with_context(active_test=False).auto_add_services(subjects)

        self.assertEqual(
            self._count_services_for_subject(),
            1,
            "Deactivated old rule must not create a service even when "
            "active_test=False is in context and a newer active rule exists",
        )

    def _make_workflow_rule(self, name):
        """A rule on its own two-state workflow (Open -> Done), so a service can
        be closed and the dedup observed against a closed one."""
        service_model = self.env["ir.model"]._get("riverflow.service").id
        workflow = self.env["riverflow.workflow"].create(
            {"name": name, "model_id": service_model}
        )
        open_state = self.env["riverflow.state"].create(
            {"name": "Open", "workflow_id": workflow.id, "sequence": 10}
        )
        self.env["riverflow.state"].create(
            {
                "name": "Done",
                "workflow_id": workflow.id,
                "sequence": 20,
                "is_end_state": True,
            }
        )
        template = self.env["riverflow.service"].create(
            {
                "name": name + " template",
                "is_this_a_template": True,
                "workflow_id": workflow.id,
                "state_id": open_state.id,
            }
        )
        rule = self.env["riverflow.auto.add.service"].create(
            {"domain_id": self.auto_add_domain.id, "service_template_id": template.id}
        )
        return workflow, rule

    def _services_of_rule(self, rule):
        return self.env["riverflow.service"].with_context(active_test=False).search(
            [
                ("res_id", "=", self.subject_partner.id),
                ("res_model", "=", "res.partner"),
                ("created_by_auto_add_service_id", "=", rule.id),
            ]
        )

    def test_closed_service_blocks_only_without_single_open(self):
        """A closed service and the occasion dedup.

        Without single-open the old rule stands: a Done service for the same
        key is "already handled" and nothing is re-created (the hand-in
        services keyed on one card rely on this when a lifecycle state is
        re-entered). With single-open, the presence side of the invariant
        wins: a closed service never blocks, only an open one does — an
        in-scope subject always holds exactly one open service. Incident
        10-Sep-2026: a Renew Passport task closed by a re-upload of the
        passport already on file left the employee with no task at all.
        """
        AutoAdd = self.env["riverflow.auto.add.service"]
        # The shared fixture rule also matches this subject; leave it be and
        # count only this test's rule.
        self.auto_add_rule.active = False
        workflow, rule = self._make_workflow_rule("Dedup vs closed")
        done = workflow.state_ids.filtered("is_end_state")

        AutoAdd.auto_add_services(self.subject_partner)
        first = self._services_of_rule(rule)
        self.assertEqual(len(first), 1)
        first.state_id = done
        self.assertFalse(first.is_open)

        AutoAdd.auto_add_services(self.subject_partner)
        self.assertEqual(
            len(self._services_of_rule(rule)), 1,
            "without single-open a closed service still counts as handled",
        )

        workflow.enforce_single_open = True
        AutoAdd.auto_add_services(self.subject_partner)
        services = self._services_of_rule(rule)
        self.assertEqual(
            len(services), 2,
            "with single-open a closed service never blocks: the subject gets "
            "its open service back",
        )
        self.assertEqual(len(services.filtered("is_open")), 1)

        AutoAdd.auto_add_services(self.subject_partner)
        self.assertEqual(
            len(self._services_of_rule(rule)), 2,
            "the open service blocks; nothing stacks on it",
        )

