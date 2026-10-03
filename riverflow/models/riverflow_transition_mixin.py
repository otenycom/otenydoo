from odoo import models, _
from odoo.exceptions import UserError
import ast


class RiverflowTransitionMixin(models.AbstractModel):
    _name = "riverflow.transition.mixin"
    _description = "Base class with function to create an action to open a transition action wizard"

    def prepare_transition_action(self):
        """Public /json/2/ door. Underscore methods are private on that pipe."""
        return self._prepare_transition_action()

    def _check_wizard_carries_this_model(self, transition, res_model):
        """Refuse a wizard that cannot carry this record.

        A transition wizard resolves its records through ``_workflow_model``. A
        wizard for another model sees no records, builds an empty in-memory
        record, and then fails the state check with the misleading "Another
        user just updated this record". Name the real cause at the button.
        A wizard that starts a transition (``self._transient``) has no record.
        """
        if self._transient:
            return
        wizard_model = getattr(self.env[res_model], "_workflow_model", None)
        if not wizard_model or wizard_model == "definedInDerivedClass":
            return
        if wizard_model != self._name:
            raise UserError(_(
                "Transition %(transition)s opens a %(wizard)s wizard, but this "
                "record is a %(record)s. Give the transition an action whose "
                "wizard applies to %(record)s.",
                transition=transition.name, wizard=wizard_model, record=self._name,
            ))

    def _prepare_transition_action(self):
        transition_id = self.env.context.get("transition_id")
        transition = self.env["riverflow.transition"].browse(transition_id)
        if not transition:
            raise UserError(_("No transition selected."))

        isWizard = self._transient
        if not isWizard:  # start transition selection wizard has no state_id
            current_state = self.state_id
            expected_state = transition.from_state_id

            if expected_state and current_state != expected_state:
                raise UserError(_("Another user just updated this record. Please refresh and try again."))

            # Refuse at the BUTTON, not after the user has filled the wizard in, when a bot is
            # mid-run on this record (the wizard's own action_save re-checks — a run can start
            # while the screen is open). hasattr: this mixin also serves models that predate the
            # bot layer.
            if len(self.ids) == 1 and hasattr(self, "_bot_assert_human_transition_allowed"):
                self._bot_assert_human_transition_allowed(transition)

        action_context = self._prepare_action_context(transition)
        action_context["transition_id"] = transition.id
        effects = list(action_context.keys())
        if self.env.context.get("riverflow_bot_caller"):
            action_context["riverflow_bot_caller"] = True

        # Resolve the wizard model before building defaults, so we can
        # filter to only fields the wizard declares.
        odoo_view = transition.action_id.odoo_view
        if "." not in odoo_view:
            odoo_view = f"riverflow.{odoo_view}"
        view = self.sudo().env.ref(odoo_view)
        res_model = view.model
        self._check_wizard_carries_this_model(transition, res_model)

        defaults_context = {}
        if isWizard:
            # start transition selection wizard; carry over the default field values passed in by the caller
            for key, value in self.env.context.items():
                if key.startswith("default_"):
                    defaults_context[key] = value
        elif len(self.ids) == 1:
            action_context["active_model"] = self._name
            action_context["active_id"] = self.id
            action_context["active_ids"] = self.ids
            # Pre-populate the wizard with current entity field values.
            # Access ALL entity fields (getattr triggers stored computes that
            # must fire for state_record tracker consistency), but only include
            # fields the wizard model declares in the context defaults. Entity-
            # only fields (is_service_with_journey, credential_ids, supply_unit_price,
            # etc.) must not leak into context where they pollute create() calls
            # on unrelated models during action_save.
            wizard_fields = set(self.env[res_model]._fields.keys())
            # A seam login often cannot read catalog rows (ir.model).
            # Read defaults as sudo. Later verbs still run as the user.
            source = self.sudo()
            for field_name in source._fields:
                field = source._fields[field_name]
                value = getattr(source, field_name)
                converted_value = field.convert_to_cache(value, source)
                if field_name in wizard_fields:
                    defaults_context["default_" + field_name] = converted_value

        action_context.update(defaults_context)

        action = {
            "type": "ir.actions.act_window",
            "name": transition._wizard_dialog_title(),
            "res_model": res_model,
            "view_mode": "form",
            "views": [(view.id, "form")],
            "target": "new",
            "context": action_context,
            "effects": [key for key in effects if key != "transition_id"],
        }

        return action

    def _prepare_action_context(self, transition):
        action_context = {}
        if transition.action_context:
            try:
                action_context = ast.literal_eval(transition.action_context) or {}
            except (ValueError, SyntaxError) as e:
                raise UserError(
                    _(
                        f"Bad action_context '{transition.action_context}', for transition '{transition.name}': {str(e)}"
                    )
                ) from e
            action_context = self._process_references(transition, action_context)
        return action_context

    def _process_references(self, transition, action_context):
        updated_context = {}
        for key, value in action_context.items():
            if key.endswith("_id_ref"):
                full_xml_id = value
                transition_xml_id = transition.get_external_id().get(transition.id, "")
                if not transition_xml_id:
                    raise UserError(_(f"No external ID found for transition '{transition.name}'"))
                if "." not in value and "." in transition_xml_id:
                    module_name = transition_xml_id.split(".")[0]
                    full_xml_id = f"{module_name}.{value}"

                try:
                    referenced_record = self.env.ref(full_xml_id)
                    referenced_id = referenced_record.id
                except ValueError as e:
                    raise UserError(
                        _(
                            f"Invalid XML ID '{full_xml_id}' for field '{key}' in transition '{transition.name}': {str(e)}"
                        )
                    ) from e

                new_key = key[:-4]  # strip the trailing '_ref' from the key name
                updated_context[new_key] = referenced_id
            else:
                updated_context[key] = value

        return updated_context
