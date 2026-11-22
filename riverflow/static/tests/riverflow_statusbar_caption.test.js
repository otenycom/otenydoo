import { expect, test } from "@odoo/hoot";
import { queryRect } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
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
 * A form that shows the workflow of a linked record in its header names that
 * record above the states (riverflow_statusbar, option caption_field). The name
 * is a link that opens the record. The Many2one is invisible in the view, so
 * the widget asks for its name (fieldDependencies). The transition buttons stand
 * on one line with the states, below the name.
 */

class CaptionState extends models.Model {
    _name = "caption.state";

    name = fields.Char();

    _records = [
        { id: 1, name: "Not Started" },
        { id: 2, name: "To Review" },
    ];
}

class CaptionService extends models.Model {
    _name = "caption.service";

    name = fields.Char();

    _records = [{ id: 5, name: "Collect Rijnvaartverklaring" }];
}

class CaptionCredential extends models.Model {
    _name = "caption.credential";

    name = fields.Char();
    service_id = fields.Many2one({ relation: "caption.service" });
    service_state_id = fields.Many2one({ relation: "caption.state" });
    service_statusbar_json = fields.Json();
    service_buttons_json = fields.Json();

    _records = [
        {
            id: 1,
            name: "Alexander - Rijnvaartverklaring",
            service_id: 5,
            service_state_id: 2,
            service_statusbar_json: {
                states: [
                    { value: 1, label: "Not Started", isFolded: false, isSelected: false },
                    { value: 2, label: "To Review", isFolded: false, isSelected: true },
                ],
            },
            service_buttons_json: {
                layout: "form_header",
                res_model: "caption.service",
                res_id: 5,
                buttons: [
                    {
                        index: 0,
                        caption: "Accept Rijnvaartverklaring",
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
defineModels([CaptionState, CaptionService, CaptionCredential]);

const ARCH = `
    <form>
        <header>
            <field name="service_buttons_json" widget="transition_buttons" layout="form_header"/>
            <field name="service_id" invisible="1"/>
            <field name="service_state_id" widget="riverflow_statusbar" readonly="1" class="o_field_statusbar"
                options="{'statusbar_json_field': 'service_statusbar_json', 'caption_field': 'service_id'}"/>
            <field name="service_statusbar_json" invisible="1"/>
        </header>
        <sheet><field name="name"/></sheet>
    </form>`;

const CAPTION = ".o_field_riverflow_statusbar > .o_riverflow_statusbar_caption";

test.tags("desktop");
test("the name of the linked record stands above the states and opens it", async () => {
    const openAction = { type: "ir.actions.act_window", res_model: "caption.service", res_id: 5 };
    onRpc("caption.service", "get_formview_action", ({ args }) => {
        expect.step(`get_formview_action ${args[0]}`);
        return openAction;
    });
    mockService("action", {
        async doAction(action) {
            expect(action).toEqual(openAction);
            expect.step("doAction");
        },
    });
    await mountView({ type: "form", resModel: "caption.credential", resId: 1, arch: ARCH });
    await animationFrame();

    expect(CAPTION).toHaveText("Collect Rijnvaartverklaring");
    expect(queryRect(CAPTION).bottom).toBeLessThan(queryRect(".o_statusbar_status").top + 1);
    // the buttons and the states stand on one line, below the name
    expect(queryRect(".riverflow_header_buttons").bottom).toBe(
        queryRect(".o_statusbar_status").bottom
    );

    await contains(CAPTION).click();
    expect.verifySteps(["get_formview_action 5", "doAction"]);
});

test.tags("desktop");
test("no caption without the caption_field option", async () => {
    await mountView({
        type: "form",
        resModel: "caption.credential",
        resId: 1,
        arch: ARCH.replace(", 'caption_field': 'service_id'", ""),
    });
    expect(CAPTION).toHaveCount(0);
});
