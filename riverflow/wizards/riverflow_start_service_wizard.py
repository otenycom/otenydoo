from odoo import models, Command, _, fields
from odoo.exceptions import UserError
from odoo.tools.misc import clean_context


class ServiceNewWizard(models.TransientModel):
    _name = "riverflow.start.service"
    _inherit = "riverflow.start.wizard"
    _description = "Service Start Transition selection Wizard"

    _workflow_model = "riverflow.service"

    def _add_template_start_transitions(self, transition_buttons, defaults_context):
        template_services = self.env["riverflow.service"].search([("is_root_a_template", "=", True)])

        # Sort alphabetically by root template name, preserving tree structure
        # within each root (root before children, ordered by display_order)
        sorted_templates = sorted(
            template_services,
            key=lambda s: (s.root_id.name.lower(), s.display_order),
        )

        # Determine placement context from the wizard defaults
        res_model = defaults_context.get("default_res_model")
        is_child_add = bool(defaults_context.get("default_parent_id"))
        Service = self.env["riverflow.service"]

        # Pre-compute which root templates are allowed so we can skip
        # entire subtrees when the root is disallowed.
        excluded_root_ids = set()
        for tpl in sorted_templates:
            if tpl.indent_level == 0:
                if not Service._template_placement_allowed(
                    tpl, res_model=res_model, is_child_add=is_child_add
                ):
                    excluded_root_ids.add(tpl.root_id.id)

        index = 0
        for template_service in sorted_templates:
            if template_service.root_id.id in excluded_root_ids:
                continue

            button_context = defaults_context.copy()
            button_context["template_service_id"] = template_service.id
            icon = template_service.workflow_id.icon or "plus"

            transition_buttons["buttons"].append(
                {
                    "index": index,
                    "is_template": True,
                    "indent_level": template_service.indent_level,
                    "icon": icon,
                    "caption": f"{template_service.indented_name}",
                    "help": "",
                    "action": "action_apply_template",
                    "context": button_context,
                }
            )
            index += 1

    def action_apply_template(self):
        self.ensure_one()

        template_service_id = self.env.context.get("template_service_id")

        # Read the deadline from the client-side input field
        project_deadline = self.env.context.get("default_project_deadline")
        # if not project_deadline:
        #     raise UserError(_("You must set the Deadline"))

        # Convert the date string if needed
        if isinstance(project_deadline, str):
            project_deadline = fields.Date.from_string(project_deadline)

        # The start screen's default_* keys describe the service the user starts:
        # its subject, its parent and the date typed on the screen. The template
        # clone also makes the sub-services of that service, and a default key
        # reaches every service it creates, so the typed date became the date of
        # every sub-service as well, whatever its own deadline mode (radar
        # pipeline applicants plan, decision 87: the same leak as in the
        # auto-add). Keep the keys the clone reads for the new service; the date
        # goes to the started service only, through the deadline argument.
        context = clean_context(self.env.context)
        for key in ("default_res_model", "default_res_id", "default_parent_id", "default_is_this_a_template"):
            if key in self.env.context:
                context[key] = self.env.context[key]
        new_services = (
            self.env["riverflow.service"]
            .with_context(context)
            ._create_services_from_template(template_service_id, project_deadline)
        )

        return {
            "type": "ir.actions.act_window",
            "res_model": "riverflow.service",
            "res_id": new_services.ids[0],
            "view_mode": "form",
            "target": "current",
        }
