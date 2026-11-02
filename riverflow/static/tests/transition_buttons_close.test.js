import { expect, test } from "@odoo/hoot";
import {
    contains,
    defineModels,
    fields,
    mockService,
    models,
    mountView,
    onRpc,
} from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

/**
 * The transition wizard's dialog reloads the record behind it when it closes,
 * but not when Odoo closes it with noReload (transition_buttons.js,
 * reloadAfterTransition).
 *
 * Odoo passes noReload when the wizard opened another record in the main
 * window (an action with target current). The form behind the dialog is then
 * already destroyed, and a load through its services never resolves; the
 * dialog service removes the dialog only after onClose resolves, so the dialog
 * stayed on the screen over the new record, now and then (found on
 * 2-Nov-2026).
 */

class Crewman extends models.Model {
    _name = "close.crewman";

    name = fields.Char();
    transition_buttons_json = fields.Json();

    _records = [
        {
            id: 1,
            name: "Gacotano",
            transition_buttons_json: {
                layout: "form_header",
                reload_on_close: true,
                buttons: [
                    {
                        index: 0,
                        caption: "Change Appointment",
                        action: "action_button_click",
                        primary: true,
                        context: { transition_id: 7 },
                    },
                ],
            },
        },
    ];
}

defineMailModels();
defineModels([Crewman]);

const ARCH = `
    <form>
        <header><field name="transition_buttons_json" widget="transition_buttons" nolabel="1"/></header>
        <sheet><field name="name"/></sheet>
    </form>`;

async function openWizardAndClose(closeParams) {
    let onClose;
    mockService("action", {
        async doActionButton(params) {
            onClose = params.onClose;
        },
    });
    let reads = 0;
    onRpc("web_read", () => {
        reads++;
    });
    await mountView({ type: "form", resModel: "close.crewman", resId: 1, arch: ARCH });
    reads = 0;
    await contains("button[name=action_button_click]").click();
    await onClose(closeParams);
    return reads;
}

test.tags("desktop");
test("the record behind the dialog reloads when the wizard closes", async () => {
    expect(await openWizardAndClose(undefined)).toBe(1);
});

test.tags("desktop");
test("no reload when Odoo closes the dialog with noReload", async () => {
    expect(await openWizardAndClose({ noReload: true })).toBe(0);
});
