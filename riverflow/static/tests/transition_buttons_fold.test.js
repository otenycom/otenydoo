import { expect, test } from "@odoo/hoot";
import { queryOne, resize } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import {
    contains,
    defineModels,
    fields,
    mockService,
    models,
    mountView,
} from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

/**
 * In a form header, the transition buttons fold into one dropdown button when
 * they do not fit on the line next to the statusbar, also a single button, and
 * come back when the form is wide enough (transition_buttons.js,
 * adjustHeaderButtons). The statusbar counts at its narrowest, its single
 * dropdown.
 */

const LONG = "A transition caption that is long enough to need its own line";

class Crewman extends models.Model {
    _name = "fold.crewman";

    name = fields.Char();
    state = fields.Selection({
        selection: [
            ["applicant", "Applicant"],
            ["signed", "Agreement Signed"],
            ["employed", "Employed"],
        ],
    });
    transition_buttons_json = fields.Json();

    _records = [
        {
            id: 1,
            name: "Gacotano",
            state: "signed",
            transition_buttons_json: {
                layout: "form_header",
                buttons: [
                    {
                        index: 0,
                        caption: "Start Employment",
                        action: "action_button_click",
                        primary: true,
                        context: { transition_id: 7 },
                    },
                    {
                        index: 1,
                        caption: "Cancel Agreement",
                        action: "action_button_click",
                        context: { transition_id: 8 },
                    },
                ],
            },
        },
        {
            id: 2,
            name: "Aguilar",
            state: "signed",
            transition_buttons_json: {
                layout: "form_header",
                buttons: [
                    {
                        index: 0,
                        caption: LONG,
                        action: "action_button_click",
                        context: { transition_id: 9 },
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
        <header>
            <field name="transition_buttons_json" widget="transition_buttons" nolabel="1"/>
            <field name="state" widget="statusbar"/>
        </header>
        <sheet><field name="name"/></sheet>
    </form>`;

const BUTTONS = ".o_field_transition_buttons .riverflow_header_buttons";
const DROPDOWN = ".o_field_transition_buttons .riverflow_header_buttons + span";

async function setWidth(width) {
    await resize({ width });
    await animationFrame();
    await animationFrame();
}

function isFolded() {
    const folded = queryOne(BUTTONS).classList.contains("d-none");
    expect(queryOne(DROPDOWN).classList.contains("d-none")).toBe(!folded);
    return folded;
}

test.tags("desktop");
test("the buttons fold when narrow and come back when wide", async () => {
    await resize({ width: 1200 });
    await mountView({ type: "form", resModel: "fold.crewman", resId: 1, arch: ARCH });
    await animationFrame();
    expect(isFolded()).toBe(false);

    await setWidth(250);
    expect(isFolded()).toBe(true);

    await setWidth(1200);
    expect(isFolded()).toBe(false);
});

test.tags("desktop");
test("the dropdown runs the transition that is chosen", async () => {
    const contexts = [];
    mockService("action", {
        async doActionButton(params) {
            contexts.push(params.context);
        },
    });
    await resize({ width: 250 });
    await mountView({ type: "form", resModel: "fold.crewman", resId: 1, arch: ARCH });
    await animationFrame();
    expect(isFolded()).toBe(true);

    await contains(`${DROPDOWN} button.dropdown-toggle`).click();
    expect(".o-dropdown--menu .dropdown-item").toHaveCount(2);
    expect(".o-dropdown--menu .dropdown-item.fw-bold").toHaveText("Start Employment");
    await contains(".o-dropdown--menu .dropdown-item:contains(Cancel Agreement)").click();
    expect(contexts).toEqual([{ transition_id: 8 }]);
});

test.tags("desktop");
test("a single button that does not fit folds too", async () => {
    await resize({ width: 500 });
    await mountView({ type: "form", resModel: "fold.crewman", resId: 2, arch: ARCH });
    await animationFrame();
    expect(isFolded()).toBe(true);
});
