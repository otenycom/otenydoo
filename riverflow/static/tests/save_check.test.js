import { expect, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import {
    contains,
    defineModels,
    fields,
    models,
    MockServer,
    mountView,
    onRpc,
} from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

/**
 * The save check (riverflow.save.check.mixin), the one browser test.
 *
 * The Python tests prove the server: the gate, the findings, the
 * confirmation and the bot path. What a server test cannot see is the
 * browser's half: a held OK answers the client action
 * riverflow_save_check_hold; the dialog must stay open, the form must reload
 * the record, and the findings box and the confirmation must show.
 */

class TransitionWizard extends models.Model {
    _name = "transition.wizard";

    name = fields.Char();
    save_check_state = fields.Selection({
        selection: [
            ["warning", "Warnings"],
            ["error", "Errors"],
        ],
    });
    save_check_findings = fields.Json();
    save_check_confirmed = fields.Boolean({ string: "I confirm — save with these warnings" });

    _records = [{ id: 1, name: "Mark done" }];

    _views = {
        form: `
            <form>
                <header>
                    <!-- The OK button. A form outside a dialog does not render a footer. -->
                    <button string="OK" type="object" name="action_save" class="btn-primary"/>
                </header>
                <sheet>
                    <group>
                        <field name="save_check_state" invisible="1"/>
                        <field name="save_check_findings" widget="save_check" nolabel="1" colspan="2"
                               invisible="not save_check_findings"/>
                        <field name="save_check_confirmed" invisible="save_check_state != 'warning'"/>
                        <field name="name"/>
                    </group>
                </sheet>
            </form>
        `,
    };
}

// The test bundle carries mail's patches of the form view, which read the
// mail models (res.users, res.partner, ...).
defineMailModels();
defineModels([TransitionWizard]);

test("a held OK keeps the form open and shows the warnings with the confirmation", async () => {
    const findings = [
        {
            level: "warning",
            message: "The dates differ from the application.",
            subject: "A1 261009.pdf",
        },
    ];
    onRpc("action_save", () => {
        // The server stores the findings and answers the hold.
        MockServer.env["transition.wizard"].write([1], {
            save_check_state: "warning",
            save_check_findings: findings,
            save_check_confirmed: false,
        });
        return {
            type: "ir.actions.client",
            tag: "riverflow_save_check_hold",
            params: {
                res_model: "transition.wizard",
                res_id: 1,
                state: "warning",
                findings,
            },
        };
    });

    onRpc("transition.wizard", "web_read", () => {
        expect.step("web_read");
    });
    await mountView({ type: "form", resModel: "transition.wizard", resId: 1 });
    expect.verifySteps(["web_read"]);
    expect(".o_form_view").toHaveCount(1);
    expect(".o_riverflow_save_check").toHaveCount(0);
    expect(".o_field_widget[name=save_check_confirmed]").toHaveCount(0);

    await contains("button[name=action_save]").click();
    await animationFrame();

    // The hold reloaded the record, and the form is still there: nothing
    // closed it and nothing replaced it.
    expect.verifySteps(["web_read"]);
    expect(".o_form_view").toHaveCount(1);
    expect(".alert-warning .o_riverflow_save_check_warning").toHaveText(
        "A1 261009.pdf: The dates differ from the application."
    );
    expect(".alert-danger").toHaveCount(0);
    expect(".o_field_widget[name=save_check_confirmed] input").toHaveCount(1);
});

test("two or more findings of one level show as a numbered list", async () => {
    const findings = [
        { level: "warning", message: "Abao still has 18.0 vacation hours on 09-Oct-26." },
        { level: "warning", message: "Abao: Waits for the final reconciliation salary of November 2026." },
        { level: "error", message: "The contract end date is missing." },
    ];
    onRpc("action_save", () => {
        MockServer.env["transition.wizard"].write([1], {
            save_check_state: "error",
            save_check_findings: findings,
            save_check_confirmed: false,
        });
        return {
            type: "ir.actions.client",
            tag: "riverflow_save_check_hold",
            params: { res_model: "transition.wizard", res_id: 1, state: "error", findings },
        };
    });
    await mountView({ type: "form", resModel: "transition.wizard", resId: 1 });
    await contains("button[name=action_save]").click();
    await animationFrame();
    // Two warnings: a numbered list. One error: a plain line, no list.
    expect(".alert-warning ol li.o_riverflow_save_check_warning").toHaveCount(2);
    expect(".alert-warning ol li:nth-child(2)").toHaveText(
        "Abao: Waits for the final reconciliation salary of November 2026."
    );
    expect(".alert-danger ol").toHaveCount(0);
    expect(".alert-danger div.o_riverflow_save_check_error").toHaveText("The contract end date is missing.");
});
