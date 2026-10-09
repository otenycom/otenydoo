import { expect, test } from "@odoo/hoot";
import { queryAllTexts } from "@odoo/hoot-dom";
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
 * A kanban drop is a step (kanban_renderer_patch.js; radar pipeline applicants
 * plan, decision 127).
 *
 * The Python test test_kanban_drop proves which step the server picks. What it
 * cannot see is the browser's half: on a board grouped by the workflow state the
 * drop must call action_kanban_drop and open what it answers, must not save the
 * state, and must reload the board when the step screen closes; a board grouped
 * by another field keeps Odoo's own drop.
 */

class RiverflowState extends models.Model {
    _name = "riverflow.state";

    name = fields.Char();

    _records = [
        { id: 1, name: "Applicant" },
        { id: 2, name: "Offboarding" },
    ];
}

class Team extends models.Model {
    _name = "drop.team";

    name = fields.Char();

    _records = [
        { id: 1, name: "HR" },
        { id: 2, name: "Sales" },
    ];
}

class Crewman extends models.Model {
    _name = "drop.crewman";

    name = fields.Char();
    state_id = fields.Many2one({ relation: "riverflow.state" });
    team_id = fields.Many2one({ relation: "drop.team" });

    // The mock server shows only groups with records, so every column keeps a
    // crewman after Gacotano moves.
    _records = [
        { id: 1, name: "Gacotano", state_id: 1, team_id: 1 },
        { id: 2, name: "Acabo", state_id: 2, team_id: 2 },
        { id: 3, name: "Buan", state_id: 1, team_id: 1 },
    ];
}

// The step screen the server opens for the drop.
class StepWizard extends models.Model {
    _name = "drop.step.wizard";

    name = fields.Char();

    _views = {
        form: `
            <form>
                <field name="name"/>
                <footer>
                    <button string="OK" name="action_save" type="object" class="btn-primary"/>
                </footer>
            </form>`,
    };
}

// The test bundle carries mail's patches of the views, which read the mail
// models (res.users, res.partner, ...).
defineMailModels();
defineModels([RiverflowState, Team, Crewman, StepWizard]);

const arch = `
    <kanban>
        <templates>
            <t t-name="card"><field name="name"/></t>
        </templates>
    </kanban>`;

test.tags("desktop");
test("a drop on a board grouped by state opens the step and does not save the state", async () => {
    onRpc("action_kanban_drop", ({ args }) => {
        expect.step(["action_kanban_drop", args]);
        return {
            type: "ir.actions.client",
            tag: "display_notification",
            params: { message: "No step leads from Applicant to Offboarding.", type: "warning" },
        };
    });
    onRpc("web_save", () => expect.step("web_save"));

    await mountView({ type: "kanban", resModel: "drop.crewman", arch, groupBy: ["state_id"] });
    await contains(".o_kanban_group:eq(0) .o_kanban_record").dragAndDrop(".o_kanban_group:eq(1)");
    await animationFrame();

    // The server gets the card, the column it left and the column it landed in.
    expect.verifySteps([["action_kanban_drop", [[1], 1, 2]]]);
    expect(".o_notification").toHaveText("No step leads from Applicant to Offboarding.");
    // Nothing was saved, so the card is still in its column.
    expect(queryAllTexts(".o_kanban_group:eq(0) .o_kanban_record")).toEqual(["Gacotano", "Buan"]);
    expect(queryAllTexts(".o_kanban_group:eq(1) .o_kanban_record")).toEqual(["Acabo"]);
});

test.tags("desktop");
test("OK on the step screen runs the step, and the board reloads with the card moved", async () => {
    onRpc("action_kanban_drop", () => ({
        type: "ir.actions.act_window",
        res_model: "drop.step.wizard",
        views: [[false, "form"]],
        target: "new",
    }));
    onRpc("drop.step.wizard", "action_save", () => {
        // The step writes the new state on the server and closes its screen.
        MockServer.env["drop.crewman"].write([1], { state_id: 2 });
        return false;
    });

    await mountView({ type: "kanban", resModel: "drop.crewman", arch, groupBy: ["state_id"] });
    await contains(".o_kanban_group:eq(0) .o_kanban_record").dragAndDrop(".o_kanban_group:eq(1)");
    await animationFrame();

    // The screen is open and the card has not moved yet.
    expect(".modal .o_form_view").toHaveCount(1);
    expect(queryAllTexts(".o_kanban_group:eq(0) .o_kanban_record")).toEqual(["Gacotano", "Buan"]);

    await contains(".modal button[name=action_save]").click();
    await animationFrame();

    expect(".modal").toHaveCount(0);
    expect(queryAllTexts(".o_kanban_group:eq(0) .o_kanban_record")).toEqual(["Buan"]);
    expect(queryAllTexts(".o_kanban_group:eq(1) .o_kanban_record")).toEqual(["Gacotano", "Acabo"]);
});

test.tags("desktop");
test("a board grouped by another field keeps Odoo's drop", async () => {
    onRpc("action_kanban_drop", () => expect.step("action_kanban_drop"));
    onRpc("web_save", () => expect.step("web_save"));

    await mountView({ type: "kanban", resModel: "drop.crewman", arch, groupBy: ["team_id"] });
    await contains(".o_kanban_group:eq(0) .o_kanban_record").dragAndDrop(".o_kanban_group:eq(1)");
    await animationFrame();

    expect.verifySteps(["web_save"]);
    expect(queryAllTexts(".o_kanban_group:eq(1) .o_kanban_record")).toEqual(["Acabo", "Gacotano"]);
});
