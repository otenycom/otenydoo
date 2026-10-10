from odoo import models, fields, api
from markupsafe import escape

from .riverflow_state import CHILD_TRANSITION_SIGNALS


class RiverflowTransition(models.Model):
    _name = "riverflow.transition"
    _description = "Workflow state transition"
    _order = "workflow_id,from_state_id,sequence,name,id"
    _rec_name = "display_name"
    _oteny_audit_parent_field = "workflow_id"

    name = fields.Char("Transition name", required=True)
    description = fields.Html("Description", required=False, sanitize_style=True)
    sequence = fields.Integer(default=10)
    icon = fields.Char("Icon", help="Font awesome icon e.g. fa-tasks")
    icon_name_html = fields.Html(
        "Name",
        compute="_compute_icon_name_html",
        store=True,
        help="Combination of Icon and name",
    )
    active = fields.Boolean("Active", default=True)
    bot_role = fields.Selection(
        [("claim", "Claim — queue → in-progress (the harness claim)"),
         ("work", "Work — the agent's success advance out of in-progress"),
         ("escalate", "Escalate — in-progress → a human state on failure")],
        string="Bot Role",
        help="The generic role of a transition in the bot transition harness (D173). The generic "
        "bot_work_queue resolves the claim/work/escalate transitions from these roles + the state "
        "bot_stage, so an app configures its workflow declaratively instead of the harness "
        "hard-coding xml-ids.",
    )
    is_bot_timeout = fields.Boolean(
        "Bot Timeout Exit",
        default=False,
        help="When set, the timeout reaper (_bot_reap_timeouts) follows this transition out of a "
        "bot in-progress state whose bot_timeout_minutes SLA has been exceeded. Distinct from "
        "bot_role='escalate' (the agent's own failure hand-back); this is the external 'you took "
        "too long' exit, though a workflow may point both at the same human state.",
    )
    bot_skill = fields.Char(
        "Bot Skill",
        help="The Talent skill the isolated bot run loads for the work this transition claims "
        "(set on the bot_role='claim' transition, e.g. 'permit-filing'). _bot_work_item "
        "reads it so the WORKFLOW declares the skill instead of an app model hard-coding it; "
        "empty falls back to the app's _bot_task_spec() hook. A multi-task bot preloads the "
        "union of its transitions' bot skills.",
    )
    bot_prompt = fields.Text(
        "Bot Prompt",
        help="The anchored instruction for the isolated bot run this transition claims — the "
        "per-transition task/persona override passed to the run alongside the skill and the "
        "bot-safe DTO. Set on the bot_role='claim' transition; empty falls back to the app's "
        "_bot_task_spec() prompt.",
    )
    bot_max_tool_turns = fields.Integer(
        "Bot Max Tool Turns",
        default=0,
        help="The tool-turn (iteration) budget the isolated bot run this transition claims needs "
        "(set on the bot_role='claim' transition, e.g. 200 for a field-by-field browser "
        "filing). Rides the work item as max_tool_turns; the platform sizes the bot's agent "
        "budget to the max over its transitions' declared budgets. 0 = undeclared (the "
        "platform default applies).",
    )
    bot_verbose = fields.Boolean(
        "Bot Verbose Trace",
        default=False,
        help="When set on the bot_role='claim' transition, the isolated bot run this transition "
        "claims streams a per-tool-call narration line into the bot's channel (the [oteny:verbose] "
        "flag on the dispatch). A debugging aid for a run that otherwise answers silently — leave "
        "off in normal operation (it is chatty and costs channel writes).",
    )
    from_state_id = fields.Many2one(
        "riverflow.state",
        "From",
        help="Leave blank to define a start-transition",
        copy=True,
        index=True,
        required=False,
        # todo: only restrict for new records, allow a way to make transitions between workflows
        domain="[('workflow_id', '=', workflow_id)]",
    )
    to_state_id = fields.Many2one(
        "riverflow.state",
        "To",
        copy=True,
        index=True,
        required=True,
        # Todo: refine this domain, so that it is possible to create a transition between workflows
        # For now disabled since i couldn't make it to back office via ui
        # domain="[('workflow_id', '=', workflow_id)]",
    )
    workflow_id = fields.Many2one(
        "riverflow.workflow",
        string="Workflow",
        help="Derived from the to-state, since the from-state is optional",
        compute="_compute_workflow_id",
        store=True,
    )

    workflow_name = fields.Char(
        string="Workflow Name",
        compute="_compute_workflow_name",
        store=True,
    )

    to_responsible_team_id = fields.Many2one(
        "res.partner",
        "Assign to Responsible Team",
        help="Team to assign the service to",
        required=False,
        # domain="['|', ('is_user', '=', True), ('is_riverflow_team', '=', True)]",
        domain="[('is_riverflow_team', '=', True)]",
    )

    @api.depends("workflow_id")
    def _compute_workflow_name(self):
        for transition in self:
            transition.workflow_name = transition.workflow_id.name if transition.workflow_id else False

    def _wizard_dialog_title(self):
        """The title of the dialog this transition opens.

        One rule for the dialog that opens the wizard and for every reopen of
        it: a wizard that reopens itself on a warning or a retry must look like
        the dialog the user just saw. An act_window without a name shows "Odoo".
        A start transition (no from-state) runs outside a record, so its title
        is the transition name alone.
        """
        self.ensure_one()
        if not self.from_state_id:
            return self.name
        return f"{self.workflow_name} | {self.name}"

    model = fields.Char(
        "Related Model",
        related="workflow_id.model",
        help="Model on which the workflow runs",
        index=True,
        store=True,
        readonly=True,
    )
    action_id = fields.Many2one(
        "riverflow.transition.action",
        "Action",
        domain="[('model', '=', model)]",
        copy=True,
    )
    action_context = fields.Text(
        "Action context", help="Configuration values for the action screen", copy=True
    )
    # The child service side of execute_child_transitions on the parent
    # service's state (radar pipeline applicants plan, Q162): the workflow marks
    # which of its own transitions closes a service that is not started, so that
    # transition executes with its wizard and note instead of a direct write of
    # the state.
    execute_on_parent_transitions = fields.Selection(
        CHILD_TRANSITION_SIGNALS,
        string="Execute On Parent Transitions",
        copy=True,
        help="When the parent service enters a state whose Execute Child "
        "Transitions has the same value, a child service in this transition's "
        "from-state executes this transition by itself, as if a person clicked "
        "it. Mark the transition that closes a service that is not started "
        "(Cancel, Not Needed); a started service has no marked transition and "
        "stays open.",
    )
    # A transition that ends a case refuses while services below the record are
    # still open, so no work is forgotten (radar pipeline applicants plan,
    # decision 145; first user: Complete Termination of the employee). Services
    # that this transition closes by itself do not count: the refusal reads the
    # same rule as the closing walk (riverflow.state.mixin._open_services_left_by).
    refuse_with_open_services = fields.Boolean(
        string="Refuse With Open Services",
        copy=True,
        help="The transition screen refuses with an error that names the open services below the record: "
        "for a service its child services, for another record the services whose subject it is, with their "
        "child services. Services that this transition closes by itself do not count.",
    )
    display_name = fields.Char("Display Name", compute="_compute_display_name", store=True, index="trigram")

    # Per-transition email template override. When set, the Send Email wizard
    # uses this template instead of the service's mail_template_id. This allows
    # different email transitions within the same workflow to use different
    # templates (e.g., AB appointment request vs. pickup request).
    mail_template_id = fields.Many2one(
        "mail.template",
        string="Email Template",
        copy=True,
        domain="[('model', '=', 'riverflow.service')]",
        help="When set, the Send Email transition wizard uses this template "
        "instead of the service's mail_template_id.",
    )

    @api.depends("from_state_id.workflow_id", "to_state_id.workflow_id")
    def _compute_workflow_id(self):
        for transition in self:
            if transition.from_state_id:
                transition.workflow_id = transition.from_state_id.workflow_id
            else:
                # start transition
                transition.workflow_id = transition.to_state_id.workflow_id

    @api.depends("name", "from_state_id", "from_state_id")
    def _compute_display_name(self):
        for transition in self:
            fromState = transition.from_state_id.display_name if transition.from_state_id else "Start"
            transition.display_name = (
                f"{transition.name}: {fromState} → {transition.to_state_id.display_name}"
            )

    @api.depends("icon", "name", "action_id.icon")
    def _compute_icon_name_html(self):
        for record in self:
            icon = record.icon or record.action_id.icon
            if icon:
                record.icon_name_html = (
                    f'<span><span class="fa {escape(icon)}"></span>&nbsp;{escape(record.name)}</span>'
                )
            else:
                record.icon_name_html = escape(record.name)

    @api.model
    def _get_transition_description(self, transition):
        if self.transition_id.from_state_id:
            transition_description = (
                self.transition_id.from_state_id.name + " → " + self.transition_id.to_state_id.name
            )
        else:  # start transition
            transition_description = self.transition_id.to_state_id.name
        if self.transition_id.description:
            transition_description += f" | {self.transition_id.description}"
