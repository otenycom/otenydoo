import { describe, expect, test } from "@odoo/hoot";
import { queryRect } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import {
    contains,
    defineModels,
    fields,
    getService,
    models,
    mountWebClient,
} from "@web/../tests/web_test_helpers";

// The article form on a phone (oteny_article): the left column is a drawer
// over the page. The button in the control panel opens it; a tap on the
// backdrop or on an article closes it.

describe.current.tags("mobile");

class OtenyArticle extends models.Model {
    _name = "oteny.article";
    _order = "sequence, id";

    name = fields.Char();
    icon = fields.Char();
    body = fields.Html();
    sequence = fields.Integer();
    parent_id = fields.Many2one({ relation: "oteny.article" });

    _records = [
        { id: 1, name: "Manual", sequence: 1, body: "<p>Start here</p>" },
        { id: 2, name: "Planning", sequence: 1, parent_id: 1, body: "<p>The crew planning</p>" },
    ];
    _views = {
        form: `
            <form js_class="oteny_article_form">
                <sheet>
                    <field name="name" />
                </sheet>
            </form>`,
    };
}

defineModels([OtenyArticle]);

async function openArticle(resId) {
    await mountWebClient();
    await getService("action").doAction({
        type: "ir.actions.act_window",
        res_model: "oteny.article",
        res_id: resId,
        views: [[false, "form"]],
    });
    await animationFrame();
}

/** The drawer is closed when it stands left of the page, out of view. */
function expectDrawerClosed() {
    expect(".o_oteny_article_backdrop").toHaveCount(0);
    expect(queryRect(".o_oteny_article_sidebar").right).toBe(queryRect(".o_form_view").left);
}

function expectDrawerOpen() {
    expect(".o_oteny_article_backdrop").toHaveCount(1);
    expect(queryRect(".o_oteny_article_sidebar").left).toBe(queryRect(".o_form_view").left);
}

test("the button opens the drawer and a tap on the backdrop closes it", async () => {
    await openArticle(1);
    expectDrawerClosed();
    // The button stands beside the cog menu, so New stays the one visible
    // main button of the control panel.
    expect(".o_form_button_create").toBeVisible();

    await contains(".o_oteny_article_toggle").click();
    expectDrawerOpen();

    await contains(".o_oteny_article_backdrop").click();
    expectDrawerClosed();
});

test("an article opened from the drawer closes the drawer", async () => {
    await openArticle(1);
    await contains(".o_oteny_article_toggle").click();

    await contains(".o_oteny_article_row[data-article-id='2']").click();
    await animationFrame();

    expect(".o_field_widget[name=name] input").toHaveValue("Planning");
    expectDrawerClosed();
});
