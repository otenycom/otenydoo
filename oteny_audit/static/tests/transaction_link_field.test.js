import { expect, test } from "@odoo/hoot";
import { click } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import {
    defineModels,
    fields,
    getFacetTexts,
    models,
    mountView,
    onRpc,
} from "@web/../tests/web_test_helpers";
import { defineMailModels } from "@mail/../tests/mail_test_helpers";

// The Transaction ID in the Audit Log lists is a link: a click adds the filter
// "Transaction ID = <value>" to the search, so the list shows every change of
// that database transaction, and the row does not open.

class AuditLog extends models.Model {
    _name = "audit.log";
    transaction_id = fields.Integer({ string: "Transaction ID" });
    _records = [
        { id: 1, transaction_id: 2419304059 },
        { id: 2, transaction_id: 2419304059 },
        { id: 3, transaction_id: 77 },
        { id: 4, transaction_id: 0 },
    ];
}

defineMailModels();
defineModels([AuditLog]);

const arch = `<list><field name="transaction_id" widget="audit_transaction_link"/></list>`;

test("a click on the transaction ID filters the list on that transaction", async () => {
    onRpc("audit.log", "web_search_read", ({ kwargs }) => {
        expect.step(JSON.stringify(kwargs.domain));
    });
    await mountView({ type: "list", resModel: "audit.log", arch });
    expect.verifySteps(["[]"]);

    await click(".o_data_row:eq(2) .o_field_audit_transaction_link a");
    await animationFrame();

    expect(getFacetTexts()).toEqual(["Transaction ID = 77"]);
    expect.verifySteps([JSON.stringify([["transaction_id", "=", 77]])]);
    expect(".o_data_row").toHaveCount(1);
    expect(".o_form_view").toHaveCount(0);
});

test("a row without a transaction ID shows no link", async () => {
    await mountView({ type: "list", resModel: "audit.log", arch });
    expect(".o_field_audit_transaction_link a").toHaveCount(3);
    expect(".o_data_row:eq(3) .o_field_audit_transaction_link a").toHaveCount(0);
});
