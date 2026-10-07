import { expect, test } from "@odoo/hoot";
import { queryAllTexts } from "@odoo/hoot-dom";
import { defineModels, fields, models, mountView, onRpc } from "@web/../tests/web_test_helpers";

import { clearFormShortcutsCache } from "@oteny_shortcut/views/x2many_field_patch";

// The in-form row moves into the toolbar of buttons directly above the list,
// at its right end, when there is one (views/x2many_field_patch.js).

class Parent extends models.Model {
    _name = "parent";
    name = fields.Char();
    line_ids = fields.One2many({ relation: "line", relation_field: "parent_id" });
    _records = [{ id: 1, name: "Parent", line_ids: [1, 2] }];
    do_it() {}
}

class Line extends models.Model {
    _name = "line";
    name = fields.Char();
    done = fields.Boolean();
    parent_id = fields.Many2one({ relation: "parent" });
    _records = [
        { id: 1, name: "Open A", done: false, parent_id: 1 },
        { id: 2, name: "Done B", done: true, parent_id: 1 },
    ];
}

defineModels([Parent, Line]);

test("in a form the row moves into the toolbar above the list", async () => {
    clearFormShortcutsCache();
    onRpc("ir.filters", "get_shortcuts", () => [
        {
            id: 7,
            name: "Todo",
            domain: '[("done", "=", False)]',
            is_default: true,
            shortcut_sequence: 10,
            shortcut_view_type: false,
            shortcut_icon: false,
            shortcut_layout: false,
            shortcut_show_when: "",
        },
    ]);
    await mountView({
        type: "form",
        resModel: "parent",
        resId: 1,
        arch: `
            <form>
                <div class="d-flex align-items-center gap-2 mb-3">
                    <button name="do_it" type="object" string="Add" class="btn btn-primary"/>
                </div>
                <field name="line_ids">
                    <list><field name="name"/><field name="done"/></list>
                </field>
            </form>`,
    });
    // The row sits in the toolbar, at its right end, and still filters.
    expect("div.d-flex > .o_x2many_shortcuts").toHaveCount(1);
    expect("div.d-flex > .o_x2many_shortcuts").toHaveClass(["order-last", "ms-auto"]);
    expect(".o_field_x2many .o_x2many_shortcuts").toHaveCount(0);
    expect(".o_x2many_shortcuts button.btn-primary").toHaveText("Todo");
    expect(queryAllTexts(".o_data_row:not(.d-none) .o_data_cell[name='name']")).toEqual(["Open A"]);
});
