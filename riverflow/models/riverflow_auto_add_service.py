from odoo import api, fields, models, tools
from odoo.fields import Domain
from odoo.tools.misc import clean_context
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

    @staticmethod
    def _split_top_and_leaves(tokens):
        """Extract top-level implicit/explicit AND leaves from normalized Domain tokens.

        Returns ``(leaves, has_complex, mentions_active)``. Any OR (``|``) or
        NOT (``!``) sub-expression sets ``has_complex = True`` while top-level
        AND siblings are still collected for safe pre-filtering.
        """
        leaves = []
        has_complex = False
        mentions_active = any(
            isinstance(t, (tuple, list)) and len(t) == 3 and t[0] == "active"
            for t in tokens
        )
        n = len(tokens)

        def _skip(idx):
            if idx >= n:
                return n
            tok = tokens[idx]
            if tok in ("&", "|"):
                return _skip(_skip(idx + 1))
            if tok == "!":
                return _skip(idx + 1)
            return idx + 1

        def _consume(idx):
            nonlocal has_complex
            if idx >= n:
                return n
            tok = tokens[idx]
            if tok == "&":
                return _consume(_consume(idx + 1))
            if tok == "|":
                has_complex = True
                return _skip(_skip(idx + 1))
            if tok == "!":
                has_complex = True
                return _skip(idx + 1)
            if isinstance(tok, (tuple, list)) and len(tok) == 3:
                leaves.append(tok)
            else:
                has_complex = True
            return idx + 1

        idx = 0
        while idx < n:
            idx = _consume(idx)
        return leaves, has_complex, mentions_active

    @staticmethod
    def _compile_simple_leaf(model_fields, leaf):
        """Compile a simple stored scalar/Many2one domain leaf into a Python predicate.

        Returns a callable ``predicate(record) -> bool``, or ``None`` if the leaf
        should be left to SQL (relational dotted paths, unstored computes,
        display_name string searches, NULL-ambiguous operators, etc.).
        """
        field_name, op, value = leaf
        if leaf in ((1, "=", 1), [1, "=", 1]):
            return lambda _rec: True
        if leaf in ((0, "=", 1), [0, "=", 1]):
            return lambda _rec: False
        if not isinstance(field_name, str) or "." in field_name:
            return None
        field = model_fields.get(field_name)
        if not field or not field.store:
            return None

        ftype = field.type
        if ftype == "boolean":
            if op == "=" and (isinstance(value, bool) or value is None):
                target = bool(value)
                return lambda rec, f=field_name, t=target: bool(rec[f]) is t
            if op == "!=" and (isinstance(value, bool) or value is None):
                target = bool(value)
                return lambda rec, f=field_name, t=target: bool(rec[f]) is not t
            return None

        if ftype == "many2one":
            if op in ("=", "!="):
                if value is False or value is None:
                    if op == "=":
                        return lambda rec, f=field_name: not rec[f].id
                    return lambda rec, f=field_name: bool(rec[f].id)
                if isinstance(value, int) and not isinstance(value, bool):
                    if op == "=":
                        return lambda rec, f=field_name, v=value: rec[f].id == v
                    return lambda rec, f=field_name, v=value: rec[f].id != v
                return None
            if op == "in" and isinstance(value, (list, tuple, set)):
                ids = set()
                allow_false = False
                for item in value:
                    if item is False or item is None:
                        allow_false = True
                    elif isinstance(item, int) and not isinstance(item, bool):
                        ids.add(item)
                    else:
                        return None
                if allow_false:
                    return lambda rec, f=field_name, s=ids: (not rec[f].id) or (rec[f].id in s)
                return lambda rec, f=field_name, s=ids: bool(rec[f].id) and (rec[f].id in s)
            return None

        if ftype in ("char", "selection"):
            if op == "=" and isinstance(value, str) and value:
                return lambda rec, f=field_name, v=value: rec[f] == v
            if op == "in" and isinstance(value, (list, tuple, set)) and value:
                if all(isinstance(v, str) and v for v in value):
                    val_set = set(value)
                    return lambda rec, f=field_name, s=val_set: rec[f] in s
            return None

        if ftype in ("date", "datetime"):
            if op in ("=", "!=") and (value is False or value is None):
                if op == "=":
                    return lambda rec, f=field_name: not rec[f]
                return lambda rec, f=field_name: bool(rec[f])
            if op in ("=", "<", "<=", ">", ">=") and value:
                try:
                    target = (
                        fields.Date.to_date(value)
                        if ftype == "date"
                        else fields.Datetime.to_datetime(value)
                    )
                except Exception:
                    return None
                if target is None:
                    return None
                if op == "=":
                    return lambda rec, f=field_name, t=target: bool(rec[f]) and rec[f] == t
                if op == "<":
                    return lambda rec, f=field_name, t=target: bool(rec[f]) and rec[f] < t
                if op == "<=":
                    return lambda rec, f=field_name, t=target: bool(rec[f]) and rec[f] <= t
                if op == ">":
                    return lambda rec, f=field_name, t=target: bool(rec[f]) and rec[f] > t
                if op == ">=":
                    return lambda rec, f=field_name, t=target: bool(rec[f]) and rec[f] >= t
            return None

        return None

    def _match_subjects_for_domain(self, model_name, subjects_sudo, domain):
        """Return set of subject IDs matching ``domain`` on ``model_name``.

        Evaluates top-level AND simple stored predicates in Python from the ORM
        cache first:
        - If all leaves are simple, returns immediately with zero SQL queries.
        - If pre-filtering leaves zero candidates, short-circuits with zero SQL.
        - Otherwise falls back to a single SQL search limited to the surviving
          candidate IDs.
        """
        Model = self.env[model_name].sudo()
        tokens = list(domain)
        leaves, has_complex, mentions_active = self._split_top_and_leaves(tokens)

        predicates = []
        if (
            not mentions_active
            and "active" in Model._fields
            and self.env.context.get("active_test", True)
        ):
            predicates.append(lambda rec: bool(rec["active"]))

        for leaf in leaves:
            pred = self._compile_simple_leaf(Model._fields, leaf)
            if pred is None:
                has_complex = True
            else:
                predicates.append(pred)

        candidates = subjects_sudo
        for pred in predicates:
            candidates = candidates.filtered(pred)
            if not candidates:
                return set()

        if not has_complex:
            return set(candidates.ids)

        combined_domain = tokens + [("id", "in", candidates.ids)]
        return set(Model.search(combined_domain).ids)

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
        if not auto_add_rules:
            return

        eval_context = self._eval_context()
        # Prefetch subjects once as sudo for cached predicate checks
        subjects_sudo = self.env[model].sudo().browse(subjects.ids)

        # Parse and deduplicate rule domains: many rules share identical domain
        # strings (e.g. 27 log-entry rules share ~10 distinct domains), so
        # evaluating each unique domain once avoids redundant SQL queries.
        rule_domains = {}
        unique_domains = {}
        for auto_add_rule in auto_add_rules:
            domain_str = auto_add_rule.domain
            if domain_str in unique_domains:
                rule_domains[auto_add_rule] = domain_str
                continue
            try:
                domain = safe_eval(domain_str, eval_context)
                unique_domains[domain_str] = Domain(domain)
                rule_domains[auto_add_rule] = domain_str
            except Exception as e:
                raise ValidationError(f"Error evaluating domain for rule {auto_add_rule.name}: {e}")

        domain_matching_ids = {
            domain_str: self._match_subjects_for_domain(model, subjects_sudo, domain)
            for domain_str, domain in unique_domains.items()
        }

        new_services = []
        for subject in subjects.sorted("id"):
            sid = subject.id
            for auto_add_rule, domain_str in rule_domains.items():
                if sid in domain_matching_ids[domain_str]:
                    new_services.append(
                        {
                            "res_id": sid,
                            "res_model": subject._name,
                            "created_by_auto_add_service_id": auto_add_rule.id,
                            "template_id": auto_add_rule.service_template_id.id,
                        }
                    )

        if not new_services:
            return

        # Hook for filtering candidates before dedup (e.g. credential plan-item check)
        new_services = self._filter_auto_add_candidates(new_services, model)
        if not new_services:
            return

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
                    # A service a compute retired is not a handled occasion.
                    ("to_be_deleted", "=", False),
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
        if not to_create:
            return

        # A clean context: the services are made with this rule's defaults only
        # (radar pipeline applicants plan, decision 87). The auto-add often runs
        # inside the save of a step screen, whose context carries the open
        # service's own values as default_* keys (riverflow.transition.mixin
        # copies every field the wizard declares). A new service took every such
        # value its template clone does not set, and a value given at create
        # never runs the compute: on 7-Oct-2026 the next work permit task got the
        # due date of the task whose step uploaded the new permit (radar service
        # 37725), two years too early.
        Service = self.env["riverflow.service"].with_context(clean_context(self.env.context))
        specs = [
            (service_vals["template_id"], self._get_service_creation_context(service_vals), False)
            for service_vals in to_create
        ]
        Service._create_services_from_templates_batch(specs)

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
