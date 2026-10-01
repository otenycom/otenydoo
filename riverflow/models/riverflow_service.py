import logging
from datetime import date, datetime, timedelta

from markupsafe import Markup

from odoo import Command, _, api, fields, models
from odoo.exceptions import UserError, ValidationError

_logger = logging.getLogger(__name__)


class Service(models.Model):
    _name = "riverflow.service"
    # no activities 'mail.activity.mixin', we use workflow buttons instead.
    _inherit = [
        # "mail.thread",
        "riverflow.mail.thread.review.mixin",
        "riverflow.state.mixin",
        "riverflow.state.record.tracker.mixin",
        #        "oteny.audit.mixin",
    ]
    _description = "Service"
    _parent_name = "parent_id"
    _parent_store = True
    _rec_name = "display_name"  # ensure default search is on display_name.
    # Services are a recursive tree, and in order to show the tree correctly in the flat
    # list view, we assign a display_order numer for all child services. For performance, we don't
    # set the display_order field to all services on any service update, so the root services are not sorted
    # by display_order, but by name.
    _order = "res_sortable_name,res_model,res_id,root_name,root_id,display_order"
    DATE_FORMAT = "%d-%b-%y"  # 01-Jan-21; dont use %-d-%b-%y" to remove the leading zero, as it also triggers french locale format on odoo.sh
    DATETIME_FORMAT = "%d-%b-%y %H:%M:%S"

    # auto calculated by Odoo in the form of parent_id/parent_id/self_id/
    # see def _get_domain_locations(self)
    parent_path = fields.Char(index="btree")
    indent_level = fields.Integer("Indent level", compute="_compute_indent_level", store=True, recursive=True)
    parent_id = fields.Many2one("riverflow.service", string="Parent Service", index=True, ondelete="cascade")
    child_ids = fields.One2many("riverflow.service", "parent_id", string="Child Services")
    descendant_ids = fields.One2many(
        "riverflow.service",
        "parent_id",
        string="Descendant Services",
        compute="_compute_descendant_ids",
        store=False,
    )
    created_by_auto_add_service_id = fields.Many2one(
        "riverflow.auto.add.service",
        string="Created by Auto Add Rule",
        help="The auto add rule that created this service. The system can use this to determine if the rule that created the service is still applicable.",
    )
    auto_add_context_ref = fields.Char(
        string="Auto-Add Context Reference",
        index=True,
        help="Contextual key for dedup of auto-added services. Distinguishes "
        "different occasions for the same auto-add rule on the same subject. "
        "For example, 'missing' for initial credential arrangement vs "
        "'cred:42' for renewal of a specific expiring credential.",
    )

    is_this_a_template = fields.Boolean(
        string="Is This a Template",
        default=False,
        help="Applies to Top-level services only. If checked, this service and its descendants will be used as a template for creating new services",
    )
    is_root_a_template = fields.Boolean(
        string="Is Root a Template",
        compute="_compute_is_root_a_template",
        store=True,
        help="Technical so we always treat the full tree of a service as a template, even if the children are not flagged astemplates themselves",
    )
    only_add_children = fields.Boolean(
        string="Only Add Children",
        default=False,
        help="If checked, only the children of this template will be added when creating a new service from this template",
        recursive=True,
    )
    can_be_root_service = fields.Boolean(
        string="Can be root service",
        default=True,
        help="Template setting: when unchecked, this template cannot be instantiated "
        "as a top-level (root) service. Useful for deferred child templates that "
        "should only exist under a parent.",
    )
    can_be_child_service = fields.Boolean(
        string="Can be child service",
        default=True,
        help="Template setting: when unchecked, this template cannot be added as a "
        "child of another service. Useful for complex workflows (A1, Work Permit) "
        "that must always be root-level on their subject.",
    )
    allowed_subject_model_ids = fields.Many2many(
        "ir.model",
        "riverflow_service_allowed_subject_model_rel",
        "service_id",
        "model_id",
        string="Allowed subjects",
        help="Template setting: restrict which subject types this template can be "
        "attached to. Empty means any subject is allowed. For example, set to "
        "'Logbook Entry' for crew-change templates that require a log entry context.",
    )
    create_on_state_id = fields.Many2one(
        "riverflow.state",
        string="Create on State",
        help="Template children only. When set, this child is NOT cloned during "
        "initial template cloning. Instead, it is automatically cloned when "
        "the parent service transitions to this state. This enables deferred "
        "child creation -- e.g. transport/notification children that should "
        "only exist once an appointment is booked.",
    )
    mail_template_id = fields.Many2one(
        "mail.template",
        string="Email Template",
        domain="[('model_id', '=', 'riverflow.service')]",
        help="The Send Email workflow transition uses this email template. "
        "Templates can be created in Odoo's Email Templates module.",
    )

    add_operator_as_recipient = fields.Boolean(
        string="Send to operator",
        default=False,
        help="The recipients for the email can also be set by adding followers to the chatter",
    )
    # One open child per bounced mail.message. Several notifications on that
    # message share this link, so a second bounce address updates the same
    # service instead of opening another one.
    bounced_mail_message_id = fields.Many2one(
        "mail.message",
        string="Bounced message",
        index=True,
        copy=False,
        ondelete="set null",
        help="The outgoing message whose bounce opened this service. "
        "Several notifications on one message share one open service.",
    )

    root_id = fields.Many2one("riverflow.service", compute="_compute_root_id", store=True, recursive=True)
    root_name = fields.Char(
        "Top-level service name",
        compute="_compute_root_name",
        help="Name of the top-level service, for sorting the list of services",
        store=True,
        index=True,
        recursive=True,
    )
    name = fields.Char(
        "Service Name",
        index="trigram",
        required=True,
        tracking=True,
    )
    sortable_name = fields.Char(
        "Sortable Name",
        compute="_compute_sortable_name",
        store=True,
    )

    indented_name = fields.Char("Service", compute="_compute_indented_name", store=False, recursive=True)
    display_name = fields.Char(
        "Display Name",
        compute="_compute_display_name",
        store=True,
        index="trigram",
        recursive=True,
    )
    company_id = fields.Many2one(
        "res.company",
        string="Company",
        compute="_compute_company_id",
        inverse="_inverse_company_id",
        recursive=True,
        store=True,
        required=False,
        index=True,
        tracking=True,
    )

    active = fields.Boolean(
        compute="_compute_active",
        inverse="_inverse_active",
        store=True,
        default=True,
        help="Set active to false to archive the service",
        tracking=True,
    )

    is_open = fields.Boolean(
        "Open",
        compute="_compute_is_open",
        store=True,
        help="True when the service is a live, in-progress service: active, not "
        "a template, and not in an end state. Backs the one-open-service-per-"
        "subject invariant (see riverflow.workflow.enforce_single_open) and the "
        "partial-unique index on enforcing workflows.",
    )

    @api.depends("active", "is_this_a_template", "state_id", "state_id.is_end_state")
    def _compute_is_open(self):
        for service in self:
            service.is_open = bool(
                service.active
                and not service.is_this_a_template
                and service.state_id
                and not service.state_id.is_end_state
            )

    days_relative_to_project = fields.Integer(
        "Day",
        help="Number of days before or after the project deadline for this service to be completed, e.g. -1 for the day before",
        required=False,
        tracking=True,
    )

    use_project_deadline_from = fields.Selection(
        [
            ("self", "Self"),
            ("root", "Top-level service"),
            ("root_appointment", "Top-level appointment"),
            ("creation", "Creation Date"),
        ],
        string="Deadline From",
        required=True,
        tracking=True,
        default="self",
    )

    subject_from = fields.Selection(
        [
            ("default", "Default"),
        ],
        string="Subject From",
        required=True,
        tracking=True,
        default="default",
        help="Where this service derives its subject (res_model/res_id) from. "
        "'Default' cascades from the parent service (top-down); a service with no "
        "parent keeps the subject it was created with. Higher-layer modules can "
        "add more options (e.g. derive from a related record).",
    )

    use_project_deadline_from_options = fields.Json(compute="_compute_use_project_deadline_from_options")

    is_days_relative_to_project_applicable = fields.Boolean(
        "Use relative days",
        compute="_compute_is_days_relative_to_project_applicable",
        help="Whether this service should use relative days to calculate its deadline",
        store=True,
    )

    weekend_deadline_rule = fields.Selection(
        [
            ("allow", "Allow weekend"),
            ("friday_before", "Bring to Friday"),
            ("monday_after", "Delay to Monday"),
        ],
        string="Weekend",
        default="allow",
        required=True,
        help="When a computed deadline falls on Saturday or Sunday: "
        "Allow keeps it, Bring to Friday shifts to the preceding Friday, "
        "Delay to Monday shifts to the following Monday.",
    )

    # This field is either set manually (if use_project_deadline_from is set to 'self')
    # or it is set to the project_deadline of the root service, or some other related entity in an inherited class
    project_deadline = fields.Date(
        "Project deadline",
        help="The services are timed relative to this deadline",
        tracking=True,
        compute="_compute_project_deadline",
        inverse="_inverse_project_deadline",
        store=True,
        recursive=True,
    )
    root_service_deadline = fields.Date(
        "Top-level deadline",
        related="root_id.deadline",
        help="Deadline of the top-level parent of this service",
        store=False,
        index=True,
        recursive=True,
    )
    root_service_appointment_date = fields.Date(
        "Top-level appointment date",
        related="root_id.supply_actual_date",
        help="Appointment date (supply_actual_date) of the top-level parent",
        store=False,
    )
    deadline = fields.Date(
        "Deadline",
        compute="_compute_deadline",
        inverse="_inverse_deadline",
        help="Deadline based on the project-deadline and the relative day of this service",
        store=True,
        index=True,
        recursive=True,
    )

    end_date = fields.Date(
        string="End Date",
    )
    end_date_for_calendar = fields.Date(
        string="End Date for Calendar",
        compute="_compute_end_date_for_calendar",
        inverse="_inverse_end_date_for_calendar",
        store=True,
    )
    supply_end_date_for_calendar = fields.Date(
        string="Supply End Date for Calendar",
        compute="_compute_supply_end_date_for_calendar",
        store=True,
        help="End date for supply order calendar display. Uses end_date if set, otherwise falls back to supply_date.",
    )

    deadline_formatted = fields.Char("Deadline Formatted", compute="_compute_deadline_formatted", store=False)
    timing_json = fields.Json(
        "Timing",
        compute="_compute_timing_json",
        store=False,
        recursive=True,
    )
    relative_timing_formatted = fields.Char(
        "Relative Timing",
        compute="_compute_relative_timing_formatted",
        store=True,
        help="User-friendly representation of the service's relative timing, e.g. 'Top-level service +5d'.",
    )
    display_order = fields.Integer(
        "Display Order",
        default=1,
        compute="_compute_display_order",
        index=True,
        required=True,
        store=True,
        recursive=True,
    )

    daily_prio = fields.Integer(
        default=1,
        string="Priority",
        help="Controls the display order of services with the same deadline in list views "
        "and the journey timeline. Lower numbers appear first.",
        required=True,
    )

    tag_ids = fields.Many2many(
        "riverflow.service.tag",
        "riverflow_service_tag_rel",
        "service_id",
        "tag_id",
        "Tags",
        tracking=True,
        copy=True,
        ondelete="cascade",
    )

    workflow_name_html = fields.Html(
        "Workflow Name",
        related="workflow_id.icon_name_html",
        help="Combination of Icon and name",
        store=False,
    )
    state_name = fields.Char(
        "State Name",
        related="state_id.name",
        help="Name of the state",
        store=False,
    )

    color_int = fields.Integer(
        related="state_id.color_int",
    )

    # the container of the service (log_entry, employee, etc)
    # res_model and res_id are stored compute fields driven by _compute_subject,
    # which cascades the subject from the parent based on subject_from.
    # readonly=False (rather than a no-op inverse) keeps them writable, so
    # business logic (resource_ref inverse, template clone vals, migrations,
    # tests) can assign res_id/res_model directly; the compute's "self" branch
    # then preserves the written value on the next recompute.
    res_model = fields.Char(
        string="Subject of Service Model Name",
        compute="_compute_subject",
        store=True,
        readonly=False,
        recursive=True,
        precompute=True,
    )
    res_id = fields.Integer(
        string="Subject of Service ID",
        required=True,
        compute="_compute_subject",
        store=True,
        readonly=False,
        recursive=True,
        precompute=True,
        # Note: no explicit default. A literal default kicks in during
        # _add_missing_default_values BEFORE precompute, which would make the
        # precompute loop skip the field (it's already "in vals") and the
        # cascade would silently fail. With no default, the precompute path
        # owns the initial value: it falls back to the Integer column default
        # (0) when subject_from='default' and the service has no parent and the
        # caller didn't pass res_id.
    )
    res_name = fields.Char(
        string="Subject of Service",
        compute="_compute_res_name",
        store=True,
        index="trigram",
    )
    res_sortable_name = fields.Char(
        "Subject's Sortable Name",
        compute="_compute_res_name",
        store=True,
    )

    # related entity (similar to the one in the mail_message.py in odoo)
    # content fields such as display_name of the related document can be looked
    # up in the riverflow.state.record model
    @api.model
    def _selection_target_model(self):
        return [(model.model, model.name) for model in self.env["ir.model"].sudo().search([])]

    resource_ref = fields.Reference(
        string="Subject Reference",
        selection="_selection_target_model",
        compute="_compute_resource_ref",
        inverse="_set_resource_ref",
    )

    subject_active = fields.Boolean(
        string="Subject Active",
        compute="_compute_subject_active",
        store=True,
        help="Technical field to track if the subject record is active",
    )

    # we take this flag from the workflow, computed field
    is_supply_order = fields.Boolean(
        "Is Supply Order",
        help="Enables data entry for supply order details, such as supplier and supply date. For example, a Taxi Order. This flag is taken from the workflow.",
        required=False,
        tracking=True,
        compute="_compute_is_supply_order",
        store=True,
    )

    has_supplier = fields.Boolean(
        "Has Supplier",
        related="front_office_workflow_id.has_supplier",
        store=True,
    )

    has_legs = fields.Boolean(
        "Has Legs",
        related="front_office_workflow_id.has_legs",
        store=True,
    )

    has_supply_unit_price = fields.Boolean(
        "Has Cost Price",
        related="front_office_workflow_id.has_supply_unit_price",
        store=True,
    )

    has_supply_time = fields.Boolean(
        "Has Time of Day",
        related="front_office_workflow_id.has_supply_time",
        store=True,
    )

    supply_unit_price = fields.Monetary("Cost", currency_field="supply_unit_price_currency_id", tracking=True)
    supply_unit_price_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Cost Currency",
        related="company_id.currency_id",
        store=True,
    )

    supplier_partner_id = fields.Many2one(
        "res.partner",
        string="Supplier",
        help="The partner that is supplying the service",
        required=False,
        tracking=True,
    )

    supply_order_instructions = fields.Text(
        "Instructions for the supplier",
        help="E.g. what to supply, extra information, etc",
        required=False,
        tracking=True,
    )

    supply_actual_date = fields.Date(
        string="Actual Supply Date",
        tracking=True,
        help="The actual date when this supply order was executed/completed. "
        "Set when trip is marked as completed. Used for billing/invoicing. "
        "If not set, the service deadline is used as fallback.",
    )

    supply_time_of_day = fields.Float(
        string="Time of Day",
        help="Time of day for this service (e.g. appointment time, pickup time, "
        "departure time). Displayed as HH:MM. Value 0 means not set.",
    )

    supply_date = fields.Date(
        string="Supply Date",
        compute="_compute_supply_date",
        store=True,
        index=True,
        help="Supply date for billing. Uses actual supply date if set, otherwise falls back to deadline.",
    )

    leg_ids = fields.One2many(
        "riverflow.service.leg",
        "service_id",
        string="Supply Legs",
        help="The legs of a trip booked using a supply order",
    )

    supply_leg_id = fields.Many2one(
        "riverflow.service.leg",
        "Supply Order Leg",
        readonly=True,
        help="Links back to the 'taxi booking' leg that generated this leg-info service",
    )

    supply_order_service_id = fields.Many2one(
        "riverflow.service",
        "Supply Order",
        related="supply_leg_id.service_id",
        readonly=True,
        recursive=True,
        help="Links back to the 'taxi booking' that generated this leg-info service",
    )

    @api.depends("supply_actual_date", "deadline")
    def _compute_supply_date(self):
        """Compute effective supply date for billing purposes.

        Uses supply_actual_date if available (set when trip completed),
        otherwise falls back to deadline (planned/scheduled date).
        """
        for service in self:
            service.supply_date = service.supply_actual_date or service.deadline

    @api.depends("deadline", "end_date")
    def _compute_end_date_for_calendar(self):
        for service in self:
            if not service.end_date:
                service.end_date_for_calendar = service.deadline
            else:
                service.end_date_for_calendar = service.end_date

    def _inverse_end_date_for_calendar(self):
        for service in self:
            if service.end_date_for_calendar == service.deadline:
                # single day services are the norm, and we don't want the form view to show the end date in that case
                service.end_date = False
            else:
                service.end_date = service.end_date_for_calendar

    @api.depends("supply_date")
    def _compute_supply_end_date_for_calendar(self):
        for service in self:
            service.supply_end_date_for_calendar = service.supply_date

    @api.constrains("supply_time_of_day")
    def _check_supply_time_of_day(self):
        for service in self:
            if service.supply_time_of_day < 0 or service.supply_time_of_day > 23.99:
                raise ValidationError(_("Time of day must be between 00:00 and 23:59."))

    @api.constrains("project_deadline", "end_date")
    def _check_end_date(self):
        for service in self:
            if service.end_date and service.project_deadline:
                if service.end_date < service.project_deadline:
                    raise ValidationError(
                        _(
                            f"The end date cannot be before the start date.\n"
                            f'Service "{service.name}" ends on {service.end_date} and starts on {service.project_deadline}'
                        ),
                    )

    @api.depends("res_model", "res_id")
    def _compute_resource_ref(self):
        for service in self:
            if not service.res_model or not service.res_id:
                service.resource_ref = False
            else:
                service.resource_ref = "%s,%s" % (
                    service.res_model,
                    service.res_id,
                )

    def _set_resource_ref(self):
        for service in self:
            if service.resource_ref:
                service.res_id = service.resource_ref.id
                service.res_model = service.resource_ref.model
            else:
                service.res_id = False
                service.res_model = False

    @api.depends(
        "subject_from",
        "parent_id.res_id",
        "parent_id.res_model",
    )
    def _compute_subject(self):
        # Subject cascade keyed off subject_from:
        # "default" cascades the subject top-down: a service with a parent pulls
        # the parent's subject (so a mid-chain change propagates to descendants),
        # while a service with no parent keeps whatever subject it was created
        # with. Other modes are dispatched to _apply_custom_subject_from.
        # The parent_id guard protects roots: a service with no parent never
        # gets its subject clobbered to (False, 0) -- this covers a root keeping
        # its own subject and orphaning a child (clearing parent_id).
        for service in self:
            mode = service.subject_from
            if mode == "default":
                if service.parent_id:
                    service.res_id = service.parent_id.res_id
                    service.res_model = service.parent_id.res_model
                # root (no parent): keep own subject -- no-op
            else:
                service._apply_custom_subject_from(mode)

    def _apply_custom_subject_from(self, mode):
        """Hook: higher-layer modules override to handle custom subject_from
        values they added via selection_add. Base implementation is a no-op."""
        return

    @api.depends("front_office_workflow_id.is_supply_order")
    def _compute_is_supply_order(self):
        for service in self:
            service.is_supply_order = service.front_office_workflow_id.is_supply_order

    # inheriting classes can override this method to add their own dependencies, "resource_ref.display_name"
    @api.depends("res_model", "res_id")
    def _compute_res_name(self):
        for service in self:
            if not service.res_id or not service.res_model:
                service.res_name = False
                service.res_sortable_name = False
                continue
            if service.res_model not in self.env:
                # Skip if the container model is not yet loaded in the environment
                #  (during upgrades of the module, when the container is a module dependent on riverflow)
                continue
            subject = self.env[service.res_model].sudo().browse(service.res_id)
            if not subject.exists():
                service.res_name = False
                service.res_sortable_name = False
                continue
            name = subject.display_name
            service.res_name = name if name else f"{service.res_model}/{service.res_id}"

            # Odoo has no .get() method, so we use hasattr() to check if the subject has a sortable_name field
            if hasattr(subject, "sortable_name") and subject.sortable_name:
                res_sortable_name = subject.sortable_name
            else:
                res_sortable_name = service.res_name
            service.res_sortable_name = res_sortable_name

    @api.depends("root_id", "root_id.root_name", "name", "deadline", "daily_prio")
    def _compute_root_name(self):
        for service in self:
            root_service = service.root_id
            if root_service.deadline:
                sortable_deadline = root_service.deadline.strftime("%Y-%m-%d")
            else:
                sortable_deadline = self._generate_sortable_root_deadline().strftime("%Y-%m-%d")

            service.root_name = f"{sortable_deadline} {root_service.daily_prio:04d} {root_service.name}"

    def _generate_sortable_root_deadline(self):
        # In the derived class', generate date with day offset for sorting templates
        return fields.Date.from_string("2000-01-01")

    @api.depends("parent_path")
    def _compute_root_id(self):
        for service in self.sudo():
            try:
                if service.parent_path:
                    path_parts = service.parent_path.split("/")
                    # Assign the first part of the path as the root_id
                    service.root_id = int(path_parts[0])
                else:
                    # If parent_path is empty, use the service's own ID
                    service.root_id = service.id
            except ValueError:
                # Handle the case where conversion to int fails
                service.root_id = service.id  # Or handle as appropriate

    @api.depends(
        "name", "parent_id.display_name", "supply_leg_id.name", "supply_leg_id.service_id.name", "state_name"
    )
    def _compute_display_name(self):
        for service in self:
            if service.supply_leg_id:
                name = service.supply_leg_id.name
                if not service.parent_id:
                    name = f"{name} ({service.supply_leg_id.service_id.name})"
                service.name = name  # used in lists/radar
                service.display_name = name  # used in calendar / chatter messages / emails
                continue

            display_name = service.name
            if service.parent_id:
                display_name = f"{service.parent_id.name} | {service.name}"

            service.display_name = display_name

    @api.depends("display_name")
    def _compute_sortable_name(self):
        for service in self:
            service.sortable_name = service.display_name

    @api.depends("child_ids")
    def _compute_descendant_ids(self):
        for service in self:
            descendants = self.env["riverflow.service"].search(
                [
                    ("parent_path", "=like", f"{service.parent_path}%"),
                    ("id", "!=", service.id),
                ],
                order="root_name,root_id,display_order",
            )
            service.descendant_ids = descendants

    @api.depends("parent_path")
    def _compute_indent_level(self):
        for service in self.sudo():
            if service.parent_path:
                # Count the number of slashes in parent_path, subtract 1 for indent level
                service.indent_level = service.parent_path.count("/") - 1
            else:
                service.indent_level = 0

    def _compute_indented_name(self):
        for service in self.sudo():
            service.indented_name = "%s%s" % (
                "\N{NO-BREAK SPACE}\N{NO-BREAK SPACE}\N{NO-BREAK SPACE}\N{NO-BREAK SPACE}"
                * service.indent_level,
                service.name,
            )

    @api.depends(
        "use_project_deadline_from",
        "root_id.deadline",
        "root_id.supply_actual_date",
        "root_id",
        "supply_order_service_id.deadline",
    )
    def _compute_project_deadline(self):
        for service in self:
            if service.supply_order_service_id:
                service.use_project_deadline_from = "self"
                service.project_deadline = service.supply_order_service_id.deadline
            else:
                use_project_deadline_from = service.use_project_deadline_from
                if use_project_deadline_from in ("self", "creation"):
                    # "creation" on templates: deadline is materialized at clone time
                    # by _create_service_member_from_template; the template itself
                    # keeps its own project_deadline (usually False).
                    service.project_deadline = service.project_deadline
                elif use_project_deadline_from == "root":
                    service.project_deadline = service.root_id.deadline
                elif use_project_deadline_from == "root_appointment":
                    # Use root's appointment date (supply_actual_date) with
                    # fallback to root's deadline. Keeps child deadlines stable
                    # even as the root's deadline changes during workflow progression.
                    service.project_deadline = service.root_id.supply_actual_date or service.root_id.deadline

    def _inverse_project_deadline(self):
        # this is a flag method specifying the user is allowed to store the project_deadline
        pass

    def _inverse_deadline(self):
        """Handle deadline changes from calendar drag-and-drop or manual edits.

        When a user drags a service to a new date in the calendar view, or manually
        changes the deadline, we automatically switch to 'self' mode and update the
        project_deadline accordingly. This ensures the service maintains its new deadline
        even if it was previously computed from another source (like log_entry_start/end or root).
        """
        for service in self:
            if service.deadline and service.deadline != service.project_deadline:
                # User has changed the deadline to a different value
                # Switch to 'self' mode and update project_deadline
                service.use_project_deadline_from = "self"
                service.project_deadline = service.deadline
                service.days_relative_to_project = 0

    @api.depends("use_project_deadline_from", "root_id")
    def _compute_is_days_relative_to_project_applicable(self):
        for service in self:
            service.is_days_relative_to_project_applicable = service.use_project_deadline_from not in (
                "self",
            ) and not (
                service.use_project_deadline_from in ("root", "root_appointment")
                and service.root_id.ids == service.ids
            )

    @api.depends(
        "project_deadline",
        "days_relative_to_project",
        "use_project_deadline_from",
        "is_days_relative_to_project_applicable",
        "supply_order_service_id.deadline",
        "weekend_deadline_rule",
    )
    def _compute_deadline(self):
        for service in self:
            if not service.project_deadline:
                service.deadline = False
            elif service.is_days_relative_to_project_applicable:
                deadline = service.project_deadline + timedelta(days=service.days_relative_to_project)
                service.deadline = service._apply_weekend_deadline_rule(deadline)
            else:
                service.deadline = service.project_deadline

    def _apply_weekend_deadline_rule(self, deadline):
        """Shift a weekend deadline to Friday or Monday based on the service's rule.

        Only called for computed deadlines (is_days_relative_to_project_applicable=True).
        Manual deadlines (use_project_deadline_from='self') bypass this entirely.
        """
        self.ensure_one()
        if not deadline or self.weekend_deadline_rule == "allow":
            return deadline
        weekday = deadline.weekday()  # 0=Monday ... 5=Saturday, 6=Sunday
        if self.weekend_deadline_rule == "friday_before":
            if weekday == 5:  # Saturday
                return deadline - timedelta(days=1)
            elif weekday == 6:  # Sunday
                return deadline - timedelta(days=2)
        elif self.weekend_deadline_rule == "monday_after":
            if weekday == 5:  # Saturday
                return deadline + timedelta(days=2)
            elif weekday == 6:  # Sunday
                return deadline + timedelta(days=1)
        return deadline

    @api.depends("deadline")
    def _compute_timing_json(self):
        for service in self:
            relative_days = ""
            if service.is_root_a_template:
                relative_days = ""
                date_str = self.relative_timing_formatted
                days_remaining = ""
                is_past = False
                is_today = False
            else:
                if service.is_days_relative_to_project_applicable:
                    relative_days = f"{self.relative_to_project_days_prefix()}{'+' if service.days_relative_to_project >= 0 else '-'}{abs(service.days_relative_to_project)}d"

                if not service.deadline:
                    date_str = ""
                    days_remaining = ""
                    is_past = False
                    is_today = False
                else:
                    today = fields.Date.today()
                    days_remaining = (service.deadline - today).days
                    date_str = service.deadline.strftime(Service.DATE_FORMAT)

                    is_past = service.deadline < today
                    is_today = service.deadline == today

            service.timing_json = {
                "relative_days": relative_days,
                "date": date_str,
                "days_remaining": days_remaining,
                "is_past": is_past,
                "is_today": is_today,
                "is_end_state": service.is_end_state,
            }

    @api.depends(
        "use_project_deadline_from", "days_relative_to_project", "is_days_relative_to_project_applicable"
    )
    def _compute_relative_timing_formatted(self):
        for service in self:
            if not service.is_days_relative_to_project_applicable:
                service.relative_timing_formatted = ""
            else:
                prefix = service.relative_to_project_days_prefix()
                days = service.days_relative_to_project
                # sign = "+" if days >= 0 else ""
                service.relative_timing_formatted = f"{prefix}{days:+03d}d"

    def relative_to_project_days_prefix(self):
        if self.use_project_deadline_from == "self":
            return ""
        elif self.use_project_deadline_from == "root":
            return "Top-level service"
        elif self.use_project_deadline_from == "root_appointment":
            return "Appointment"
        else:
            # Values without a bespoke short prefix (e.g. "creation", or options
            # added by higher-layer modules) fall back to the label from the
            # selection definition, so they never render as unknown.
            selection = dict(self._fields["use_project_deadline_from"]._description_selection(self.env))
            return selection.get(self.use_project_deadline_from, "")

    @api.depends("deadline")
    def _compute_deadline_formatted(self):
        for service in self:
            if service.deadline == False:
                service.deadline_formatted = ""
            else:
                service.deadline_formatted = service.deadline.strftime(Service.DATE_FORMAT)

    def print_compute_sequence_counter(self):
        pass
        # if not hasattr(self.__class__, "_compute_sequence_counter"):
        #     self.__class__._compute_sequence_counter = 0
        # print(
        #     f"_compute_sequence counter: {self.__class__._compute_sequence_counter} - {self.display_name} - id: {self.id} - entry: {self.res_id}"
        # )
        # self.__class__._compute_sequence_counter += 1

    @api.depends(
        "parent_id",
        "root_name",
        "deadline",
        "daily_prio",
    )
    def _compute_display_order(self):
        if any(isinstance(record.id, api.NewId) for record in self):
            return

        # Bulk migrations / imports re-anchor or re-home hundreds of services in
        # a Python loop, and every write touches a display_order dependency.
        # Without this guard the costly per-record sibling scan + tree rebuild
        # below runs once per write -- the dominant cost of those migrations (an
        # N+1 query storm). When the caller opts in with `defer_display_order`,
        # keep the current stored value and skip the rebuild; the caller does one
        # batched, flag-free rebuild of every touched tree at the end.
        #
        # We re-assign the *current* value rather than bare-`return`: this is a
        # STORED field, so a bare return would flush NULL. compute_value() has
        # already cleared these records from `tocompute` and protects the field
        # (see odoo/orm/fields.py), so reading the old value is safe and does not
        # recurse, and assigning it marks the field computed -- a flush inside the
        # deferred scope then converges instead of re-deferring forever.
        if self.env.context.get("defer_display_order"):
            for service in self:
                service.display_order = service.display_order
            return

        # The sibling-discovery SQL below bypasses the ORM cache, so the
        # stored compute fields it queries (root_id, res_id, res_model) must
        # already be materialised in the database. Flush them first --
        # otherwise children just-created in the same transaction look
        # orphaned (their root_id column is still NULL while only the cache
        # holds the computed value), the sibling set comes back empty, and
        # display_order stays at its default.
        self.env["riverflow.service"].flush_model(["root_id", "res_id", "res_model", "parent_path"])

        Service = self.env["riverflow.service"].with_context(active_test=False).sudo()

        order_field = self._fields["display_order"]

        for record in self:

            # Retrieve all service records with the same root_id as the current record
            # we only set the display_order field of child nodes, the root nodes are sorted
            # by name; as it would become very slow to display_order the entire list of services

            # we use direct sql to avoid recalculation of root_id, which means 'search' would decide
            # to recursively recalculate all records in the table, this overflows the stack
            if record.res_model and record.res_id:
                self.env.cr.execute(
                    """
                    SELECT id FROM riverflow_service
                    WHERE res_model = %s AND res_id = %s AND id != %s
                    """,
                    (record.res_model, record.res_id, record.id),
                )
            else:
                self.env.cr.execute(
                    """
                    SELECT id FROM riverflow_service
                    WHERE root_id = %s AND id != %s
                    """,
                    (record.root_id.id, record.id),
                )
            service_ids = [row[0] for row in self.env.cr.fetchall()]
            services = set(Service.browse(service_ids))
            # current record is not included in the search as it can lead to recursive stack overflow
            # as search also recalculates the display_order field
            services.add(record)
            # finally:
            #     self = self.with_context(computing_sequence=False)

            # Dictionary to map service IDs to their corresponding service objects
            service_dict = {service.id: service for service in services}

            # Dictionary to hold the tree structure of services
            service_tree = {}
            # Build the tree structure
            for service in services:
                # Determine the parent ID of the current service
                parent_id = service.parent_id.id if service.parent_id else None

                # Initialize the parent node list if it doesn't exist
                if parent_id not in service_tree:
                    service_tree[parent_id] = []

                # Add the current service to its parent's list of children
                service_tree[parent_id].append(service.id)

            # Initialize the display_order counter
            display_order = 0

            def assign_sequence(service_id, visited):
                nonlocal display_order  # Use the nonlocal keyword to modify the outer scope 'display_order' variable

                # Check for circular references in the hierarchy
                if service_id in visited:
                    raise ValueError(
                        f"Circular reference detected in service hierarchy involving service ID {service_id}"
                    )

                # Add the current service ID to the set of visited nodes
                visited.add(service_id)

                # Retrieve the service object using its ID
                service = service_dict[service_id]

                # Never READ display_order on a record whose recompute is still
                # pending: display_order is recursive=True, so the ORM computes
                # it record by record, and the read would re-enter this compute
                # for `service`, nesting one Python stack level per dirty
                # sibling. A root deadline change dirties the whole tree at
                # once, so large trees overflow the stack (RecursionError).
                # Assigning without reading is safe: Field.write() first calls
                # env.remove_to_compute(), clearing the pending computation.
                # For settled records, keep the read-and-compare to avoid
                # unnecessary database writes and ORM overhead.
                if self.env.is_to_compute(order_field, service) or service.display_order != display_order:
                    service.display_order = display_order

                # Increment the display_order number for the next service
                display_order += 1

                if service_id in service_tree:
                    children_ids = service_tree[service_id]
                    # Sort children based on `deadline`, then `name`, then `id`
                    sorted_children_ids = sorted(
                        children_ids,
                        key=lambda child_id: (
                            service_dict[child_id].deadline or date.max,
                            service_dict[child_id].daily_prio,
                            service_dict[child_id].name,
                            service_dict[child_id].id,
                        ),
                    )
                    for child_id in sorted_children_ids:
                        assign_sequence(child_id, visited)

            # Assign display_order numbers to root services (those without parents) and their children
            if None in service_tree:
                visited = set()

                root_ids = service_tree[None]

                # Sort the root services
                root_ids = sorted(
                    service_tree[None],
                    key=lambda root_id: (
                        service_dict[root_id].deadline or date.max,
                        service_dict[root_id].daily_prio,
                        service_dict[root_id].name,
                        service_dict[root_id].id,
                    ),
                )
                for root_id in root_ids:
                    assign_sequence(root_id, visited)

    def action_view_parent_service(self):
        return {
            "type": "ir.actions.act_window",
            "res_model": "riverflow.service",
            "res_id": self.parent_id.id,
            "view_mode": "form",
            "target": "current",
        }

    def add_child_service(self):
        # Select a start transition for a new service. Can be overridden by child modules
        # to set more default field values
        return {
            "type": "ir.actions.act_window",
            "name": "Add Service to: " + self.name,
            "view_mode": "form",
            "res_model": "riverflow.start.service",
            "context": {
                "default_parent_id": self.id,
                "default_company_id": self.company_id.id,
                "default_use_project_deadline_from": "self",
                "default_res_model": self.res_model,
                "default_res_id": self.res_id,
            },
            "target": "new",
        }

    @api.model_create_multi
    def create(self, vals_list):
        # subject_from defaults to 'default' (the field default), which is
        # parent-aware in _compute_subject: a root keeps its own subject while
        # a child cascades from its parent. No per-record juggling needed here.
        # current user is not subscribed to the chatter, because we have the radar-view, the review-count and top-3 external messages
        # this way, a team can keep track of the external messages instead of a single user
        # Also, the user eventually sending messages in the chatter will be subscribed to the record thread; the
        # user creating the service may not be involved in the actual execution of the service
        records = super(
            Service,
            self.with_context(
                mail_create_nosubscribe=True,  # At create or message_post, do not subscribe the current user to the record thread
                mail_auto_subscribe_no_notify=True,  # Do no notify users set as followers of the mail thread
            ),
        ).create(vals_list)
        for record in records:
            if not record.parent_id and record.res_model == self._name and record.res_id:
                # Auto-add reparenting hack: the caller passed res_id pointing
                # to the parent service. Convert to a real parent_id assignment
                # and let _compute_subject cascade res_id / res_model from the
                # new parent (subject_from='default' ensures the cascade fires).
                record.parent_id = record.res_id
                record.subject_from = "default"
                record.invalidate_recordset(["parent_id", "parent_path", "root_id"])
                record.parent_id.invalidate_recordset(["child_ids"])
        return records

    def write(self, vals):
        # Guard manual archive (active=False) of a protected single-open service,
        # the archive counterpart of the unlink guard. Scoped to a manual archive
        # while the subject is still active, so a legitimate subject-cascade
        # archive (subject_active already False) is never blocked.
        if vals.get("active") is False and not self.env.context.get("bypass_user_unlink_check"):
            blocked = self.filtered(lambda s: s.subject_active and s._single_open_removal_blocked())
            if blocked:
                raise UserError(
                    _(
                        "Cannot archive %s: it is in progress or still has active "
                        "sub-services. Close it through its workflow instead.",
                        ", ".join(blocked.mapped("display_name")),
                    )
                )
        result = super(Service, self).write(vals)
        if "res_model" in vals and "res_id" in vals:
            for record in self:
                if record.res_model == self._name:
                    # Auto-add reparenting hack (see create() comment)
                    record.parent_id = record.res_id
                    record.subject_from = "default"
                    record.invalidate_recordset(["parent_id", "parent_path", "root_id"])
                    record.parent_id.invalidate_recordset(["child_ids"])

        if "state_id" in vals:
            self._on_state_changed()

        return result

    def _on_state_changed(self):
        """Hook: the state of these services changed through ``write``.

        Empty here. An app module that keeps records of its own in step with a
        service's state extends it (an app's info rows that track a supply
        order's delivery, for example). riverflow itself names no such model.
        """
        return None

    @api.depends("root_id.is_this_a_template", "is_this_a_template")
    def _compute_is_root_a_template(self):
        for service in self:
            service.is_root_a_template = bool(service.root_id.is_this_a_template)

    def _get_default_recipients(self):
        """Get default recipients for email templates based on active followers who receive comments"""
        self.ensure_one()
        # Get followers with comment notification enabled (mail.mt_comment)
        comment_subtype_id = self.env["ir.model.data"]._xmlid_to_res_id("mail.mt_comment")
        recipients = self.message_follower_ids.filtered(
            lambda f: (f.partner_id and f.partner_id.active and comment_subtype_id in f.subtype_ids.ids)
        ).mapped("partner_id")
        if self.is_supply_order and self.supplier_partner_id:
            recipients = recipients.union(self.supplier_partner_id)
        return recipients

    @api.depends("supply_order_service_id.company_id")
    def _compute_company_id(self):
        """Default company is the current user's company, unless overridden"""
        for record in self:
            if record.supply_order_service_id:
                record.company_id = record.supply_order_service_id.company_id
            elif not record.company_id:
                # we do this because the company who is ordering the service
                # is driven by the user's company; not by the subject record (log entry)
                # e.g. Log Entry is for Company Germany with German Employee, but the user is from Company Netherlands
                # the supplier of a third party service will be billing to the user's company, and the user's company
                # will do an intra-company invoice to the German company
                record.company_id = self.env.company

    def _inverse_company_id(self):
        """Allow manual override of computed company"""
        # This is a flag method that allows the field to be written
        pass

    @api.model
    def calculate_use_project_deadline_from_options_for_new_service(
        self, parent_id, res_model, res_id, is_root_a_template
    ):
        """Calculate deadline options for a new service being created
        Used by both the start transition wizard and existing services to determine available options
        """
        options = ["self"]
        # when creating templates, any 'use from' is allowed because we don't know yet which parent service or subject will be selected
        if parent_id or is_root_a_template:
            options.append("root")
            options.append("root_appointment")
        if is_root_a_template:
            options.append("creation")

        return options

    def _compute_use_project_deadline_from_options(self):
        """Calculate deadline options for existing services"""
        for service in self:

            service.use_project_deadline_from_options = (
                self.calculate_use_project_deadline_from_options_for_new_service(
                    service.parent_id,
                    service.res_model,
                    service.res_id,
                    service.is_root_a_template,
                )
            )

    @api.model
    def _template_placement_allowed(self, template_service, res_model=None, is_child_add=False):
        """Check whether a template may be instantiated in the given context.

        Used by both the wizard (to filter visible templates) and the server
        (to reject invalid creation in _create_service_member_from_template).

        Args:
            template_service: The template service record to check.
            res_model: The subject model technical name (e.g. 'crewradar.log.entry').
            is_child_add: True when adding as a child of another service.

        Returns:
            True if placement is allowed, False otherwise.
        """
        if is_child_add and not template_service.can_be_child_service:
            return False
        if not is_child_add and not template_service.can_be_root_service:
            return False
        allowed_models = template_service.allowed_subject_model_ids
        # A child whose template derives its own subject via a custom
        # subject_from mode (e.g. crewradar's "most_fitting_log_entry" picks a
        # log entry off the parent's employee) does not take the parent's
        # subject. The parent/context res_model is only the derivation source,
        # not the child's eventual subject, so the allowed-subject gate must not
        # apply to it -- the mode owns the subject model.
        derives_own_subject = is_child_add and template_service.subject_from != "default"
        if allowed_models and not derives_own_subject:
            # Template is restricted to specific subjects: reject if no subject
            # is provided or if the subject model is not in the allowed list.
            if not res_model or res_model not in allowed_models.mapped("model"):
                return False
        return True

    def _check_template_placement(self, template_service, parent_id=False):
        """Validate placement rules before creating a service from a template.

        Raises UserError if the template cannot be placed in the current context.
        Skipped when context has 'skip_service_template_placement_check'.
        """
        if self.env.context.get("skip_service_template_placement_check"):
            return
        # Only check templates that have placement rules configured
        # (avoids false positives on non-template records and on templates
        # where all flags are at their permissive defaults)
        has_rules = (
            not template_service.can_be_root_service
            or not template_service.can_be_child_service
            or template_service.allowed_subject_model_ids
        )
        if not has_rules:
            return

        is_child_add = bool(parent_id)
        if parent_id:
            parent = self.env["riverflow.service"].browse(parent_id)
            res_model = parent.res_model
        else:
            res_model = self.env.context.get("default_res_model")

        if not self._template_placement_allowed(
            template_service, res_model=res_model, is_child_add=is_child_add
        ):
            # Build a descriptive error message
            reasons = []
            if is_child_add and not template_service.can_be_child_service:
                reasons.append(_("'%s' cannot be added as a child service.", template_service.name))
            if not is_child_add and not template_service.can_be_root_service:
                reasons.append(
                    _("'%s' cannot be used as a standalone (root) service.", template_service.name)
                )
            allowed_models = template_service.allowed_subject_model_ids
            if allowed_models:
                allowed_names = ", ".join(allowed_models.mapped("name"))
                if not res_model:
                    reasons.append(
                        _(
                            "'%s' requires a subject (%s).",
                            template_service.name,
                            allowed_names,
                        )
                    )
                elif res_model not in allowed_models.mapped("model"):
                    reasons.append(
                        _(
                            "'%s' can only be used on: %s.",
                            template_service.name,
                            allowed_names,
                        )
                    )
            raise UserError("\n".join(reasons))

    @api.model
    def _get_template_clone_vals(self, template_service):
        """Hook: return additional vals to merge when cloning a service template.

        Modules that add fields to service templates override this method
        and call super() to accumulate all extra vals. Called from
        _create_service_member_from_template before the create() call.

        bounce_review_responsible_team_id: the bounce review puts the
        parent's team here so the new row is born with that team. A later
        write of the same team is not a handover, and the handover notice
        is what can email the team channel.
        """
        vals = {}
        if "bounce_review_responsible_team_id" in self.env.context:
            vals["responsible_team_id"] = self.env.context["bounce_review_responsible_team_id"]
        return vals

    @api.model
    def _create_service_member_from_template(self, template_service, parent_id=False, deadline=False):
        """Create a new service based on a template service.

        Args:
            template_service: The template service record to clone from
            parent_id: Optional parent service ID for the new service

        Returns:
            The newly created service record
        """
        self._check_template_placement(template_service, parent_id=parent_id)

        vals = {
            "name": template_service.name,
            "res_id": self.env.context.get("default_res_id"),
            "res_model": self.env.context.get("default_res_model"),
            "created_by_auto_add_service_id": self.env.context.get("default_created_by_auto_add_service_id"),
            "auto_add_context_ref": self.env.context.get("default_auto_add_context_ref"),
            "workflow_id": template_service.workflow_id.id,
            "state_id": template_service.state_id.id,
            "responsible_team_id": template_service.responsible_team_id.id,
            "company_id": template_service.company_id.id,
            "is_this_a_template": self.env.context.get("default_is_this_a_template", False),
            "mail_template_id": template_service.mail_template_id.id,
            "add_operator_as_recipient": template_service.add_operator_as_recipient,
            "is_supply_order": template_service.is_supply_order,
            "supplier_partner_id": template_service.supplier_partner_id.id,
            "supply_order_instructions": template_service.supply_order_instructions,
            "tag_ids": [Command.link(tag_id) for tag_id in template_service.tag_ids.ids],
            "daily_prio": template_service.daily_prio,
            "weekend_deadline_rule": template_service.weekend_deadline_rule,
            "subject_from": template_service.subject_from,
        }

        if deadline:
            # deadline param is an instance-level override set by the caller after creation;
            # project_deadline is intentionally not set here -- "self" mode with no
            # project_deadline means the service starts without a deadline until the
            # caller assigns one. The "creation" option below is the template-level
            # alternative for auto-setting deadlines at clone time.
            vals["use_project_deadline_from"] = "self"
            vals["days_relative_to_project"] = 0
        elif template_service.use_project_deadline_from == "creation":
            # Materialize the deadline relative to today at clone time, then store
            # as a concrete "self" date so it doesn't shift on recomputation.
            vals["use_project_deadline_from"] = "self"
            vals["project_deadline"] = fields.Date.today() + timedelta(
                days=template_service.days_relative_to_project
            )
            vals["days_relative_to_project"] = 0
        else:
            vals["use_project_deadline_from"] = template_service.use_project_deadline_from
            vals["days_relative_to_project"] = template_service.days_relative_to_project

        if parent_id:
            vals["parent_id"] = parent_id

        vals.update(self._get_template_clone_vals(template_service))

        new_service = self.env["riverflow.service"].with_context(mail_create_nosubscribe=True).create(vals)

        # Copy the legs from the template
        for leg in template_service.leg_ids:
            self.env["riverflow.service.leg"].create(
                {
                    "service_id": new_service.id,
                    "sequence": leg.sequence,
                    "supply_from": leg.supply_from,
                    "supply_to": leg.supply_to,
                    "supply_cost_amount": leg.supply_cost_amount,
                    "supply_instructions": leg.supply_instructions,
                }
            )

        # Clone direct attachments from the template service
        template_attachments = self.env["ir.attachment"].search(
            [
                ("res_model", "=", "riverflow.service"),
                ("res_id", "=", template_service.id),
            ]
        )

        for attachment in template_attachments:
            attachment.copy(
                {
                    "res_id": new_service.id,
                    "res_model": "riverflow.service",
                }
            )

        # Clone notes (comments) from template
        template_notes = self.env["mail.message"].search(
            [
                ("model", "=", "riverflow.service"),
                ("res_id", "=", template_service.id),
                ("message_type", "=", "comment"),
                ("subtype_id", "=", self.env.ref("mail.mt_note").id),
            ]
        )

        for note in template_notes:
            # First clone the attachments
            new_attachment_ids = []
            if note.attachment_ids:
                for attachment in note.attachment_ids:
                    new_attachment = attachment.copy(
                        {
                            "res_id": new_service.id,
                            "res_model": "riverflow.service",
                        }
                    )
                    new_attachment_ids.append(new_attachment.id)

            self.env["mail.message"].sudo().create(
                {
                    "subject": note.subject,
                    "body": note.body,
                    # We convert template-notes to auto-comments, because we don't want to see them in the top-3 internal notes
                    # they are just for documentation purposes
                    "message_type": "auto_comment" if note.message_type == "comment" else note.message_type,
                    "subtype_id": note.subtype_id.id,
                    "model": "riverflow.service",
                    "res_id": new_service.id,
                    "author_id": note.author_id.id,
                    "email_from": note.email_from,
                    "create_uid": note.create_uid.id,
                    "parent_id": note.parent_id.id,
                    "date": note.date,
                    "starred_partner_ids": [(6, 0, note.starred_partner_ids.ids)],
                    "attachment_ids": [(6, 0, new_attachment_ids)],
                }
            )

        return new_service

    @api.model
    def _review_email_bounce_template(self):
        """The review template, or an empty recordset when the feature is off.

        Off means the xmlid is missing, the record is missing, or active is
        false. Archiving the template is how an operator turns this off.
        Deleting the xmlid does the same. The caller must not create a
        service and must not post notes. An open child is left as it is.
        """
        template = self.env.ref(
            "riverflow.template_service_review_email_bounce",
            raise_if_not_found=False,
        )
        if not template or not template.exists() or not template.active:
            return self.browse()
        return template

    @api.model
    def _record_can_own_service(self, record):
        """True when a bounce on this record may open a review service.

        A service is the parent of the review. Any other record that can own
        a service is the subject: it inherits the review mixin (that mixin is
        the team and chatter link), or an existing service already uses its
        model as res_model. A template does not count as that proof.
        """
        if record._name == "riverflow.service":
            return True
        review_mixin = self.env.registry.get("riverflow.mail.thread.review.mixin")
        if review_mixin and isinstance(record, review_mixin):
            return True
        return bool(
            self.with_context(active_test=False).search_count(
                [
                    ("res_model", "=", record._name),
                    ("active", "in", (True, False)),
                    ("is_this_a_template", "=", False),
                ],
                limit=1,
            )
        )

    @api.model
    def _bounce_review_owner(self, message):
        """The record that owns the bounced message, when it can own a service."""
        model_name = message.model
        res_id = message.res_id
        if not model_name or not res_id or model_name not in self.env:
            return None
        owner = self.env[model_name].with_context(active_test=False).browse(res_id).exists()
        if not owner or not isinstance(owner, self.env.registry["mail.thread"]):
            return None
        if not self._record_can_own_service(owner):
            return None
        return owner

    @api.model
    def _open_bounce_review_service(self, message):
        """The open child for this message, if one exists.

        Open means active and not an end state. A closed child does not
        count: the caller creates a new service.
        """
        return self.with_context(active_test=False).search(
            [
                ("bounced_mail_message_id", "=", message.id),
                ("active", "=", True),
                ("is_end_state", "=", False),
                ("is_this_a_template", "=", False),
            ],
            limit=1,
        )

    @api.model
    def _bounce_address_line(self, notification):
        partner = notification.res_partner_id
        email = notification.mail_email_address or (partner.email if partner else "") or ""
        name = partner.display_name if partner else ""
        if name and email:
            return f"{name} <{email}>"
        return email or name or "unknown address"

    @api.model
    def _bounce_note_marker(self, message):
        """Hidden id so a later bounce on this message finds the same note.

        The sentence the reader sees does not include the mail.message id.
        """
        # mail.message body sanitizes attributes. An unknown data attribute
        # is removed, so the note could not be found again. id is kept, and
        # it is not part of the sentence the reader sees.
        return f'id="riverflow-bounced-mail-message-{message.id}"'

    @api.model
    def _bounce_review_note_body(self, message, notifications):
        """Internal note: who bounced, then each failure reason.

        One sentence starts with "Bounced email to". Each recipient is
        "name <email>". Several recipients of the same message stay in
        that sentence, joined by a comma. The mail.message id is not in
        the sentence. The wrapper id carries it, so a later notification
        on the same message finds this note and replaces the body. The
        new address is added without a second note.
        """
        addresses = [
            self._bounce_address_line(notification) for notification in notifications.sorted("id")
        ]
        lines = [f"Bounced email to {', '.join(addresses)}"]
        reasons = []
        for notification in notifications.sorted("id"):
            reason = (notification.failure_reason or "").strip()
            if reason and reason not in reasons:
                reasons.append(reason)
        lines.extend(reasons)
        paragraphs = Markup("").join(Markup("<p>%s</p>") % line for line in lines)
        # Markup(f"...") does not escape. The paragraphs are already escaped.
        # The marker is our own attribute, built from the integer id.
        return Markup(f"<div {self._bounce_note_marker(message)}>{paragraphs}</div>")

    @api.model
    def _post_or_update_bounce_note(self, record, message, body):
        marker = self._bounce_note_marker(message)
        subtype = self.env.ref("mail.mt_note")
        existing = record.message_ids.filtered(
            lambda note: note.message_type == "comment"
            and note.subtype_id == subtype
            and marker in (note.body or "")
        )
        if existing:
            existing[:1].body = body
            return
        # No partner_ids: an internal note must not mail anyone.
        record.with_context(mail_create_nosubscribe=True).message_post(
            body=body,
            message_type="comment",
            subtype_xmlid="mail.mt_note",
        )

    @api.model
    def _create_bounce_review_service(self, template, owner, message):
        """Clone the template, then set the parent's team and today's deadline.

        The clone copies the template team. That team is not the parent's
        team. The deadline argument of the clone does not write
        project_deadline, so both are set after the clone. A service owner
        becomes parent_id. Any other owner becomes res_model and res_id,
        with no parent_id.
        """
        clone_context = {}
        if "responsible_team_id" in owner._fields:
            clone_context["bounce_review_responsible_team_id"] = owner.responsible_team_id.id
        if owner._name == "riverflow.service":
            parent_id = owner.id
        else:
            parent_id = False
            clone_context["default_res_model"] = owner._name
            clone_context["default_res_id"] = owner.id
        service_env = self.with_context(**clone_context) if clone_context else self
        child = service_env._create_service_member_from_template(template, parent_id=parent_id)
        # One write, not field assignment. Assignment only dirties the cache,
        # and the later flush calls write() without this context. That write
        # would post a team-handover notice. This is the first team, copied
        # from the parent, not a handover.
        vals = {
            "use_project_deadline_from": "self",
            "project_deadline": fields.Date.today(),
            "bounced_mail_message_id": message.id,
        }
        if "responsible_team_id" in owner._fields:
            vals["responsible_team_id"] = owner.responsible_team_id.id
        child.with_context(skip_responsible_team_notification=True).write(vals)
        return child

    @api.model
    def _add_review_for_bounced_message(self, message):
        """Open or update the review service for one bounced mail.message.

        Returns the new service, or an empty recordset when nothing was
        created (no bounce, feature off, owner cannot own a service, or an
        open child already holds this message).
        """
        self = self.sudo()
        message = message.exists()
        if not message:
            return self.browse()
        bounced = message.notification_ids.filtered(
            lambda notification: notification.notification_status == "bounce"
            and notification.failure_type == "mail_bounce"
        )
        if not bounced:
            return self.browse()
        template = self._review_email_bounce_template()
        if not template:
            return self.browse()
        owner = self._bounce_review_owner(message)
        if not owner:
            return self.browse()
        open_child = self._open_bounce_review_service(message)
        if open_child:
            body = self._bounce_review_note_body(message, bounced)
            self._post_or_update_bounce_note(open_child, message, body)
            self._post_or_update_bounce_note(owner, message, body)
            return self.browse()
        child = self._create_bounce_review_service(template, owner, message)
        body = self._bounce_review_note_body(message, bounced)
        self._post_or_update_bounce_note(child, message, body)
        self._post_or_update_bounce_note(owner, message, body)
        _logger.info(
            "review email bounce: opened service %s for mail.message %s",
            child.id,
            message.id,
        )
        return child

    @api.model
    def _backfill_review_email_bounces(self):
        """Open review services for bounces already stored.

        The latest email or email_outgoing message per record is the only
        candidate. A note or a tracking message is not a send. The candidate
        qualifies when it has a bounce notification and its date is at or
        after 72 hours before this run. A latest send with no bounce does
        not qualify, even when an older message bounced. Creation uses the
        same path as the live hook, and it does not send email.
        """
        cutoff = fields.Datetime.now() - timedelta(hours=72)
        self.env.cr.execute(
            """
            SELECT DISTINCT ON (model, res_id) id
              FROM mail_message
             WHERE message_type IN ('email', 'email_outgoing')
               AND COALESCE(model, '') != ''
               AND COALESCE(res_id, 0) != 0
             ORDER BY model, res_id, date DESC NULLS LAST, id DESC
            """
        )
        message_ids = [row[0] for row in self.env.cr.fetchall()]
        messages = self.env["mail.message"].browse(message_ids)
        opened = 0
        for message in messages:
            if not message.date or message.date < cutoff:
                continue
            bounced = message.notification_ids.filtered(
                lambda notification: notification.notification_status == "bounce"
                and notification.failure_type == "mail_bounce"
            )
            if not bounced:
                continue
            if self._add_review_for_bounced_message(message):
                opened += 1
        return opened

    @api.model
    def _create_services_from_template(self, template_service_id, project_deadline=False):
        """Create a new service from a template, including all child services recursively.

        Args:
            template_service_id: ID of the template service to clone

        Returns:
            The newly created root service record(s)
        """
        template_service = self.env["riverflow.service"].browse(template_service_id)
        if not template_service:
            raise UserError(_("No template service selected."))

        newly_created_services = self.env["riverflow.service"]
        if template_service.only_add_children:
            for child_template in template_service.child_ids:
                new_service = self._create_service_member_from_template(
                    child_template, deadline=project_deadline
                )
                self._clone_template_children(child_template, new_service)
                newly_created_services += new_service
        else:
            # Create main service from template
            new_service = self._create_service_member_from_template(
                template_service, deadline=project_deadline
            )
            self._clone_template_children(template_service, new_service)
            newly_created_services += new_service

        return newly_created_services

    def _clone_template_children(self, template, parent, include_deferred=False):
        """Recursively clone the children of `template` under `parent`.

        Walks the template tree depth-first; for each non-deferred child it
        creates a runtime service via `_create_service_member_from_template`
        and recurses into that child's own descendants. Deferred children
        (those with `create_on_state_id` set) are skipped unless
        `include_deferred=True` — in that mode the caller is materialising a
        deferred subtree, so the root of the subtree was already created and
        its descendants must be cloned regardless of their own
        `create_on_state_id` flags.
        """
        for child in template.child_ids:
            if child.create_on_state_id and not include_deferred:
                continue
            new_child = self._create_service_member_from_template(child, parent.id)
            # Once we're inside a deferred subtree the descendants are
            # ordinary non-deferred children, so reset include_deferred to
            # avoid re-cloning further deferred siblings at deeper levels.
            self._clone_template_children(child, new_child, include_deferred=False)

    def _create_deferred_children(self, target_state):
        """Clone deferred template children that match the target state.

        When a service transitions to a new state, this method checks
        the source template for children with create_on_state_id matching
        the target state, and clones them onto the current service.

        Deferred children are template children skipped during initial
        cloning (because create_on_state_id was set). They are created
        later when the service reaches the specified state.

        Idempotent: skips children whose template name already exists
        as a child of the current service (prevents duplicates on
        repeated transitions to the same state, e.g. self-loops).
        """
        self.ensure_one()
        if not target_state:
            return

        # Find the source template via the auto-add rule or by name match
        template = False
        if self.created_by_auto_add_service_id:
            template = self.created_by_auto_add_service_id.service_template_id
        if not template:
            # Fallback: find template by matching name
            template = self.env["riverflow.service"].search(
                [
                    ("name", "=", self.name),
                    ("is_this_a_template", "=", True),
                    ("active", "=", True),
                ],
                limit=1,
            )
        if not template:
            return

        # Find deferred children matching the target state
        deferred_children = template.child_ids.filtered(lambda c: c.create_on_state_id == target_state)
        if not deferred_children:
            return

        # Existing child names for dedup (prevent duplicates on repeated transitions)
        existing_names = set(self.child_ids.filtered("active").mapped("name"))

        # Build a clean context with only the defaults that
        # _create_service_member_from_template needs for deferred children.
        # The transition mixin copies ALL parent fields as default_* context
        # keys for wizard form pre-population (riverflow_transition_mixin.py
        # lines 35-39), but those must not leak into child service creation.
        # Without isolation, the ORM's create() picks up parent values for
        # any field not explicitly set in the vals dict — causing children
        # to inherit tags, deadlines, is_service_with_journey, credential
        # links, supply prices, etc. from the parent instead of the template.
        clean_ctx = {k: v for k, v in self.env.context.items() if not k.startswith("default_")}
        clean_ctx["default_res_model"] = self.res_model
        clean_ctx["default_res_id"] = self.res_id
        Service = self.env["riverflow.service"].with_context(clean_ctx)
        for child_template in deferred_children:
            if child_template.name in existing_names:
                continue
            new_child = Service._create_service_member_from_template(child_template, self.id)
            # Recursively clone the full descendant tree of the deferred
            # child. Without this, great-grandchildren and deeper levels
            # (e.g. an "Inform Employee" task under "AB Arrange Transport"
            # under the deferred "AB Appointment" marker) would silently
            # be skipped — the initial template instantiation uses the
            # same helper, so the deferred path should mirror it.
            Service._clone_template_children(child_template, new_child)

    def _check_parent_auto_progress(self):
        """Check if this service's parent should auto-progress after a child transition.

        Called from the transition wizard after a child service reaches a new state.
        If the parent's current state has auto_progress_on_children_done and all
        active children are in end states, the parent advances to the next
        sequential workflow state.
        """
        for service in self:
            parent = service.parent_id
            if not parent or not parent.state_id.auto_progress_on_children_done:
                continue
            active_children = parent.child_ids.filtered("active")
            if active_children and all(child.is_end_state for child in active_children):
                parent._auto_progress_to_next_state()

    def _cascade_done_to_children(self):
        """Cascade Done to active non-end-state children when this service entered
        a state flagged with ``auto_done_children_on_enter``.

        Mirror of ``_check_parent_auto_progress`` in the opposite direction.
        Called from the transition wizard after this service reaches a new
        state. For each active child still in a non-end state, finds the
        first non-cancelled end state of the child's workflow (by sequence)
        and writes it.

        Idempotent: children already in any end state (Cancelled or Done)
        are left untouched.

        When the state entered is itself a CANCELLED state, the cascade flips
        to ``_cascade_cancel_to_subtree`` instead: children are cancelled
        rather than completed, and the whole subtree is walked. See there for
        why the difference matters.
        """
        for service in self:
            if not service.state_id.auto_done_children_on_enter:
                continue
            if service.state_id.is_cancelled_state:
                service._cascade_cancel_to_subtree()
                continue
            active_children = service.child_ids.filtered(lambda c: c.active and not c.state_id.is_end_state)
            for child in active_children:
                done_state = self.env["riverflow.state"].search(
                    [
                        ("workflow_id", "=", child.state_id.workflow_id.id),
                        ("is_end_state", "=", True),
                        ("is_cancelled_state", "=", False),
                    ],
                    order="sequence",
                    limit=1,
                )
                if done_state:
                    child.state_id = done_state

    def _cascade_cancel_to_subtree(self):
        """Cancel every active, still-open service below this one.

        Two differences with the Done cascade above, both driven by what
        cancelling a parent actually means for the work underneath it:

        - The child lands in its own workflow's CANCELLED end state, not the
          Done one. Closing a taxi that was never taken as "Done" would read
          as work completed and would count as such in reporting.
        - The FULL subtree is walked, not just the direct children. A cancelled
          parent typically sits above a marker child that is already Done while
          the bookings under *that* marker are still open — e.g. an Arrange
          Work Permit root at AB Booked over a Done "AB Appointment" marker
          whose taxis and train tickets are still Registered. Those bookings
          are exactly what has to be dropped, so an already-finished node is
          descended through instead of stopping the walk.

        Workflows without a cancelled state fall back to their first normal end
        state — closing the task is still better than leaving it open. Services
        already in an end state are left untouched (idempotent).
        """
        self.ensure_one()
        for child in self.child_ids.filtered("active"):
            if not child.state_id.is_end_state:
                cancel_state = child._cancel_end_state()
                if cancel_state:
                    child.state_id = cancel_state
            child._cascade_cancel_to_subtree()

    def _cancel_end_state(self):
        """The end state to park a cancelled service in.

        The workflow's cancelled state when it has one. Otherwise the LAST end
        state by sequence rather than the first: workflows here are sequenced
        "work finished" before "no work happened" (Done 30 → Not Needed 40 →
        Cancelled 50), so the last one is the closest thing to a cancel the
        workflow offers. A workflow with only a Done state (the generic Task)
        lands there — closing the task still beats leaving it open.
        """
        self.ensure_one()
        State = self.env["riverflow.state"]
        end_states = [
            ("workflow_id", "=", self.state_id.workflow_id.id),
            ("active", "=", True),
            ("is_end_state", "=", True),
        ]
        return State.search(
            end_states + [("is_cancelled_state", "=", True)], order="sequence", limit=1
        ) or State.search(end_states, order="sequence desc", limit=1)

    def _auto_progress_to_next_state(self):
        """Progress to the next visible workflow state by sequence.

        Finds the next state in the same workflow with a higher sequence number,
        skipping states hidden from the statusbar. Sets state_id and creates
        any deferred children for the new state.
        """
        self.ensure_one()
        current_state = self.state_id
        next_state = self.env["riverflow.state"].search(
            [
                ("workflow_id", "=", current_state.workflow_id.id),
                ("sequence", ">", current_state.sequence),
                ("hide_in_statusbar", "=", False),
            ],
            order="sequence",
            limit=1,
        )
        if next_state:
            self.state_id = next_state
            self._create_deferred_children(next_state)

    @api.model
    def _add_state_record(self, record):
        """We don't want template services in the Radar screen"""
        return not record.is_root_a_template

    @api.depends(
        "workflow_id.is_supply_order",
        "workflow_id.has_supply_quantity",
    )
    def _compute_supply_quantity(self):
        for service in self:
            if (
                service.front_office_workflow_id.is_supply_order
                and service.front_office_workflow_id.has_supply_quantity
            ):
                # keep user supplied value
                pass
            else:
                # If the service has no supply quantity, set it to 1
                # e.g. a flight booking for which we only need the price but not the quantity
                service.supply_quantity = 1

    def _inverse_supply_quantity(self):
        for service in self:
            if (
                service.front_office_workflow_id.is_supply_order
                and service.front_office_workflow_id.has_supply_quantity
            ):
                # Allow manual updates only when has_supply_quantity is True, e.g manual entry of distance for a taxi order
                continue
            else:
                # Reset to 1 if someone tries to change it when has_supply_quantity is False,
                #  e.g. a flight booking for which we only need the price but not the quantity
                service.supply_quantity = 1

    @api.depends("res_model", "res_id")
    def _compute_subject_active(self):
        """Track the active state of the subject record"""
        for service in self:
            if not service.res_model or not service.res_id:
                service.subject_active = True
                continue
            if service.res_model not in self.env:
                service.subject_active = True
                continue
            record = self.env[service.res_model].sudo().browse(service.res_id)
            if not record.exists():
                service.subject_active = True
                continue
            service.subject_active = record.active if "active" in record else True

    @api.depends("subject_active")
    def _compute_active(self):
        """Ensure service is archived when its subject is archived"""
        for service in self:
            if not service.subject_active:
                service.active = False

    def _inverse_active(self):
        """Allow manual override of computed active field"""
        # This is a flag method that allows the field to be written
        pass

    def _single_open_removal_blocked(self):
        """True when this OPEN service must not be deleted/archived by hand.

        On a workflow that enforces single-open, a service that is **past its
        initial state** or still holds **live child services** (active,
        non-end-state — e.g. a booked AB appointment) is closed through its
        workflow, never destroyed: destroying it would lose the in-progress work
        and silently drop the single-open guarantee. A childless service still at
        its initial state (a plain monitoring task) stays freely removable — it
        self-heals (see crewradar_creds).
        """
        self.ensure_one()
        if not (self.is_open and self.workflow_id.enforce_single_open):
            return False
        initial = self.workflow_id.state_ids.sorted("sequence")[:1]
        past_initial = bool(initial and self.state_id.sequence > initial.sequence)
        live_children = self.child_ids.filtered(lambda c: c.active and not c.state_id.is_end_state)
        return past_initial or bool(live_children)

    def unlink(self):
        if not self.env.context.get("bypass_user_unlink_check"):
            if self.supply_leg_id.ids and not self.env.user.has_group("base.group_no_one"):
                raise UserError(
                    _(
                        "Cannot delete info-service linked to a supply order leg. Delete the leg from the supply order instead."
                    )
                )
            blocked = self.filtered(lambda s: s._single_open_removal_blocked())
            if blocked:
                raise UserError(
                    _(
                        "Cannot delete %s: it is in progress or still has active "
                        "sub-services (e.g. a booked appointment). Close it through "
                        "its workflow instead of deleting it.",
                        ", ".join(blocked.mapped("display_name")),
                    )
                )

        return super().unlink()

    @api.constrains("use_project_deadline_from", "project_deadline")
    def _check_project_deadline_if_self(self):
        """If deadline is set to 'Self', the Project Deadline must be set."""
        for record in self:
            if (
                not record.is_this_a_template
                and record.use_project_deadline_from == "self"
                and not record.project_deadline
            ):
                pass
                # raise UserError(_("You must set the Deadline"))

    def _message_compute_subject(self):
        """
        Overrides the default subject of the message (display_name) to use only the service name.
        This prevents that the workflow state is appended to the subject.
        """
        self.ensure_one()
        return self.name

    def action_view_audit_log(self):
        return {
            "type": "ir.actions.act_window",
            "name": f"Audit Log for {self.display_name}",
            "res_model": "oteny.audit.log.aggregated",
            "view_mode": "list,form",
            "domain": [("record_id", "=", self.id), ("model_name", "=", self._name)],
        }

    def action_recompute_subject_debug(self):
        """Developer tool: force a recompute of res_id/res_model on these
        services so the subject cascade can be profiled in isolation.

        Exposed only in developer mode (base.group_no_one) via the
        "Recompute Subject (debug)" server action in the list/form Action
        menu. To profile: enable the UI profiler (debug menu), select some
        services, run this action, then inspect the captured trace. We mark
        the fields to-compute and flush so the run goes through Odoo's normal
        recompute machinery (dependency resolution + recursive compute +
        write), matching what happens during an upgrade.
        """
        self.invalidate_recordset(["res_id", "res_model"])
        self.env.add_to_compute(self._fields["res_id"], self)
        self.env.add_to_compute(self._fields["res_model"], self)
        self.flush_recordset(["res_id", "res_model"])

    def handle_journey_drop(self, target_service_id, position, journey_service_ids=None):
        """
        Handle drop of this service relative to target service in journey widget.

        The journey_service_ids list must be in display order (as shown in widget).
        This method trusts that order and does not re-sort, ensuring the drop
        always works correctly regardless of initial priority values.

        Args:
            target_service_id: ID of the service dropped onto
            position: 'before' or 'after'
            journey_service_ids: List of service IDs in display order (required)
        """
        self.ensure_one()
        if not journey_service_ids:
            return  # Required for reordering

        # Filter to same date, preserving the display order from the widget.
        # The widget passes IDs in display order, so we trust that sequence.
        same_date_ids = [
            sid for sid in journey_service_ids if self.browse(sid).supply_date == self.supply_date
        ]

        # Build the new order: remove self from current position, insert at target
        services_list = [sid for sid in same_date_ids if sid != self.id]
        try:
            target_idx = services_list.index(target_service_id)
        except ValueError:
            return  # Target not found in list

        # Insert at position relative to target
        insert_idx = target_idx + 1 if position == "after" else target_idx
        services_list.insert(insert_idx, self.id)

        # Renumber all services with well-spaced priorities.
        # Uses *100 spacing to accommodate HHMM time-based daily_prio values.
        for idx, service_id in enumerate(services_list):
            self.browse(service_id).daily_prio = (idx + 1) * 100


