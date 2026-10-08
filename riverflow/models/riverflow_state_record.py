from odoo import models, fields, api, _
from collections import defaultdict
from datetime import datetime

# Define the date format constant here instead of relying on Service class
DATE_FORMAT = "%d/%m/%Y"


class RiverflowStateRecord(models.Model):
    _name = "riverflow.state.record"
    _description = "Global View of Riverflow State"
    _order = "res_date asc,res_model,res_sortable_name,res_id,is_subject desc,root_name,root_id,display_order,deadline"
    _inherit = [
        "riverflow.highlight.row.mixin",
    ]
    _oteny_audit_ignore = True

    active = fields.Boolean(
        default=True,
        help="Set active to false to archive the service",
        compute="_compute_active",
        store=True,
    )
    is_subject = fields.Boolean(
        string="Is Subject",
        compute="_compute_is_subject",
        store=True,
    )
    record_type = fields.Selection(
        [
            ("single", "Single"),
            ("start", "Start"),
            ("end", "End"),
        ],
        string="Record Type",
        default="single",
        required=True,
        help="Indicates whether this is a single state record or part of a start/end pair for subjects with date ranges",
    )

    @api.depends("root_id")
    def _compute_is_subject(self):
        for record in self:
            # services have a root_id, but subjects dont
            record.is_subject = record.root_id == 0

    @api.model
    def _selection_target_model(self):
        return [(model.model, model.name) for model in self.env["ir.model"].sudo().search([])]

    # Fields to identify the record
    name = fields.Char(
        string="Name",
        index=True,
        compute="_compute_name",
        store=True,
    )
    sortable_name = fields.Char(
        string="Sortable Name",
        compute="_compute_sortable_name",
        store=True,
    )
    display_name = fields.Char(
        string="Display Name",
        index=True,
        compute="_compute_display_name",
        store=True,
    )
    master_model = fields.Char(string="Master Model", required=True, index=True)
    master_res_id = fields.Integer(string="Master Record ID", required=True, index=True)
    master_record_id_computed = fields.Integer(
        string="Master Record ID Computed",
        compute="_compute_master_record_id_computed",
        store=True,
        index=True,
        help="Computed field used as inverse for One2many relationship from master records",
    )
    master_record_reference = fields.Reference(
        string="Master Record Reference",
        selection="_selection_target_model",
        compute="_compute_master_record_reference",
        readonly=True,
    )

    @api.depends("master_res_id")
    def _compute_master_record_id_computed(self):
        """Compute master_record_id_computed field for One2many inverse relationship.

        This field is used as the inverse_name for the One2many field state_record_ids
        in the tracker mixin, enabling efficient queries from master records to their state records.
        """
        for record in self:
            record.master_record_id_computed = record.master_res_id

    @api.depends("master_model", "master_res_id")
    def _compute_master_record_reference(self):
        for record in self:
            if record.master_model and record.master_res_id:
                record.master_record_reference = f"{record.master_model},{record.master_res_id}"
            else:
                record.master_record_reference = False

    # --- service fields
    res_id = fields.Integer(
        string="Subject of Service ID",
        required=False,
        compute="_compute_res_id",
        store=True,
    )
    res_model = fields.Char(
        string="Subject of Service Model Name",
        compute="_compute_res_model",
        index=True,
        store=True,
    )
    res_name = fields.Char(
        string="Subject of Service",
        compute="_compute_res_name",
        store=True,
        index="trigram",
    )
    res_sortable_name = fields.Char(
        string="Subject of Service Sortable Name",
        compute="_compute_res_sortable_name",
        store=True,
        index="trigram",
    )
    res_date = fields.Date(
        string="Subject Date",
        compute="_compute_res_date",
        store=True,
        index=True,
        help="Deadline date of the subject record, used for sorting. All records in a tree share the same res_date.",
    )

    root_id = fields.Integer(
        string="Root ID",
        index=True,
        compute="_compute_root_id",
        store=True,
    )
    root_name = fields.Char(
        string="Root Name",
        index=True,
        compute="_compute_root_name",
        store=True,
    )
    indent_level = fields.Integer(
        string="Indent Level",
        compute="_compute_indent_level",
        store=True,
    )
    indented_name = fields.Char("Record", compute="_compute_indented_name", store=False)

    deadline = fields.Date(
        "Deadline",
        help="Deadline based on the project-deadline and the relative day of this service",
        index=True,
        compute="_compute_deadline",
        inverse="_inverse_deadline",
        store=True,
    )

    def _inverse_deadline(self):
        """Allow manual override of computed deadline field

        When a deadline is written on a state record:
        - If service_id is set: propagate the deadline change to the service
        - If service_id is False: revert to computed value (no manual override allowed)
        """
        for record in self:
            if record.service_id:
                # Write the new deadline to the related service
                record.service_id.deadline = record.deadline
            else:
                # No service_id: revert to computed value by recomputing the field
                # Since there's no service, the computed value will be False
                computed_deadline = self._compute_deadline_for_record(record)
                if record.deadline != computed_deadline:
                    record.deadline = computed_deadline

    end_date = fields.Date(
        "End Date",
        compute="_compute_end_date",
        inverse="_inverse_end_date",
        store=True,
    )

    def _inverse_end_date(self):
        """Allow manual override of computed end_date field

        When an end_date is written on a state record:
        - If service_id is set: propagate the end_date change to the service
        - If service_id is False: revert to computed value (no manual override allowed)
        """
        for record in self:
            if record.service_id:
                # Write the new end_date to the related service
                record.service_id.end_date_for_calendar = record.end_date
            else:
                # No service_id: revert to computed value by recomputing the field
                # Without a service, the computed value falls back to the deadline
                computed_end_date = self._compute_end_date_for_record(record)
                if record.end_date != computed_end_date:
                    record.end_date = computed_end_date

    deadline_formatted = fields.Char("Deadline Formatted", compute="_compute_deadline_formatted", store=False)

    timing_json = fields.Json(
        "Timing",
        compute="_compute_timing_json",
        store=False,
    )
    display_order = fields.Integer(
        "Display Order",
        default=1,
        index=True,
        required=True,
        compute="_compute_display_order",
        store=True,
    )

    # Fields from RiverflowWorkflowStateMixin
    workflow_id = fields.Many2one(
        "riverflow.workflow",
        string="Workflow",
        compute="_compute_workflow_id",
        readonly=True,
        store=True,
    )
    current_workflow_name = fields.Char(
        "Workflow name",
        related="workflow_id.name",
        store=True,
        index=True,
    )
    state_id = fields.Many2one(
        "riverflow.state",
        string="Workflow State",
        compute="_compute_state_id",
        store=True,
        readonly=True,
        index=True,
    )
    state_name = fields.Char("State name", related="state_id.name", store=True, index=True)
    state_json = fields.Json(
        string="State",
        readonly=True,
        compute="_compute_state_json",
    )
    is_end_state = fields.Boolean(
        "Is End State",
        compute="_compute_is_end_state",
        store=True,
        index=True,
    )

    # fields from MailThreadReviewMixin
    color_int = fields.Integer(
        related="state_id.color_int",
    )
    internal_notes_summary = fields.Text(
        string="Internal Notes",
        compute="_compute_internal_notes_summary",
        index="trigram",
        store=True,
    )
    external_messages_summary = fields.Text(
        string="External Messages",
        compute="_compute_external_messages_summary",
        index="trigram",
        store=True,
    )
    unreviewed_message_count = fields.Integer(
        string="Review",
        compute="_compute_unreviewed_message_count",
        index=True,
        store=True,
    )

    responsible_team_id = fields.Many2one(
        "res.partner",
        string="Responsible Team",
        help="Team executing the workflow. This team is also responsible for reviewing external messages.",
        index=True,
        compute="_compute_responsible_team_id",
        store=True,
    )

    # Override from RiverflowHighlightRowMixin to make it computed
    user_write_date = fields.Datetime(
        "User Write Date",
        help="Timestamp of the last user-initiated modification (excludes automated computed field updates).",
        compute="_compute_user_write_date",
        store=True,
        readonly=True,
    )

    company_id = fields.Many2one(
        "res.company",
        string="Company",
        compute="_compute_company_id",
        store=True,
        required=False,
    )

    service_id = fields.Many2one(
        "riverflow.service",
        string="Service",
        compute="_compute_service_id",
        store=True,
        index=True,
        help="The service to which this record applies",
        ondelete="cascade",
    )

    tag_ids = fields.Many2many(
        comodel_name="riverflow.service.tag",
        relation="riverflow_state_record_service_tag_rel",
        column1="state_record_id",
        column2="tag_id",
        string="Tags",
        compute="_compute_tag_ids",
        ondelete="cascade",
        store=True,  # Needed for compute to be triggered
    )

    check_result_ids = fields.One2many(
        comodel_name="riverflow.check.result",
        inverse_name="state_record_id",
        string="Issues",
    )

    @api.depends("master_model", "master_res_id")
    def _compute_service_id(self):
        for record in self:
            if record.master_model == "riverflow.service":
                record.service_id = record.master_res_id
            else:
                record.service_id = False

    def _inverse_service_id(self):
        for record in self:
            if record.service_id:
                record.master_model = "riverflow.service"
                record.master_res_id = record.service_id.id
            else:
                record.master_model = False
                record.master_res_id = False

    @api.depends("service_id.indented_name", "name")
    def _compute_indented_name(self):
        for slave in self:
            if slave.indent_level == 0:
                slave.indented_name = slave.name
            else:
                slave.indented_name = "%s%s" % (
                    "\N{NO-BREAK SPACE}\N{NO-BREAK SPACE}\N{NO-BREAK SPACE}\N{NO-BREAK SPACE}"
                    * slave.indent_level,
                    slave.name,
                )

    @api.depends("service_id.internal_notes_summary")
    def _compute_internal_notes_summary(self):
        for record in self:
            record.internal_notes_summary = self._compute_internal_notes_summary_for_record(record)

    @api.model
    def _compute_internal_notes_summary_for_record(self, record):
        if record.service_id:
            return record.service_id.internal_notes_summary
        return False

    @api.depends("service_id.external_messages_summary")
    def _compute_external_messages_summary(self):
        for record in self:
            record.external_messages_summary = self._compute_external_messages_summary_for_record(record)

    @api.model
    def _compute_external_messages_summary_for_record(self, record):
        if record.service_id:
            return record.service_id.external_messages_summary
        return False

    @api.depends("service_id.unreviewed_message_count")
    def _compute_unreviewed_message_count(self):
        for record in self:
            record.unreviewed_message_count = self._compute_unreviewed_message_count_for_record(record)

    @api.model
    def _compute_unreviewed_message_count_for_record(self, record):
        if record.service_id:
            return record.service_id.unreviewed_message_count
        return 0

    @api.depends("service_id.user_write_date")
    def _compute_user_write_date(self):
        for record in self:
            record.user_write_date = self._compute_user_write_date_for_record(record)

    @api.model
    def _compute_user_write_date_for_record(self, record):
        if record.service_id:
            return record.service_id.user_write_date
        return False

    @api.depends("service_id.tag_ids")
    def _compute_tag_ids(self):
        for record in self:
            # Assign only a change: assigning a Many2many diffs it against the
            # stored relation, one query per record (11616 on a radar billing
            # month move, 2026-10-08, for tags that did not change).
            tags = self._compute_tag_ids_for_record(record) or self.env["riverflow.service.tag"]
            if record.tag_ids != tags:
                record.tag_ids = tags

    @api.model
    def _compute_tag_ids_for_record(self, record):
        if record.service_id:
            return record.service_id.tag_ids
        return False

    def _compute_state_json(self):
        # for rendering just the state name and workflow name
        for record in self:
            wf_state_text = record.current_workflow_name or ""
            if wf_state_text:
                if not record.state_name:
                    wf_state_text = "Not Started"
                else:
                    wf_state_text = record.state_name  # " | ".join([wf_state_text, record.state_name])

            # todo: store the icon so its not a lookup
            icon = record.workflow_id.icon or ""
            is_end_state = record.is_end_state == True

            state_json = {
                "text": wf_state_text,
                "workflow_icon": icon,
                "is_end_state": is_end_state,
                # this is not a start transition, so we can refresh the underlying list/form view
                "reload_on_close": True,
                "buttons": [],
            }

            record.state_json = state_json

    @api.depends("deadline")
    def _compute_deadline_formatted(self):
        for record in self:
            if record.deadline:
                # Format the deadline date
                record.deadline_formatted = datetime.strftime(record.deadline, DATE_FORMAT)
            else:
                record.deadline_formatted = False

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

    @api.depends("deadline", "is_subject")
    def _compute_res_date(self):
        """Compute res_date to be the deadline of the subject record.
        For subjects: use their own deadline
        For services: use the deadline of their subject (via res_id lookup)
        """
        for record in self:
            if record.is_subject:
                # Subject records use their own deadline
                record.res_date = record.deadline
            elif record.service_id:
                # For service records, try to get the deadline from the subject
                # First check if we can access it through service relationships
                # (this will be overridden in inheriting modules for better performance)
                subject_deadline = self._get_subject_deadline_for_service(record)
                record.res_date = subject_deadline if subject_deadline else record.deadline
            else:
                record.res_date = record.deadline

    def _get_subject_deadline_for_service(self, record):
        """Get the deadline of the subject for a service record.
        This method can be overridden in inheriting modules for better performance.
        """
        if record.res_model and record.res_id:
            # Find the subject record (where master_model/master_res_id matches this service's res_model/res_id)
            subject_record = self.search(
                [
                    ("master_model", "=", record.res_model),
                    ("master_res_id", "=", record.res_id),
                    ("is_subject", "=", True),
                ],
                limit=1,
            )
            if subject_record:
                return subject_record.deadline
        return False

    @api.depends("service_id.workflow_id")
    def _compute_workflow_id(self):
        for record in self:
            record.workflow_id = self._compute_workflow_id_for_record(record)

    @api.model
    def _compute_workflow_id_for_record(self, record):
        if record.service_id:
            return record.service_id.workflow_id
        return False

    @api.depends("service_id.state_id")
    def _compute_state_id(self):
        for record in self:
            # print(f"old state_id: {record.state_id.name}")
            record.state_id = self._compute_state_id_for_record(record)
            # print(f"new state_id: {record.state_id.name}")

    @api.model
    def _compute_state_id_for_record(self, record):
        if record.service_id:
            return record.service_id.state_id
        return False

    @api.depends("service_id.is_end_state")
    def _compute_is_end_state(self):
        for record in self:
            record.is_end_state = self._compute_is_end_state_for_record(record)

    @api.model
    def _compute_is_end_state_for_record(self, record):
        if record.service_id:
            return record.service_id.is_end_state
        return False

    @api.depends("service_id.display_name", "name")
    def _compute_display_name(self):
        for record in self:
            record.display_name = self._compute_display_name_for_record(record)

    @api.model
    def _compute_display_name_for_record(self, record):
        if record.service_id:
            return record.service_id.display_name
        return record.name

    @api.depends("service_id.active")
    def _compute_active(self):
        for record in self:
            record.active = self._compute_active_for_record(record)

    @api.model
    def _compute_active_for_record(self, record):
        if record.service_id:
            return record.service_id.active
        return True

    @api.depends("service_id.deadline")
    def _compute_deadline(self):
        for record in self:
            record.deadline = self._compute_deadline_for_record(record)

    @api.model
    def _compute_deadline_for_record(self, record):
        if record.service_id:
            return record.service_id.deadline
        return False

    @api.depends("service_id.end_date", "deadline")
    def _compute_end_date(self):
        for record in self:
            record.end_date = self._compute_end_date_for_record(record)

    @api.model
    def _compute_end_date_for_record(self, record):
        if record.service_id:
            return record.service_id.end_date_for_calendar
        return record.deadline

    @api.depends("service_id.root_name")
    def _compute_root_name(self):
        for record in self:
            if record.service_id:
                record.root_name = record.service_id.root_name
            else:
                record.root_name = False

    @api.depends("service_id.root_id")
    def _compute_root_id(self):
        for record in self:
            if record.service_id:
                record.root_id = record.service_id.root_id
            else:
                record.root_id = False

    @api.depends("service_id.display_order")
    def _compute_display_order(self):
        for record in self:
            if record.service_id:
                record.display_order = record.service_id.display_order
            else:
                record.display_order = 1

    @api.depends("service_id.res_id")
    def _compute_res_id(self):
        for record in self:
            if record.service_id:
                record.res_id = record.service_id.res_id
            else:
                record.res_id = record.master_res_id

    @api.depends("service_id.res_model")
    def _compute_res_model(self):
        for record in self:
            if record.service_id:
                record.res_model = record.service_id.res_model
            else:
                record.res_model = record.master_model

    @api.depends("service_id.res_name", "name")
    def _compute_res_name(self):
        for record in self:
            if record.service_id:
                record.res_name = record.service_id.res_name
            else:
                record.res_name = record.name

    @api.depends("service_id.res_sortable_name", "sortable_name")
    def _compute_res_sortable_name(self):
        for record in self:
            if record.service_id:
                record.res_sortable_name = record.service_id.res_sortable_name
            else:
                record.res_sortable_name = record.sortable_name

    @api.depends("service_id.responsible_team_id")
    def _compute_responsible_team_id(self):
        for record in self:
            record.responsible_team_id = self._compute_responsible_team_id_for_record(record)

    @api.model
    def _compute_responsible_team_id_for_record(self, record):
        if record.service_id:
            return record.service_id.responsible_team_id
        return False

    @api.depends("service_id.company_id")
    def _compute_company_id(self):
        for record in self:
            record.company_id = self._compute_company_id_for_record(record)

    @api.model
    def _compute_company_id_for_record(self, record):
        if record.service_id:
            return record.service_id.company_id
        return False

    @api.depends("service_id.name")
    def _compute_name(self):
        for record in self:
            record.name = self._compute_name_for_record(record)

    @api.depends("service_id.sortable_name")
    def _compute_sortable_name(self):
        for record in self:
            record.sortable_name = self._compute_sortable_name_for_record(record)

    @api.model
    def _compute_name_for_record(self, record):
        if record.service_id:
            return record.service_id.name
        return False

    @api.model
    def _compute_sortable_name_for_record(self, record):
        if record.service_id:
            return record.service_id.sortable_name
        return False

    @api.depends("service_id.indent_level")
    def _compute_indent_level(self):
        for record in self:
            if record.service_id:
                record.indent_level = record.service_id.indent_level
                if record.res_model != False:
                    # services that that are children of a subject-record need to be indented + 1 so they appear as children
                    record.indent_level += 1
            else:
                record.indent_level = 0

    def action_view_master_record(self):
        action = {
            "name": f"View {self.name}",
            "type": "ir.actions.act_window",
            "res_model": self.master_model,
            "res_id": self.master_res_id,
            "target": "current",
            "view_mode": "form",
            "views": [[False, "form"]],
        }
        return action

    def row_click(self):
        return self.action_view_master_record()

    @api.model
    def action_recompute_company_id(self):
        """Recompute company_id for all state records.
        This is a maintenance action to fix records where company_id was not properly set.
        """
        records = self.search([])
        records._compute_company_id()
        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": _("Company Recompute"),
                "message": _("Recomputed company for %d state records", len(records)),
                "sticky": False,
                "type": "success",
            },
        }
