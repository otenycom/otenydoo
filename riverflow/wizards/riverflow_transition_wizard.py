from datetime import timedelta

from odoo import models, fields, api, _, Command
from odoo.exceptions import UserError, ValidationError


class TransitionWizard(models.AbstractModel):
    _name = "riverflow.transition.wizard"
    # Every transition wizard can hold its OK on errors and warnings by
    # overriding _save_check(); the root view below shows the findings box.
    _inherit = ["riverflow.save.check.mixin"]
    _description = "Transition action base Wizard"

    _workflow_model = "definedInDerivedClass"

    transition_id = fields.Many2one("riverflow.transition", "Transition")
    transition_description = fields.Html("Description", compute="_compute_transition_description")
    transition_description_invisible = fields.Boolean()

    # in each derived class, define the records_to_transition_ids field to be of the correct co-model
    records_to_transition_ids = fields.Many2many("riverflow.service")  # to be overridden
    responsible_team_id = fields.Many2one(
        "res.partner",
        string="Responsible",
        help="Team or user who is assigned to this record.",
        # domain="['|', ('is_user', '=', True), ('is_riverflow_team', '=', True)]",
        domain="[('is_riverflow_team', '=', True)]",
    )

    responsible_team_id_invisible = fields.Boolean()

    internal_notes_summary = fields.Text(
        string="Top 3 Internal Notes",
        help="Recent internal notes from the record being transitioned",
        compute="_compute_internal_notes_summary",
        store=False,
    )
    internal_notes_summary_invisible = fields.Boolean()

    new_note = fields.Html(string="Internal Note")
    new_note_invisible = fields.Boolean()

    @api.model
    def default_get(self, form_fields):
        defaultValues = super().default_get(form_fields)

        transition_id = self.env.context.get("transition_id")
        transition_id = self.env["riverflow.transition"].browse(transition_id)
        defaultValues["transition_id"] = transition_id.id

        if transition_id.to_responsible_team_id:
            defaultValues["responsible_team_id"] = transition_id.to_responsible_team_id.id

        records_to_transition = self.env[self._workflow_model]
        records_to_transition_ids = []
        # if we are in a wizard that is triggered by a record,
        # as opposed to a start-transition selection wizard,
        # then use the active ids
        if self.env.context.get("active_model") == self._workflow_model:
            records_to_transition_ids = self.env.context.get("active_ids")
        if records_to_transition_ids:
            records_to_transition = records_to_transition.browse(records_to_transition_ids)

        if records_to_transition.ids:
            defaultValues["records_to_transition_ids"] = records_to_transition.ids

        self.default_get_using_records(defaultValues, records_to_transition)

        visibility_defaults = self.get_visibility_defaults(transition_id)
        # Update defaultValues with new values, without overwriting existing ones, so that
        # the defaults given by the calling action are kept (e.g. from action_context)
        for key, value in visibility_defaults.items():
            if key not in defaultValues:
                defaultValues[key] = value

        return defaultValues

    def get_visibility_defaults(self, transition_id):
        return {
            "responsible_team_id_invisible": transition_id.to_state_id.is_end_state,
            "internal_notes_summary_invisible": True,
        }

    def action_save(self):
        for wizard in self:
            transition = wizard.transition_id

            if not transition:
                raise ValidationError("Workflow Transition not specified")

            recordsToTransition = wizard.records_to_transition_ids

            createNewRecord = len(recordsToTransition) == 0
            create_vals = {}
            if createNewRecord:
                create_vals["workflow_id"] = transition.workflow_id.id
                in_memory_record = self.env[self._workflow_model].new(create_vals)

                recordsToTransition = [in_memory_record]

                action = {
                    "type": "ir.actions.act_window",
                    "res_model": self._workflow_model,
                    "res_id": 0,  # NewId value, will be updated below
                    # "views": [(self.env.ref("riverflow.view_service_form").id, "form")],
                    "view_mode": "form",
                    "target": "current",
                    "context": {},
                }
            else:
                action = {"type": "ir.actions.act_window_close"}

            # Collect the property values that need to be updated and pass
            # them in one go write(), so that the validations @api.constrains
            # get triggered on a record with all the new property values
            expected_state = transition.from_state_id
            for record in recordsToTransition:
                current_state = record.state_id

                if expected_state and current_state != expected_state:
                    raise UserError(_("Another user just updated this record. Please refresh and try again."))

                # The bot fence. A person may not move a record under a live
                # claim. bot_claim and a flagged bot open/OK reach this method
                # so the wizard still runs. Re-checked here, not only at the
                # button, because a run can start while the wizard sits open.
                # hasattr: the wizard also drives pre-bot-layer models.
                if hasattr(record, "_bot_assert_human_transition_allowed"):
                    record._bot_assert_human_transition_allowed(transition)

                write_vals = {"state_id": transition.to_state_id.id}
                self.update_write_values(record, write_vals)

                # Deadline overrides from transition action_context:
                # clear_deadline: permanently remove the deadline
                # set_deadline_to_today: freeze deadline as today (e.g. A1 Done)
                # followup_in_days: set deadline to the wizard's project_deadline
                #   (pre-filled as today + N days, user may have adjusted).
                #   A wizard without a deadline field (a custom action) gets
                #   today + N, so the key never silently does nothing.
                if self.env.context.get("clear_deadline"):
                    write_vals["use_project_deadline_from"] = "self"
                    write_vals["project_deadline"] = False
                    write_vals["days_relative_to_project"] = 0
                elif self.env.context.get("set_deadline_to_today"):
                    write_vals["use_project_deadline_from"] = "self"
                    write_vals["project_deadline"] = fields.Date.today()
                    write_vals["days_relative_to_project"] = 0
                elif self.env.context.get("followup_in_days"):
                    followup_deadline = getattr(self, "project_deadline", None) or (
                        fields.Date.today() + timedelta(days=int(self.env.context["followup_in_days"]))
                    )
                    write_vals["use_project_deadline_from"] = "self"
                    write_vals["project_deadline"] = followup_deadline
                    write_vals["days_relative_to_project"] = 0
                elif self.env.context.get("set_deadline_relative"):
                    spec = self.env.context.get("set_deadline_relative")
                    if not isinstance(spec, dict) or "from" not in spec or "days" not in spec:
                        raise UserError(_(
                            "set_deadline_relative must be a dict with from and days."
                        ))
                    write_vals["use_project_deadline_from"] = spec["from"]
                    write_vals["days_relative_to_project"] = int(spec["days"])
                    write_vals.pop("project_deadline", None)

                if not createNewRecord:
                    record.write(write_vals)
                    # Flush stored computes that depend on state_id (e.g. auto_add_trigger)
                    # so side-effects like auto-add services run before create_related_records.
                    record.flush_recordset()
                    # cancel_children: a back step whose work underneath is
                    # moot (e.g. the application goes back to be corrected).
                    # Runs before the deferred children, so a cancelled child
                    # never blocks a fresh one in the same transition.
                    if self.env.context.get("cancel_children") and hasattr(record, "_cascade_cancel_to_subtree"):
                        record._cascade_cancel_to_subtree()
                    # Create deferred template children that match the new state.
                    # Runs before create_related_records so wizards see the full child tree.
                    if hasattr(record, "_create_deferred_children"):
                        record._create_deferred_children(transition.to_state_id)
                    self.create_related_records(record)
                    # Check if this record's parent should auto-progress now that
                    # this child has transitioned (e.g. all sibling tasks done).
                    if hasattr(record, "_check_parent_auto_progress"):
                        record._check_parent_auto_progress()
                    # Cascade Done to children if this record entered a state
                    # flagged with auto_done_children_on_enter (mirror of the
                    # parent auto-progress check, opposite direction).
                    if hasattr(record, "_cascade_done_to_children"):
                        record._cascade_done_to_children()
                else:
                    # Merge write_vals into create_vals
                    create_vals.update(write_vals)

                    new_record = (
                        self.env[self._workflow_model]
                        .with_context(
                            mail_create_nosubscribe=True,  # current user not made followers of the record thread
                            mail_auto_subscribe_no_notify=True,  # recipients are not notified of the record thread
                        )
                        .create(create_vals)
                    )
                    action["res_id"] = new_record.id
                    # Flush stored computes that depend on state_id (e.g. auto_add_trigger)
                    new_record.flush_recordset()
                    self.create_related_records(new_record)

        return action

    # abstract methods
    def create_related_records(self, record):
        if not self.new_note_invisible and self.new_note:
            self.env["mail.message"].create(
                {
                    "body": self.new_note,
                    "model": self._workflow_model,
                    "res_id": record.id,
                    "message_type": "comment",
                    "subtype_id": self.env.ref("mail.mt_note").id,
                    "child_ids": [],
                    "parent_id": [],
                }
            )

    @api.depends("transition_id")
    def _compute_transition_description(self):
        for wizard in self:
            description = ""
            if wizard.transition_id.description:
                description = wizard.transition_id.description

            wizard.transition_description = description

    @api.depends("records_to_transition_ids")
    def _compute_internal_notes_summary(self):
        for wizard in self:
            # Get internal notes from the first record being transitioned
            if wizard.records_to_transition_ids and len(wizard.records_to_transition_ids) == 1:
                record = wizard.records_to_transition_ids[0]
                # Check if the record has internal_notes_summary field (from mail.thread.review.mixin)
                if hasattr(record, "internal_notes_summary"):
                    wizard.internal_notes_summary = record.internal_notes_summary
                else:
                    wizard.internal_notes_summary = False
            else:
                wizard.internal_notes_summary = False

    def default_get_using_records(self, defaultValues, records_to_transition):
        pass

    def update_write_values(self, record, vals):
        if not self.responsible_team_id_invisible:
            vals["responsible_team_id"] = self.responsible_team_id.id

    def _is_to_end_state(self):
        return self.transition_id.to_state_id.is_end_state
