import { FormController } from "@web/views/form/form_controller";
import { useBus } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";

/**
 * Reload the form a save check held (riverflow.save.check.mixin).
 *
 * The held OK committed the findings on the server, but the form still shows
 * the record as it was before the click. Only the form of the held record
 * reloads: the dialog's form, not the form behind it.
 */
patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);
        useBus(this.env.bus, "RIVERFLOW:SAVE_CHECK_HOLD", async (ev) => {
            const { res_model, res_id } = ev.detail || {};
            const root = this.model.root;
            if (root && root.resModel === res_model && root.resId === res_id) {
                await root.load();
            }
        });
    },
});
