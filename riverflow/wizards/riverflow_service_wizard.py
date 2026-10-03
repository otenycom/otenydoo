from datetime import timedelta

from odoo import api, models, fields, Command


class ServiceWizard(models.TransientModel):
    _name = "riverflow.service.wizard"  #
    _inherit = "riverflow.transition.wizard"
    _description = "Service Wizard"

    _workflow_model = "riverflow.service"
    records_to_transition_ids = fields.Many2many("riverflow.service")

    # from service record
    name = fields.Char("Service Name")
    name_invisible = fields.Boolean()

    days_relative_to_project_invisible = fields.Boolean()
    use_project_deadline_from = fields.Selection(
        selection="_selection_use_project_deadline_from",
        string="Deadline From",
        required=False,
        default="self",
    )

    @api.model
    def _selection_use_project_deadline_from(self):
        """Mirror the selection from riverflow.service so the wizard always
        accepts every value the service model can store."""
        service_field = self.env["riverflow.service"]._fields["use_project_deadline_from"]
        return service_field._description_selection(self.env)

    use_project_deadline_from_invisible = fields.Boolean()
    use_project_deadline_from_options = fields.Json(default=list)

    project_deadline = fields.Date(
        "Project deadline",
        help="The services are timed relative to this deadline",
    )
    project_deadline_invisible = fields.Boolean()

    days_relative_to_project = fields.Integer("Relative Deadline (days)")
    days_relative_to_project_invisible = fields.Boolean()

    # the container of the service (log_entry, employee, etc)
    res_id = fields.Integer(string="Subject of Service ID", required=False)
    res_model = fields.Char(
        string="Subject of Service Model Name",
    )

    tag_ids = fields.Many2many("riverflow.service.tag", string="Tags")
    tag_ids_invisible = fields.Boolean()

    def _save_check(self):
        """Warn when the user moves a service to an end state by hand while
        its state would complete it by itself once all child services are
        done (auto_progress_on_children_done), and children are still open.

        Only a manual move to a normal end state warns: going back, or
        cancelling, does not bypass the auto-progress mechanism."""
        findings = super()._save_check()
        service = self.records_to_transition_ids[:1]
        to_state = self.transition_id.to_state_id
        if (
            service
            and service.state_id.auto_progress_on_children_done
            and service.child_ids
            and to_state
            and to_state.is_end_state
            and not to_state.is_cancelled_state
        ):
            incomplete = service.child_ids.filtered(
                lambda c: c.active and not c.is_end_state
            )
            if incomplete:
                names = ", ".join(
                    f"{c.name} ({c.state_id.name})" for c in incomplete
                )
                findings.append({
                    "level": "warning",
                    "kind": "incomplete_children",
                    "message": (
                        "This service will complete automatically when all "
                        "child services are done. The following child services "
                        f"are not yet completed: {names}. "
                        "Complete the child services first, or confirm below."
                    ),
                })
        return findings

    def default_get_using_records(self, defaultValues, records_to_transition):
        if not "project_deadline" in defaultValues:
            defaultValues["project_deadline"] = fields.Date.today()

        # followup_in_days (from transition action_context): pre-fill deadline
        # so the user sees and can adjust the follow-up date before confirming.
        followup_days = self.env.context.get("followup_in_days")
        if followup_days:
            defaultValues["project_deadline"] = fields.Date.today() + timedelta(
                days=int(followup_days)
            )
            defaultValues["use_project_deadline_from"] = "self"
            defaultValues["project_deadline_invisible"] = False

        super().default_get_using_records(defaultValues, records_to_transition)

        if len(records_to_transition) == 0:
            ctx = self.env.context
            parent_id = self.env["riverflow.service"].browse(ctx.get("default_parent_id", 0))
            res_model = ctx.get("default_res_model", False)
            res_id = ctx.get("default_res_id", 0)
            use_project_deadline_from_options = self.env[
                "riverflow.service"
            ].calculate_use_project_deadline_from_options_for_new_service(
                parent_id, res_model, res_id, parent_id.is_root_a_template
            )
            defaultValues["use_project_deadline_from_options"] = use_project_deadline_from_options

        else:
            # This is not a stored field, so it is not in the defaultValues
            # but it is needed for the filterable_selection widget
            defaultValues["use_project_deadline_from_options"] = records_to_transition[
                0
            ].use_project_deadline_from_options

    def update_write_values(self, service, vals):
        super().update_write_values(service, vals)
        # Only push res_model/res_id into write_vals when creating a new
        # service (start-transition wizard).  For existing records the subject
        # is already set and the wizard fields are empty defaults — writing
        # them would blank out the service's subject.
        if self.res_model:
            vals["res_id"] = self.res_id
            vals["res_model"] = self.res_model

        if not self.env.context.get("name_readonly") and self.name:
            vals["name"] = self.name

        if not self.days_relative_to_project_invisible:
            vals["days_relative_to_project"] = self.days_relative_to_project

        if not self.use_project_deadline_from_invisible:
            vals["use_project_deadline_from"] = self.use_project_deadline_from
            if self.use_project_deadline_from == "self":
                vals["project_deadline"] = self.project_deadline
                vals["days_relative_to_project"] = 0

        if not self.tag_ids_invisible:
            vals["tag_ids"] = [Command.set(self.tag_ids.ids)]

    def get_visibility_defaults(self, transition_id):
        visibility_defaults = super().get_visibility_defaults(transition_id)
        # Show the timing fields even in end-states, as an end-state can also be a start-start, and
        # we require a date for each service

        # # todo: consider a computed field 'hide_timing_fields' in the state and/or transition to hide the fields
        # hide_timing_fields = (
        #     transition_id.to_state_id.is_end_state or transition_id.to_state_id.is_back_office_state
        # )
        # visibility_defaults["project_deadline_invisible"] = hide_timing_fields
        # visibility_defaults["use_project_deadline_from_invisible"] = hide_timing_fields
        # visibility_defaults["days_relative_to_project_invisible"] = hide_timing_fields

        return visibility_defaults
