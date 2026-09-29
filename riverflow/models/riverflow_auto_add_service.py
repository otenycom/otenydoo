from odoo import api, fields, models, tools
from odoo.fields import Domain
from odoo.tools.safe_eval import safe_eval
import logging
from odoo.exceptions import ValidationError


_logger = logging.getLogger(__name__)


class AutoAddService(models.Model):
    _name = "riverflow.auto.add.service"
    _description = "Auto Add Service"
    _order = "applies_to_model, template_relative_timing desc, template_daily_order"

    name = fields.Char(string="Name", compute="_compute_name", store=True, index=True)
    active = fields.Boolean(
        default=True,
        help="If unchecked, the auto-add rule will be disabled without removing it.",
    )
    domain_id = fields.Many2one(
        "riverflow.named.domain",
        string="Domain Condition",
        required=True,
        ondelete="restrict",
    )
    applies_to_model_id = fields.Many2one(
        related="domain_id.applies_to_model_id",
        store=True,
        readonly=True,
    )
    applies_to_model = fields.Char(
        related="domain_id.applies_to_model",
        store=True,
        index=True,
    )
    domain = fields.Text(
        related="domain_id.domain",
        readonly=True,
    )
    service_template_id = fields.Many2one(
        "riverflow.service",
        string="Service Template",
        required=True,
        domain=[("is_this_a_template", "=", True)],
        help="Template that will be copied to create the auto-added service",
    )
    template_relative_timing = fields.Char(
        related="service_template_id.relative_timing_formatted",
        string="Template Timing",
        store=True,
        readonly=True,
    )
    template_daily_order = fields.Integer(
        related="service_template_id.daily_prio",
        string="Priority",
        help="Sort order for services that have the same deadline",
        store=True,
        readonly=True,
    )

    @api.depends("service_template_id.name")
    def _compute_name(self):
        for record in self:
            if record.service_template_id:
                record.name = f"Auto-add: {record.service_template_id.name}"
            else:
                record.name = "New Auto Add Service"

    @api.model
    def _eval_context(self):
        return {
            "user": self.env.user.with_context({}),
            "time": tools.safe_eval.time,
            "datetime": tools.safe_eval.datetime,
            "company_ids": self.env.companies.ids,
            "company_id": self.env.company.id,
            "ref": self.env.ref,
        }

    def _filter_auto_add_candidates(self, new_services, model):
        """Hook for filtering auto-add candidates before dedup.

        Called between candidate building and dedup. Override in inheriting
        modules to add filtering logic (e.g. credential plan-item check).
        """
        return new_services

    @api.model
    def auto_add_services(self, subjects):
        if subjects and isinstance(subjects[0].id, api.NewId):
            """Because of the fake id in form view, we need to return
            todo: review if we can use .add() and .new() on the many2one fields in the sync below to also make this work in form view
            """
            return

        """Check rules and add services to matching records."""
        if not subjects:
            return

        model = subjects[0]._name

        """
        TODO: also make this a log_entry.applicable_auto_add_service_ids field, so that log entry services_ids can take 
        a  dependency on applicable_auto_add_service_ids.domain to make this more responsive
        """
        # Force active_test=True so deactivated rules are never evaluated,
        # even when the calling compute method runs with active_test=False
        # leaked from the ORM's trigger traversal context.
        auto_add_rules = self.with_context(active_test=True).search(
            [
                ("applies_to_model", "=", model),
            ]
        )

        eval_context = self._eval_context()
        new_services = []
        # Prefetch the records to avoid multiple database hits
        self.env[model].browse(subjects.ids)

        # Pre-process domains for all auto_add_rules
        rule_domains = {}
        for auto_add_rule in auto_add_rules:
            try:
                domain = safe_eval(auto_add_rule.domain, eval_context)
                rule_domains[auto_add_rule] = Domain(domain)
            except Exception as e:
                raise ValidationError(f"Error evaluating domain for rule {auto_add_rule.name}: {e}")

        # Performance optimization: batch-evaluate all subjects per rule using search()
        # instead of filtered_domain() per subject. This reduces O(subjects x rules) Python
        # domain evaluations to O(rules) database queries, dramatically improving performance.
        rule_matching_ids = {}
        for auto_add_rule, domain in rule_domains.items():
            # Combine rule domain with subject filter - single DB query per rule
            # Domain objects are iterable ASTs, so list() converts to traditional list format
            combined_domain = list(domain) + [("id", "in", subjects.ids)]
            matching = self.env[model].sudo().search(combined_domain)
            rule_matching_ids[auto_add_rule] = set(matching.ids)

        # Now iterate without expensive per-record domain evaluation
        for subject in subjects:
            for auto_add_rule in rule_domains:
                if subject.id in rule_matching_ids[auto_add_rule]:
                    new_services.append(
                        {
                            "res_id": subject.id,
                            "res_model": subject._name,
                            "created_by_auto_add_service_id": auto_add_rule.id,
                            "template_id": auto_add_rule.service_template_id.id,
                        }
                    )

        # Hook for filtering candidates before dedup (e.g. credential plan-item check)
        new_services = self._filter_auto_add_candidates(new_services, model)

        # Get existing auto-added services to avoid duplicates.
        # Dedup key is (rule_id, res_model, res_id, context_ref) so the same rule
        # can create multiple services for different occasions (e.g. initial vs
        # renewal credentials). Candidates are grouped by THEIR res_model because
        # a filter hook may have redirected them to a different subject than the
        # trigger model (e.g. credential-subject rules anchor the service on the
        # credential's holder) — searching only the trigger model would let
        # redirected candidates escape dedup on every re-evaluation.
        # context_ref defaults to False for backward compatibility with rules that don't use it.
        candidate_ids_by_model = {}
        for ns in new_services:
            candidate_ids_by_model.setdefault(ns["res_model"], set()).add(ns["res_id"])

        # On a workflow that enforces single-open, a CLOSED service (end state,
        # or archived) never counts as "already handled": the presence side of
        # that invariant says an in-scope subject always holds one open
        # service, so a Done task for the current occasion must not block a
        # fresh monitoring task. Without this, filing the passport already on
        # file through the Renew Passport task closed it and left the employee
        # with no task at all (10-Sep-2026). Only open services block here, and
        # _filter_single_open below already dedups on open-ness. Workflows
        # without single-open keep the full dedup: a hand-in service keyed on
        # a specific card must not return when its lifecycle state is
        # re-entered.
        Service = self.with_context(active_test=False).env["riverflow.service"]
        current_service_keys = set()
        for candidate_model, candidate_ids in candidate_ids_by_model.items():
            for s in Service.search(
                [
                    ("res_id", "in", list(candidate_ids)),
                    ("res_model", "=", candidate_model),
                    ("created_by_auto_add_service_id", "!=", False),
                ]
            ):
                if s.workflow_id.enforce_single_open and not s.is_open:
                    continue
                current_service_keys.add(
                    (
                        s.created_by_auto_add_service_id.id,
                        s.res_model,
                        s.res_id,
                        s.auto_add_context_ref or False,
                    )
                )

        # Services to create (candidates without an existing service for their key)
        # Only create services that don't already exist - no deactivation or reactivation
        to_create = [
            ns
            for ns in new_services
            if (
                ns["created_by_auto_add_service_id"],
                ns["res_model"],
                ns["res_id"],
                ns.get("auto_add_context_ref") or False,
            )
            not in current_service_keys
        ]

        # Single-open guard: for workflows that enforce one open service per
        # subject, drop any candidate whose subject already has an open service
        # for that workflow, and collapse duplicate candidates within this batch.
        # This is the application layer of the invariant; a partial-unique index
        # on enforcing workflows is the structural backstop against races.
        to_create = self._filter_single_open(to_create)

        if to_create:
            for service_vals in to_create:
                ctx = self._get_service_creation_context(service_vals)
                service_context = self.env["riverflow.service"].with_context(**ctx)
                service_context._create_services_from_template(service_vals["template_id"])

    def _filter_single_open(self, to_create):
        """Drop candidates that would create a second OPEN service for a
        subject on a workflow that enforces single-open, and collapse duplicate
        candidates within this batch (same workflow + subject).

        Keyed on workflow open-ness (riverflow.service.is_open), not the
        occasion ref, so a renewal cycle never stacks a second service on an
        already-open one, and a manually-created open service equally blocks
        auto-creation. Non-enforcing workflows are returned unchanged.

        Like the dedup above, existing open services are looked up per
        candidate res_model (not per trigger model) so redirected candidates
        are checked against their actual subject.
        """
        if not to_create:
            return to_create
        Service = self.env["riverflow.service"]
        template_ids = {c["template_id"] for c in to_create}
        workflow_by_template = {
            t.id: t.workflow_id for t in Service.browse(template_ids)
        }
        enforcing = {
            wf.id
            for wf in workflow_by_template.values()
            if wf and wf.enforce_single_open
        }
        if not enforcing:
            return to_create

        candidate_ids_by_model = {}
        for c in to_create:
            candidate_ids_by_model.setdefault(c["res_model"], set()).add(c["res_id"])

        open_existing = set()
        for candidate_model, candidate_ids in candidate_ids_by_model.items():
            for s in Service.with_context(active_test=False).search(
                [
                    ("res_id", "in", list(candidate_ids)),
                    ("res_model", "=", candidate_model),
                    ("workflow_id", "in", list(enforcing)),
                    ("is_open", "=", True),
                ]
            ):
                open_existing.add((s.workflow_id.id, s.res_model, s.res_id))

        result = []
        seen = set()
        for c in to_create:
            wf = workflow_by_template.get(c["template_id"])
            if wf and wf.id in enforcing:
                key = (wf.id, c["res_model"], c["res_id"])
                if key in open_existing or key in seen:
                    continue
                seen.add(key)
            result.append(c)
        return result

    def _get_service_creation_context(self, service_vals):
        """Build context dict for creating a service from auto-add vals.

        Override in inheriting modules to inject additional default fields
        (e.g. credential_plan_item_id in rivercreds).
        """
        return {
            "default_res_id": service_vals["res_id"],
            "default_res_model": service_vals["res_model"],
            "default_created_by_auto_add_service_id": service_vals["created_by_auto_add_service_id"],
            "default_auto_add_context_ref": service_vals.get("auto_add_context_ref"),
        }
