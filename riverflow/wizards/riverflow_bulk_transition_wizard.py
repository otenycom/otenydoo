from odoo import _, api, fields, models
from odoo.exceptions import UserError


class BulkTransitionWizard(models.TransientModel):
    """Run Transition on many records (radar plan credential-end-follow-up, D9a).

    The action Run Transition in a list opens this chooser for the selected
    records. It lists the transitions with "Run on Many Records" that leave the
    state of at least one record and that the record offers, each with the number
    of records it runs on. A choice opens the transition's own screen, the same as
    after a click on its button, for those records; the screen names the records
    that are left out (a warning of the save check), and OK runs the transition on
    every record or on none (riverflow.transition.wizard.action_save).
    """

    _name = "riverflow.bulk.transition.wizard"
    _description = "Run Transition"

    res_model = fields.Char(required=True)
    res_ids = fields.Json(default=list)
    record_count = fields.Integer(compute="_compute_record_count")
    line_ids = fields.One2many("riverflow.bulk.transition.line", "wizard_id", string="Transitions")
    note = fields.Text(readonly=True)

    @api.depends("res_ids")
    def _compute_record_count(self):
        for wizard in self:
            wizard.record_count = len(wizard.res_ids or [])

    @api.model
    def _open_for(self, records, note=False):
        """The chooser for ``records``, records of one workflow model. A Radar row
        (riverflow.state.record) stands for its service; a row without a service
        is left out, as in Update Deadline."""
        if records._name == "riverflow.state.record":
            records = records.mapped("service_id")
        if not records:
            raise UserError(_("Select at least one record with a workflow."))
        lines = self._transition_lines(records)
        if not lines:
            raise UserError(
                _("No transition can run on these %(count)s records at once.", count=len(records))
            )
        wizard = self.create(
            {
                "res_model": records._name,
                "res_ids": records.ids,
                "note": note or False,
                "line_ids": [fields.Command.create(vals) for vals in lines],
            }
        )
        if len(wizard.line_ids) == 1 and not note:
            return wizard.line_ids.action_choose()
        return {
            "type": "ir.actions.act_window",
            "name": _("Run Transition"),
            "res_model": self._name,
            "res_id": wizard.id,
            "view_mode": "form",
            "views": [(False, "form")],
            "target": "new",
        }

    @api.model
    def _transition_lines(self, records):
        transitions = self.env["riverflow.transition"].search(
            [("allow_bulk", "=", True), ("from_state_id", "in", records.mapped("state_id").ids)]
        )
        lines = []
        for transition in transitions.sorted(lambda t: (t.sequence, t.id)):
            count = len(self._eligible(records, transition))
            if count:
                lines.append({"transition_id": transition.id, "eligible_count": count})
        return lines

    @api.model
    def _eligible(self, records, transition):
        return records.filtered(
            lambda r: r.state_id == transition.from_state_id and r._is_transition_offered(transition)
        )

    @api.model
    def _subject_label(self, record):
        """How a left-out record is named: a service by its subject and its own
        name ("Aurora (21): Collect Rijnvaartverklaring"), because many services
        of one workflow share one name; another record by its display name."""
        subject = record.res_name if "res_name" in record._fields else False
        return "%s: %s" % (subject, record.display_name) if subject else record.display_name

    def _records(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_ids or []).exists()

    def _open_transition(self, transition):
        """The screen of ``transition`` for the records that can take it, with the
        others named as warnings."""
        self.ensure_one()
        records = self._records()
        eligible = self._eligible(records, transition)
        if not eligible:
            raise UserError(_("No selected record can take %(transition)s now.", transition=transition.name))
        skipped = []
        for record in records - eligible:
            if record.state_id != transition.from_state_id:
                reason = _("Left out: it is in %(state)s, not in %(from)s.",
                           state=record.state_id.name, **{"from": transition.from_state_id.name})
            else:
                reason = _("Left out: %(transition)s does not fit this record now.", transition=transition.name)
            skipped.append({"subject": self._subject_label(record), "message": reason})
        action = eligible.with_context(transition_id=transition.id)._prepare_transition_action()
        action["context"] = dict(action.get("context") or {}, riverflow_bulk=True, riverflow_bulk_skipped=skipped)
        if len(eligible) > 1:
            action["name"] = _("%(name)s (%(count)s records)", name=action.get("name") or transition.name,
                               count=len(eligible))
        return action


class BulkTransitionLine(models.TransientModel):
    _name = "riverflow.bulk.transition.line"
    _description = "Run Transition: one transition"
    _order = "id"

    wizard_id = fields.Many2one("riverflow.bulk.transition.wizard", required=True, ondelete="cascade")
    transition_id = fields.Many2one("riverflow.transition", required=True, readonly=True)
    eligible_count = fields.Integer(readonly=True)
    label = fields.Char(compute="_compute_label")

    @api.depends("transition_id", "eligible_count", "wizard_id.record_count")
    def _compute_label(self):
        for line in self:
            line.label = _(
                "%(transition)s (%(from)s → %(to)s): %(count)s of %(total)s",
                transition=line.transition_id.name,
                count=line.eligible_count,
                total=line.wizard_id.record_count,
                **{"from": line.transition_id.from_state_id.name, "to": line.transition_id.to_state_id.name},
            )

    def action_choose(self):
        self.ensure_one()
        return self.wizard_id._open_transition(self.transition_id)
