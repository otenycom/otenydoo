from odoo import models, fields, api, _
from odoo.addons.riverflow.models.riverflow_transition_mixin import (
    RiverflowTransitionMixin,
)


class StartWizard(RiverflowTransitionMixin):
    _name = "riverflow.start.wizard"
    _description = "Base Start Transition selection Wizard"

    def _default_start_transition_ids(self):
        domain = [("from_state_id", "=", False), ("model", "=", self._workflow_model)]
        if self.env.context.get("recurring_only"):
            domain.append(("workflow_id.is_recurring", "=", True))
        return self.env["riverflow.transition"].search(domain, order="workflow_name,sequence,id")

    start_transition_ids = fields.Many2many("riverflow.transition", default=_default_start_transition_ids)

    transition_buttons_json = fields.Json(
        "Start Transitions", compute="_compute_transition_buttons_json", store=False
    )

    @api.depends("start_transition_ids")
    def _compute_transition_buttons_json(self):
        wizard = self
        transition_buttons = {
            "text": "",
            "workflow_icon": "",
            "is_end_state": False,
            "reload_on_close": False,
            "layout": "full_list",
            "buttons": [],
        }

        # copy over all default values from fields, such as the the default parent FK,
        # from env.context into context for the new action
        defaults_context = {}
        for key, value in self.env.context.items():
            if key.startswith("default_"):
                defaults_context[key] = value

        self._add_template_start_transitions(transition_buttons, defaults_context)

        offset = len(transition_buttons["buttons"])

        for index, transition in enumerate(wizard.start_transition_ids):
            transition_id = transition.id.origin if isinstance(wizard.id, api.NewId) else int(transition.id)

            button_context = defaults_context.copy()
            button_context["transition_id"] = transition_id
            icon = transition.to_state_id.workflow_id.icon
            if not icon:
                icon = "plus"  # for 'create new record'

            caption = f"{transition.workflow_name} - {transition.name}"

            transition_buttons["buttons"].append(
                {
                    "index": index + offset,
                    "icon": icon,
                    "caption": caption,
                    "help": transition.description,
                    "action": "action_start_transition",
                    "context": button_context,
                }
            )

        wizard.transition_buttons_json = transition_buttons

    def action_start_transition(self):
        return self._prepare_transition_action()

    def _add_template_start_transitions(self, transition_buttons, defaults_context):
        pass
