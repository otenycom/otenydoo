import { describe, expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import { defineModels, fields, mockService, models, mountView, onRpc } from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

// The Audit tab of a form (widget oteny_audit_log): it reads the record's audit log only when
// the tab is opened, shows the captions, and "Full Screen (N)" opens the whole list.

class Ship extends models.Model {
    _name = "audit.tab.ship";
    name = fields.Char();
    write_date = fields.Datetime();
    _records = [{ id: 7, name: "Carpe Solim", write_date: "2026-11-18 10:00:00" }];
}

defineMailModels();
defineModels([Ship]);

// A form with a notebook: the phone layout draws it otherwise.
describe.current.tags("desktop");

const arch = `
    <form><sheet><notebook>
        <page name="details" string="Details"><field name="name"/></page>
        <page name="oteny_audit_log" string="Audit"><widget name="oteny_audit_log"/></page>
    </notebook></sheet></form>`;

test("the audit tab reads the log when opened, groups it per transaction, and opens the full list", async () => {
    // oteny_shortcut's Form Wide Toggle asks this on every form open.
    onRpc("ir.config_parameter", "get_param", () => "False");
    onRpc("oteny.audit.log.aggregated", "web_search_read", ({ kwargs }) => {
        expect.step(JSON.stringify(kwargs.domain));
        expect(kwargs.context.oteny_audit_caption_without_user).toBe(true);
        const fred = { id: 5, display_name: "Fred" };
        const kirsten = { id: 6, display_name: "Kirsten" };
        return {
            length: 4,
            records: [
                { id: 8, transaction_id: 900, create_date: "2026-11-18 10:15:00", create_uid: fred,
                  caption: "<div>Update Ship <b>Carpe Solim</b></div>" },
                { id: 6, transaction_id: 900, create_date: "2026-11-18 10:15:00", create_uid: fred,
                  caption: "<div class='ms-4'>Updated Name</div>" },
                { id: 4, transaction_id: 800, create_date: "2026-11-17 09:00:00", create_uid: kirsten,
                  caption: "<div>Insert Ship <b>Carpe Solim</b></div>" },
            ],
        };
    });
    mockService("action", {
        doAction(action) {
            expect.step(`${action.res_model} ${JSON.stringify(action.domain)}`);
        },
    });
    await mountView({ type: "form", resModel: "audit.tab.ship", resId: 7, arch });
    expect.verifySteps([]);

    await click(".o_notebook_headers a:contains(Audit)");
    await animationFrame();
    const domain = JSON.stringify([["model_name", "=", "audit.tab.ship"], ["record_id", "in", [7]]]);
    expect.verifySteps([domain]);
    expect(".o_oteny_audit_tx").toHaveCount(2);
    expect(".o_oteny_audit_tx:eq(0) .o_oteny_audit_tx_header").toHaveText(/^Fred\s*18-Nov-26 \d\d:15:00$/);
    expect(".o_oteny_audit_tx:eq(0) .o_oteny_audit_row").toHaveCount(2);
    expect(".o_oteny_audit_tx:eq(1) .o_oteny_audit_tx_header").toHaveText(/^Kirsten\s*17-Nov-26/);
    expect(".o_oteny_audit_tx:eq(0)").not.toHaveClass("o_oteny_audit_tx_alt");
    expect(".o_oteny_audit_tx:eq(1)").toHaveClass("o_oteny_audit_tx_alt");
    expect(".o_oteny_audit_more").toHaveText("3 of 4 rows; Full Screen shows all.");
    expect(".o_oteny_audit_full_screen").toHaveClass("btn-link");

    await click(".o_oteny_audit_full_screen");
    expect.verifySteps([`oteny.audit.log.aggregated ${domain}`]);
});
