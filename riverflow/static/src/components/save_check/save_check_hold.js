import { registry } from "@web/core/registry";

/**
 * The answer of a guarded method that held on its save check.
 *
 * A function client action that returns nothing: the action service then
 * neither closes the dialog nor opens anything, so the wizard stays open.
 * The open form listens for the bus event and reloads its record, which
 * brings in the findings box and the confirmation the server just stored
 * (see save_check_form_controller_patch.js).
 */
export function saveCheckHold(env, action) {
    env.bus.trigger("RIVERFLOW:SAVE_CHECK_HOLD", action.params || {});
}

registry.category("actions").add("riverflow_save_check_hold", saveCheckHold);
