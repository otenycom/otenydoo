from datetime import timedelta
from random import randint

from odoo import _, api, fields, models

# The context keys a step uses to set the deadline itself
# (riverflow.transition.wizard.action_save). A step with one of them overrides
# the deadline setting of its target state.
STEP_DEADLINE_KEYS = ("clear_deadline", "set_deadline_to_today", "followup_in_days", "set_deadline_relative")
# The task fields a deadline setting writes.
DEADLINE_FIELDS = ("use_project_deadline_from", "project_deadline", "days_relative_to_project")
# The named values that link a parent service's state to the transitions of its
# child services: a state with execute_child_transitions "cancel" makes every
# open child service below it execute its transition marked
# execute_on_parent_transitions "cancel". A named value, not an on/off flag, so
# one mechanism can carry another meaning later and a transition answers only
# the value it names.
CHILD_TRANSITION_SIGNALS = [("cancel", "Cancel the child services that are not started")]


class RiverflowWorkflowState(models.Model):
    _name = "riverflow.state"
    _description = "Workflow state"
    _order = "workflow_id,sequence,name,id"
    _rec_name = "display_name"
    _oteny_audit_parent_field = "workflow_id"

    name = fields.Char("State name", required=True)
    description = fields.Text("Description", required=False)
    active = fields.Boolean("Active", default=True)
    workflow_id = fields.Many2one("riverflow.workflow", "Workflow", copy=True, index=True, required=True)
    sequence = fields.Integer(default=10)
    hide_in_statusbar = fields.Boolean("Hide in Statusbar", default=False)
    is_end_state = fields.Boolean("Is End State", default=False)
    is_owned_by_bot = fields.Boolean(
        "Owned by Bot",
        default=False,
        help="If true, a service in this state is owned/worked by an automated agent "
        "(a business bot), not a human. Ownership lives in the STATE — a hand-off transition "
        "moves the service between a human-owned and a bot-owned state, so the per-state "
        "transition buttons stay meaningful. Drives the bot's work poll and lets a human "
        "review filter exclude the bot's in-flight queue (is_owned_by_bot = False).",
    )
    bot_stage = fields.Selection(
        [("queue", "Queue — the bot should act"),
         ("in_progress", "In progress — claimed, the bot is working"),
         ("watch", "Watch — the bot monitors, no action")],
        string="Bot Stage",
        help="The generic role of a bot-owned state in the transition harness (D173): a service in "
        "a `queue` state is picked up (claimed → the `in_progress` state → an isolated agent run); "
        "`watch` states the bot monitors without firing. Read by the generic bot_work_queue so an "
        "app sets these on its workflow instead of hard-coding transition xml-ids.",
    )
    bot_timeout_minutes = fields.Integer(
        "Bot Timeout (minutes)",
        default=0,
        help="SLA for a bot `in_progress` state: a record that has sat here longer than this is "
        "escalated by the timeout reaper (ir.cron → _bot_reap_timeouts) through the state's "
        "is_bot_timeout transition. 0 disables the reaper for this state (the backstop for a dead "
        "harness that never reported back — set it comfortably above the harness's own poll window).",
    )
    bot_login_hold = fields.Boolean(
        "Bot Login Hold",
        default=False,
        help="A record in this state holds its bot's one live slot for a human sign-in. "
        "Fresh bot work on the same workflow waits. The record's own login resume is "
        "admitted (exclude-self). Set this on login-park and login-resume states in the "
        "workflow XML. There is no occupancy record and no extra clock.",
    )

    """
    SERVICE STATE COLORS
    Aministrative & Financial (Orange - 2)
    Back Office Workflow: Registered, Approved, Booked states
    To Be Invoiced Workflow: To Be Invoiced, Ready for Invoicing, Invoiced states
    Rationale: Orange represents official/formal processes related to finance and administration
    
    Transportation & Travel (Teal - 7)
    Taxi Order Workflow: Not Started, Ordered, Confirmed states
    Train Ticket Workflow: Not Started, Booked states
    Taxi Leg Workflow: Info state
    Rationale: Teal provides good visibility without conflicting with blue used for log entries
    
    Task Management (Raspberry/Pink - 9)
    Task Workflow: Not Started, In Progress, Done states
    Rationale: Distinctive color that stands out for action items and tasks
    
    Communication (Purple - 5)
    Email Workflow: Not Started, Sent, Not Needed states
    Rationale: Purple represents communication and information exchange
    

    ENTRIES
    Work-Related Workflows (Blue - 8)
    Crew Change (already blue)
    Sales (already blue)

    Availability/Waiting Workflows (Green - 10)
    Available
    Standby Home
    Standby Away
    No Replacement Needed

    Time Off Workflows (Yellow - 3)
    Vacation (already yellow)
    Own Request

    Health-Related Workflows (Purple - 5)
    Sick At Home
    Sick Away

    Administrative Workflows (Orange - 2)
    Cash Payout
    Terminated
    Special States

    Cancelled states: Red (1)
    Default/To be planned: Gray (0)

        The 12 colors in $o-colors are:
        0 #a2a2a2 - Gray (for "No color" option)
        1 #ee2d2d - Red
        2 #dc8534 - Orange
        3 #e8bb1d - Yellow
        4 #5794dd - Cyan/Light Blue
        5 #9f628f - Purple
        6 #db8865 - Almond/Light Brown
        7 #41a9a2 - Teal
        8 #304be0 - Blue
        9 #ee2f8a - Raspberry/Pink
        10 #61c36e - Green
        11 #9872e6 - Violet
    """
    color_int = fields.Integer("Color", default=11)

    # todo: add to radar.
    # Extend filters, 'Is Pending' should be 'Pending in Front Office' and 'Pending in Back Office'
    is_back_office_state = fields.Boolean(
        "Is Back Office State",
        help="If true, this state is for back office use; to handle the financial aspect of the workflow.",
        default=False,
    )
    is_cancelled_state = fields.Boolean("Is Cancelled State", default=False)
    is_supply_order_delivered = fields.Boolean(
        "Supply Order Delivered",
        help="If true, this state indicates the supply/service has been delivered to the customer, "
        "even if administrative tasks (verification, billing) remain. Used by info services to track "
        "when the actual service completion occurred.",
        default=False,
    )
    requires_onboarding_credentials = fields.Boolean(
        "Requires Onboarding Credentials",
        help="If true, onboarding credential slots are shown in the credential plan "
        "for entities in this state. Set on employee states where credential "
        "arrangement should be tracked (e.g. Agreement Signed, Employed) and "
        "ship states where ship documents are needed (e.g. Onboarding, Active).",
        default=False,
    )
    counts_as_active_employment = fields.Boolean(
        "Counts as Active Employment",
        help="If true, an employee in this state is treated as actively/possibly "
        "employed for credential-holder eligibility, so their credential plan "
        "slots (and the employment-contract ground-layer slot) are generated even "
        "before a contract record exists. This catches newly onboarding crew "
        "(e.g. Agreement Signed) who do not yet have a contract row. Set on "
        "employment states such as Agreement Signed, Employed and Offboarding; "
        "leave off end states (Terminated, Cancelled) and pre-employment states.",
        default=False,
    )
    show_state_in_crew_planning = fields.Boolean(
        "Show in Crew Planning",
        help="If true, this state is shown as a badge on the slot label in the crew planning view. "
        "Set on states that are not the normal operating state (e.g. not 'Employed' for employees, "
        "not 'Active' for ships) to alert planners.",
        default=False,
    )
    auto_progress_on_children_done = fields.Boolean(
        "Auto-Progress When Children Done",
        help="When set, services in this state automatically progress to the "
        "next sequential workflow state once all active child services "
        "have reached an end state. Used for states that spawn deferred "
        "child tasks (e.g. parallel email-sending services) and should "
        "complete when those tasks finish.",
        default=False,
    )
    auto_done_children_on_enter = fields.Boolean(
        "Auto-Done Children When Entered",
        help="When set, entering this state moves all active non-end-state "
        "child services to the first non-cancelled end state of their own "
        "workflow (by sequence). Mirror of auto_progress_on_children_done in "
        "the opposite direction. Used on parent terminal states whose "
        "semantics imply child tasks are also complete (e.g. AUV Done means "
        "the OPS Review-AUV child task is moot). On a state that is also a "
        "cancelled state, the children are CANCELLED instead of completed and "
        "the whole subtree is walked (e.g. cancelling a work permit case drops "
        "the taxis booked under its AB appointment).",
        default=False,
    )
    # A parent service's state that ends the case says so to the child services
    # below it, and each child service closes through its OWN transition (radar
    # pipeline applicants plan, Q162). The parent decides WHEN: only a state whose
    # end means "the case is over" sets it, because an end state such as "Sent"
    # of an e-mail or "Application Sent" of an A1 means "my part is done" while
    # the child services below are follow-up work. The child service's workflow
    # decides HOW: it marks the transition that closes a service that is not
    # started (Cancel, Not Needed), so the transition wizard, its note and its
    # side effects run as when a person clicks it. A started child service has no
    # such transition from its state and stays open.
    execute_child_transitions = fields.Selection(
        CHILD_TRANSITION_SIGNALS,
        string="Execute Child Transitions",
        help="When a service enters this state, every open child service below "
        "it that has a transition marked with the same value (Execute On Parent "
        "Transitions) from its current state executes that transition, as if a "
        "person clicked it. The whole tree below is walked. A child service that "
        "is already started has no such transition, so it stays open, with the "
        "child services below it, for a person to decide. Set it only on a state "
        "that ends the case.",
    )

    # The deadline of a task follows the step that brings it into its state
    # (radar pipeline applicants plan, release R5c, decisions Q151 and Q156).
    # Before, the deadline belonged to the task alone: a plain step (Back,
    # Restart, Resend, "Sent already") carried over whatever date the task had,
    # and a task added by hand kept the fixed date of its start screen for good,
    # so a renewal task could sit on a date that no longer followed its permit.
    # The setting on the state is the default for every step into it and for a
    # task made in it. A step overrides it only explicitly: its own deadline key
    # (STEP_DEADLINE_KEYS) or a date the user chooses on its screen. A date typed
    # or dragged on the task itself stays possible and holds until the next step
    # into a state with a setting (Q155: the manual override stays). "Keep" is
    # the default, so a state without a setting behaves as before.
    deadline_on_entry = fields.Selection(
        [
            ("keep", "Keep the task's date"),
            ("rule", "Follow a rule"),
            ("followup", "Follow-up after N days"),
            ("today", "Today"),
            ("clear", "No deadline"),
        ],
        string="Deadline on Entry",
        default="keep",
        required=True,
        help="What a task's deadline becomes when a step brings it into this state, "
        "or when a task is made in this state. Keep: the task keeps its date. "
        "Follow a rule: the task follows the Deadline Rule plus the Deadline Days. "
        "Follow-up: today plus the Deadline Days, as a fixed date. Today: today, as "
        "a fixed date. No deadline: the date is cleared. A step that sets the "
        "deadline itself, or a date chosen on the step screen, overrides this.",
    )
    deadline_rule_from = fields.Selection(
        selection="_selection_deadline_rule_from",
        string="Deadline Rule",
        help="The source of the date when Deadline on Entry is 'Follow a rule': the "
        "same choices as Deadline From on a task.",
    )
    deadline_days = fields.Integer(
        "Deadline Days",
        help="For a rule: the days before (negative) or after the rule's date. "
        "For a follow-up: the days after the step.",
    )

    @api.model
    def _selection_deadline_rule_from(self):
        """The task's Deadline From choices that follow a source, so modules that
        add a mode (rivercreds, crewradar) add it here as well. 'Self' and
        'Creation Date' are fixed dates, which the other kinds already cover."""
        service_field = self.env["riverflow.service"]._fields["use_project_deadline_from"]
        return [
            (value, label)
            for value, label in service_field._description_selection(self.env)
            if value not in ("self", "creation")
        ]

    def _deadline_entry_vals(self):
        """The deadline values a task takes when it enters this state, or {} when
        the state keeps the task's date (also for a rule without a source)."""
        self.ensure_one()
        today = fields.Date.today()
        kind = self.deadline_on_entry
        if kind == "rule" and self.deadline_rule_from:
            return {
                "use_project_deadline_from": self.deadline_rule_from,
                "days_relative_to_project": self.deadline_days,
            }
        if kind == "followup":
            fixed = today + timedelta(days=self.deadline_days)
        elif kind == "today":
            fixed = today
        elif kind == "clear":
            fixed = False
        else:
            return {}
        return {"use_project_deadline_from": "self", "project_deadline": fixed, "days_relative_to_project": 0}

    def _put_fixed_open_tasks_on_rule(self):
        """Put the open tasks of this state that sit on a fixed date (Self) back
        on the state's rule, each with a note that names the old date. Returns
        the tasks it changed.

        For a migration that gives a state its rule (release R5c, decision Q157):
        a task added by hand, or carried along by a plain step, kept a fixed
        date that no longer followed its source (Andres, radar task 32423). Only
        a rule: a fixed-date setting (follow-up, today) would move every task to
        the day of the migration. Checks active explicitly: a migration can run
        with active_test=False."""
        self.ensure_one()
        entry_vals = self._deadline_entry_vals()
        if self.deadline_on_entry != "rule" or not entry_vals:
            return self.env["riverflow.service"]
        tasks = self.env["riverflow.service"].search(
            [
                ("state_id", "=", self.id),
                ("is_this_a_template", "=", False),
                ("use_project_deadline_from", "=", "self"),
            ]
        ).filtered(lambda task: task.active and not task.is_end_state)
        rule_label = dict(self._fields["deadline_rule_from"]._description_selection(self.env)).get(
            self.deadline_rule_from, self.deadline_rule_from
        )
        date_format = self.env["riverflow.service"].DATE_FORMAT
        for task in tasks:
            old = task.project_deadline
            task.write(entry_vals)
            task.message_post(
                body=_(
                    "The deadline follows the rule of the step %(state)s again (%(rule)s). "
                    "The task had a fixed date: %(old)s.",
                    state=self.name,
                    rule=rule_label,
                    old=old.strftime(date_format) if old else _("none"),
                ),
                subtype_xmlid="mail.mt_note",
            )
        return tasks

    from_transition_ids = fields.One2many(
        "riverflow.transition",
        "from_state_id",
        string="From-transitions",
        help="Transitions from this state",
    )

    def bot_timeout_transition(self):
        """This state's ``is_bot_timeout`` exit — the one hand-back door out of a stalled bot
        run, and an empty recordset when the state has none.

        TWO callers share this lookup on purpose. ``_bot_reap_timeouts`` takes the door on the
        CLOCK, once the state's ``bot_timeout_minutes`` SLA passes.
        ``_bot_assert_human_transition_allowed`` lets a person take the SAME door ON DEMAND,
        before the SLA, when a run looks abandoned. One lookup keeps those two from drifting
        into two meanings: a workflow that declares one timeout exit gets one hand-back, however
        it is triggered. ``[:1]`` because a state with two flagged exits has an ambiguous
        hand-back, and the first by sequence is the declared one."""
        self.ensure_one()
        return self.from_transition_ids.filtered("is_bot_timeout")[:1]

    display_name = fields.Char("Display Name", compute="_compute_display_name", store=True, index="trigram")

    @api.depends("name", "workflow_id.name")
    def _compute_display_name(self):
        for state in self:
            state.display_name = f"{state.name} | {state.workflow_id.name}"

    def workflow_add_from_transition(self):
        return {
            "type": "ir.actions.act_window",
            "name": "Add Transition from: " + self.name,
            "view_mode": "form",
            "res_model": "riverflow.transition",
            "context": {
                "default_workflow_id": self.workflow_id.id,
                "default_from_state_id": self.id,
                "is_start_transition": False,
            },
            "target": "current",
        }
