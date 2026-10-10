from odoo import _, fields, models, api
from odoo.exceptions import UserError
from odoo.addons.riverflow.models.riverflow_transition_mixin import RiverflowTransitionMixin  # type: ignore
from datetime import timedelta
import ast
import json


class RiverflowWorkflowStateMixin(RiverflowTransitionMixin):
    _name = "riverflow.state.mixin"
    _description = "Mixin to support workflow state in any model"

    # Current workflow. Not a computed field, so it can be set in the form view
    # and the user can then select the state. If state is change later in write/create, we keep
    # the workflow_id as it was set initially, so it represents the front office workflow/work status
    workflow_id = fields.Many2one(
        "riverflow.workflow",
        domain="[('model', '=', model)]",
        string="Workflow",
        tracking=True,
        index=True,
    )

    front_office_workflow_id = fields.Many2one(
        "riverflow.workflow",
        domain="[('model', '=', model)]",
        string="Front Office Workflow",
        help="Used to remember the front office workflow, once the state is set to a back office workflow. The front office workflow is used to flag if the service is a supply order.",
        compute="_compute_front_office_workflow_id",
        store=True,
    )

    state_id = fields.Many2one(
        "riverflow.state",
        "State",
        tracking=True,
        index=True,
        help="Current workflow state",
    )
    state_id_statusbar_json = fields.Json(
        "Statusbar Info", compute="_compute_state_id_statusbar_json", store=False
    )
    from_transition_ids = fields.Many2many(
        "riverflow.transition",
        compute="_compute_from_transition_ids",
    )

    current_workflow_name = fields.Char(
        "Workflow name",
        related="workflow_id.name",
        store=True,
        index=True,
    )
    state_name = fields.Char("State name", related="state_id.name", store=True, index=True)

    state_json = fields.Json(string="Workflow State", compute="_compute_state_json", store=False)
    transition_buttons_json = fields.Json(
        string="State Actions",
        compute="_compute_transition_buttons_json",
        store=False,
    )

    deadline = fields.Date(
        string="Deadline",
        compute="_compute_deadline",
        store=True,
        index=True,
        help="Planning deadline.",
    )

    timing_json = fields.Json(
        "Timing",
        compute="_compute_timing_json",
        store=False,
    )

    is_end_state = fields.Boolean(
        "Is End State",
        compute="_compute_is_end_state",
        store=True,
        index=True,
    )

    # = self._name, made accessible for use in the filter-domain of the workflow dropdown
    model = fields.Char(compute="_compute_model", help="Model on which the workflow runs.")

    @api.model
    def default_get(self, fields_list):
        res = super(RiverflowWorkflowStateMixin, self).default_get(fields_list)
        res["model"] = self._name
        return res

    @api.model
    def _compute_model(self):
        for record in self:
            record.model = record._name

    @api.onchange("workflow_id", "state_id")
    def on_change_workflow_id(self):
        for s in self:
            if s.workflow_id and not s.state_id:
                startStates = self.env["riverflow.state"].search(
                    [
                        ("workflow_id", "=", s.workflow_id.id),
                    ],
                    limit=1,
                    order="sequence,name,id",
                )
                if len(startStates):
                    s.state_id = startStates[0]

    @api.depends("state_id", "state_id.from_transition_ids")
    def _compute_from_transition_ids(self):
        for s in self:
            if not s.state_id:
                if s.workflow_id:
                    domain = [
                        ("from_state_id", "=", False),
                        ("workflow_id", "=", s.workflow_id.id),
                    ]
                    s.from_transition_ids = self.env["riverflow.transition"].search(
                        domain, order="workflow_name,sequence,id"
                    )
                else:
                    s.from_transition_ids = []
            else:
                s.from_transition_ids = self.env["riverflow.transition"].search(
                    [
                        ("from_state_id", "=", s.state_id.id),
                    ],
                    order="sequence,id",
                )

    def _compute_state_json(self):
        # for rendering just the state name and workflow name
        for record in self:
            wf_state_text = record.current_workflow_name or ""
            if wf_state_text or record.state_name:
                state_text = record.state_name or "Not Started"
                wf_state_text = state_text  # " | ".join([wf_state_text, state_text])

            # todo: store the icon so its not a lookup
            icon = record.workflow_id.icon or ""
            is_end_state = record.is_end_state == True

            state_json = {
                "text": wf_state_text,
                "workflow_name": record.current_workflow_name,
                "workflow_icon": icon,
                "is_end_state": is_end_state,
                # this is not a start transition, so we can refresh the underlying list/form view
                "reload_on_close": True,
                "buttons": [],
            }

            record.state_json = state_json

    def _user_sees_bot_work_transitions(self):
        """True only for the bound bot user. HR never sees claim / work."""
        if "oteny.bot" not in self.env:
            return False
        return bool(
            self.env["oteny.bot"].sudo().search(
                [("bot_user_id", "=", self.env.uid)], limit=1
            )
        )

    def _format_bot_working_clock(self, when):
        """Naive UTC Datetime as the viewing user's wall clock.

        Form widgets already convert via ``env.tz``. The working-note banner
        interpolates the same instants as text, so it must convert too.
        """
        if not when:
            return ""
        local = fields.Datetime.context_timestamp(self, when)
        return fields.Datetime.to_string(local) or ""

    def _bot_working_note(self):
        """Generic strip when a live claim hides every button."""
        self.ensure_one()
        started = self.bot_work_started_at
        sla = self.state_id.bot_timeout_minutes
        deadline = started + timedelta(minutes=sla) if started and sla else False
        return _(
            "The bot is working. Started %(started)s. It is handed back at "
            "%(deadline)s if it does not finish.",
            started=self._format_bot_working_clock(started),
            deadline=self._format_bot_working_clock(deadline),
        )

    def _transition_button(self, transition, index, primary):
        """One button row for ``transition_buttons_json``. Extracted so the live-claim branch
        below can render the abort with exactly the shape every other button has — a second
        copy of this dict would drift the moment a key is added."""
        self.ensure_one()
        transition_id = (
            transition.id.origin if isinstance(self.id, api.NewId) else int(transition.id)
        )
        button = {
            "index": index,
            "caption": transition.name,
            "help": transition.description,
            "action": "action_button_click",
            "primary": primary,
            # context is posted back to the server side action method
            "context": {
                "transition_id": transition_id,
            },
        }
        button.update(self._transition_button_extras(transition))
        return button

    @api.depends(
        "state_json",
        "from_transition_ids",
        "bot_work_started_at",
        "bot_run_started_at",
        "state_id.bot_stage",
        "state_id.bot_timeout_minutes",
        "state_id.is_owned_by_bot",
    )
    def _compute_transition_buttons_json(self):
        # for rendering the transition buttons below the state name
        for record in self:
            transition_buttons = dict(record.state_json or {})
            transition_buttons["buttons"] = []
            sees_bot_work = record._user_sees_bot_work_transitions()
            if (
                hasattr(record, "_bot_claim_is_live")
                and record._bot_claim_is_live()
                and not sees_bot_work
            ):
                transition_buttons["working_note"] = record._bot_working_note()
                # Every other exit stays hidden while the run is live — the fence in
                # _bot_assert_human_transition_allowed would refuse it anyway, and a button
                # that always errors is worse than no button. The state's own timeout exit is
                # the exception: it is the hand-back the reaper takes on the clock, and the
                # note beside it names the hour that will happen. A person who can see the run
                # is dead needs that door NOW, on the panel that told them to wait.
                abort = record.state_id.bot_timeout_transition()
                if abort:
                    transition_buttons["buttons"] = [
                        record._transition_button(abort, 0, False)]
                record.transition_buttons_json = transition_buttons
                continue
            transition_ids = record.from_transition_ids
            hide_bot_work = not sees_bot_work
            no_primary = bool(
                record.state_id
                and record.state_id.is_owned_by_bot
                and record.state_id.bot_stage in ("queue", "in_progress")
            )
            if transition_ids:
                index = 0
                for transition in transition_ids:
                    if hide_bot_work and transition.bot_role in ("claim", "work"):
                        continue
                    if not record.state_id:
                        # The start transition to the first state can be skipped, as that is a no-op
                        # its typically named "Not Started" and used in the Start-new wizard to create a new record in the first state
                        # in these, we are handling a record that is already created and the null state is logically the first state
                        if transition.from_state_id.id == False and transition.name == "Not Started":
                            continue

                    primary = (not no_primary) and index == 0
                    transition_buttons["buttons"].append(
                        record._transition_button(transition, index, primary))
                    index += 1

            record.transition_buttons_json = transition_buttons

    def _compute_state_id_statusbar_json(self):
        for record in self:
            current_state_id = record.state_id
            if current_state_id:
                workflow_id = current_state_id.workflow_id.id
            else:
                workflow_id = record.workflow_id.id

            state_ids = self.env["riverflow.state"].search([("workflow_id", "=", workflow_id)])

            json = {"states": []}
            is_first = True
            is_last = False

            for state in state_ids:
                if current_state_id:
                    is_current_state = state.id == current_state_id.id
                else:
                    is_current_state = is_first
                    is_first = False

                if not state.hide_in_statusbar or is_current_state:
                    json["states"].append(
                        {
                            "value": state.id,
                            "label": state.name,
                            "isFolded": state.hide_in_statusbar,
                            "isSelected": is_current_state,
                        }
                    )

            record.state_id_statusbar_json = json

            # Check if last state has transition to another workflow
            if not json["states"]:
                continue

            last_state = self.env["riverflow.state"].browse(json["states"][-1]["value"])
            if not last_state.from_transition_ids:
                continue

            first_transition = last_state.from_transition_ids[0]
            if first_transition.to_state_id.workflow_id != last_state.workflow_id:
                # Add the target state to next workflow (e.g 'Registered' of 'Back office' workflow)
                target_state = first_transition.to_state_id
                json["states"].append(
                    {
                        "value": target_state.id,
                        "label": target_state.name,
                        "isFolded": target_state.hide_in_statusbar,
                        "isSelected": False,
                    }
                )

            record.state_id_statusbar_json = json

    def _transition_button_extras(self, transition):
        """Keys the form widget ignores. A bot reads them from the same JSON."""
        keys = self._transition_action_context_keys(transition)
        parsed = {key: True for key in keys}
        default = self.env.ref(
            "riverflow.transition_action_default", raise_if_not_found=False
        )
        if default and transition.action_id == default:
            visible = bool(
                parsed.get("followup_in_days") or parsed.get("snooze_deadline_days")
            )
        else:
            visible = True
        return {
            "has_visible_fields": visible,
            "action_context_keys": keys,
        }

    def _transition_action_context_keys(self, transition):
        raw = transition.action_context
        if not raw:
            return []
        try:
            parsed = ast.literal_eval(raw) or {}
        except (ValueError, SyntaxError):
            return []
        if not isinstance(parsed, dict):
            return []
        return list(parsed.keys())

    def action_button_click(self):

        return self._prepare_transition_action()

    def action_kanban_drop(self, from_state_id, to_state_id):
        """A card dropped in another column of a board grouped by state (radar
        pipeline applicants plan, decision 127).

        A state changes only through a step, because the step screen does the
        step's work (a contract started, a date asked, a note required). So a drop
        never writes the state: it opens the screen of the first step that leads
        into the column, as a click on that button would. The record's own button
        strip decides which steps count, not the transitions table: the strip
        already hides the bot's claim and work steps from a person, and every exit
        but the hand-back while a bot run is live.

        ``from_state_id`` is the column the card was dragged from. The board can be
        older than the record, and a step chosen from a state the user did not see
        would surprise them, so a stale card is refused like a stale button."""
        self.ensure_one()
        if self.state_id.id != (from_state_id or False):
            raise UserError(_("Another user just updated this record. Please refresh and try again."))

        buttons = self.transition_buttons_json.get("buttons", [])
        offered = self.env["riverflow.transition"].browse(
            [button["context"]["transition_id"] for button in buttons]
        )
        step = offered.filtered(lambda transition: transition.to_state_id.id == to_state_id)[:1]
        if step:
            return self.with_context(transition_id=step.id)._prepare_transition_action()

        # No step leads there (for example Applicant to Offboarding). The card
        # stays, and the notice names the steps the card does offer, so the user
        # sees the way forward instead of only a refusal.
        from_name = self.state_id.name or _("Not Started")
        to_name = self.env["riverflow.state"].browse(to_state_id).name or _("Not Started")
        message = _("No step leads from %(from_state)s to %(to_state)s.", from_state=from_name, to_state=to_name)
        if buttons:
            message += " " + _(
                "Steps from %(from_state)s: %(steps)s.",
                from_state=from_name,
                steps=", ".join(button["caption"] for button in buttons),
            )
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {"message": message, "type": "warning", "sticky": False},
        }

    def write(self, vals):
        self._sync_workflow_with_state(vals)
        return super().write(vals)

    @api.model_create_multi
    def create(self, vals_list):
        for vals in vals_list:
            self._sync_workflow_with_state(vals)
        records = super().create(vals_list)
        # self.env["riverflow.auto.add.service"].auto_add_services(records)
        return records

    def _sync_workflow_with_state(self, vals):
        if "state_id" in vals:
            if vals["state_id"]:
                new_state = self.env["riverflow.state"].browse(vals["state_id"])
                vals["workflow_id"] = new_state.workflow_id.id
            else:
                vals["workflow_id"] = False

    def _compute_deadline(self):
        pass

    @api.depends("deadline", "is_end_state")
    def _compute_timing_json(self):
        Service = self.env["riverflow.service"]
        for record in self:
            if not record.deadline:
                record.timing_json = False
                continue

            date_str = record.deadline.strftime(Service.DATE_FORMAT)

            today = fields.Date.today()
            days_remaining = (record.deadline - today).days
            is_past = record.deadline < today
            is_today = record.deadline == today

            record.timing_json = {
                "relative_days": "",
                "date": date_str,
                "days_remaining": days_remaining,
                "is_past": is_past,
                "is_today": is_today,
                "is_end_state": record.is_end_state,
            }

    @api.depends("from_transition_ids", "state_id.is_end_state")
    def _compute_is_end_state(self):
        for record in self:
            if record.state_id:
                record.is_end_state = record.state_id.is_end_state
            else:
                # start transitions are not end states
                # = len(record.from_transition_ids) == 0
                # however, a blank state is considered to be 'always active' therefore not an end state
                record.is_end_state = False

    @api.depends("state_id", "workflow_id")
    def _compute_front_office_workflow_id(self):
        for record in self:
            has_front_office_workflow = record.state_id and not record.state_id.is_back_office_state
            if has_front_office_workflow:
                record.front_office_workflow_id = record.state_id.workflow_id
            else:
                # Odoo requires compute methods to set the field, or else it will disable the compute
                record.front_office_workflow_id = record.front_office_workflow_id

    # --- Child services of a record in a workflow (radar pipeline applicants
    # plan, decisions 145-147) ---------------------------------------------------
    #
    # One meaning of "child services" for every record with a workflow: for a
    # service its child services (riverflow.service overrides the method); for
    # any other record (an employee, a log entry, a ship) the services whose
    # subject it is. The closing walk of Q162 and the refusal of decision 145
    # both read it, so a state that ends an employee's employment can close his
    # services that are not started, and a transition can refuse while his
    # services are still open.

    def _workflow_child_services(self):
        """The services whose subject is this record. A service whose parent has
        the same subject is reached through that parent. Checks active and the
        tombstone explicitly: a transition can run with active_test=False."""
        self.ensure_one()
        services = self.env["riverflow.service"].search(
            [("res_model", "=", self._name), ("res_id", "=", self.id), ("is_this_a_template", "=", False)]
        )
        return services.filtered(
            lambda service: service.active
            and not service.to_be_deleted
            and not (service.parent_id.res_model == self._name and service.parent_id.res_id == self.id)
        )

    def _execute_child_transitions(self):
        """Let the child services below execute their own closing transition
        when this record entered a state that ends the case
        (``execute_child_transitions`` on the state; radar pipeline applicants
        plan, Q162; for any record with a workflow since decision 147).

        Called after a transition (``riverflow.transition.wizard.action_save``)
        and by code that moves a record into such a state without a transition
        (a bulk upload, an automatic close), so every way into the state has the
        same effect.

        Each open child service whose current state has a transition marked
        with the same value (``execute_on_parent_transitions``) executes that
        transition through its own transition wizard: its note, its deadline
        setting and its side effects run as when a person clicks it, and nothing
        writes the state directly. The whole tree is walked. A child service
        that is closed, or that this rule just closed, is descended through,
        because its open child services belong to the same finished case. A
        started child service has no marked transition: it stays open and the
        walk does not descend into it, so the person who works on it also
        decides about the services below it.
        """
        for record in self:
            signal = record.state_id.execute_child_transitions
            if signal:
                record._workflow_child_services()._execute_signal_down(signal)

    def _open_services_left_by(self, transition):
        """The open child services of this record that stay open when
        ``transition`` saves (decision 145): what the transition closes by
        itself does not count. Reads the same rule as the closing walk
        (``_open_services_left`` mirrors ``_execute_signal_down``), so the
        refusal and the walk never disagree."""
        self.ensure_one()
        signal = transition.to_state_id.execute_child_transitions
        return self._workflow_child_services()._open_services_left(signal)
