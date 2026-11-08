from odoo import _, api, fields, models


class RiverflowSendEmailChoice(models.TransientModel):
    """The choice of the chatter's Send Email button when the current state has more than one
    e-mail transition, or only transitions that send a document (plan
    riverflow/plans/chatter-send-message.md, § 5 A and decision 3)."""

    _name = "riverflow.send.email.choice"
    _description = "Choose how to send an e-mail"

    res_model = fields.Char(required=True, readonly=True)
    res_id = fields.Many2oneReference(model_field="res_model", required=True, readonly=True)
    transition_ids = fields.Many2many("riverflow.transition", readonly=True)
    offer_other_email = fields.Boolean(readonly=True)
    transition_buttons_json = fields.Json(compute="_compute_transition_buttons_json")

    @api.depends("transition_ids", "offer_other_email")
    def _compute_transition_buttons_json(self):
        for choice in self:
            buttons = []
            for index, transition in enumerate(choice.transition_ids):
                buttons.append(
                    {
                        "index": index,
                        "icon": "fa-envelope",
                        "caption": transition.name,
                        "help": transition.description,
                        "action": "action_open_transition",
                        "context": {"choice_transition_id": transition.id},
                    }
                )
            if choice.offer_other_email:
                buttons.append(
                    {
                        "index": len(buttons),
                        "icon": "fa-envelope-o",
                        "caption": _("Other e-mail"),
                        "help": _("A plain e-mail in a new Send Email service, without the document."),
                        "action": "action_other_email",
                        "context": {},
                    }
                )
            choice.transition_buttons_json = {
                "text": "",
                "workflow_icon": "",
                "is_end_state": False,
                "reload_on_close": False,
                "layout": "full_list",
                "buttons": buttons,
            }

    def _record(self):
        self.ensure_one()
        return self.env[self.res_model].browse(self.res_id)

    def action_open_transition(self):
        transition = self.transition_ids.filtered(
            lambda t: t.id == self.env.context.get("choice_transition_id")
        )
        return self._record()._riverflow_open_email_transition(transition)

    def action_other_email(self):
        return self._record()._riverflow_email_service_action()