class ServiceLeg(models.Model):
    _name = "riverflow.service.leg"
    _description = "Supply Order Leg"
    _order = "sequence,id"
    _oteny_audit_parent_field = "service_id"

    name = fields.Char(
        string="Name",
        compute="_compute_name",
        store=True,
    )

    service_id = fields.Many2one(
        "riverflow.service",
        required=True,
        ondelete="cascade",
        index=True,
        help="Supply Order",
    )

    sequence = fields.Integer(default=10)
    supply_from = fields.Char("From")
    supply_to = fields.Char("To")
    supply_cost_amount = fields.Monetary("Cost", currency_field="supply_cost_currency_id")
    supply_cost_currency_id = fields.Many2one(
        comodel_name="res.currency",
        string="Cost Currency",
        related="service_id.company_id.currency_id",
        store=True,
    )
    supply_distance_km = fields.Float(
        string="Distance (km)",
        help="Distance in kilometers for this leg (used for billing calculations)",
    )
    supply_instructions = fields.Text(
        "Instructions",
        help="Instructions to the supplier about this leg of the supply order",
    )
    is_reviewed_for_invoicing = fields.Boolean(
        string="Reviewed for Invoicing",
        default=False,
        help="If True, the leg is considered for invoicing",
    )

    @api.depends("supply_from", "supply_to", "service_id.supply_date", "service_id.name")
    def _compute_name(self):
        for leg in self:
            name_parts = []

            if leg.supply_from and leg.supply_to:
                name_parts.append(f"{leg.supply_from} → {leg.supply_to}")
            elif leg.supply_from:
                name_parts.append(leg.supply_from)
            elif leg.supply_to:
                name_parts.append(leg.supply_to)

            if leg.service_id.deadline:
                name_parts.append(leg.service_id.supply_date.strftime(Service.DATE_FORMAT))

            leg.name = " | ".join(name_parts)
