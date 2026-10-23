import { describe, expect, test } from "@odoo/hoot";
import { press, queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame, runAllTimers } from "@odoo/hoot-mock";
import {
    contains,
    defineModels,
    fields,
    models,
    mountView,
    onRpc,
} from "@web/../tests/web_test_helpers";

// The article form (oteny_article): the left column shows the tree of
// contents with the branch of the open article opened, a click loads another
// article in place (after a save of the open one), and the search box shows
// the title matches, then the body matches, each with its place in the tree.

describe.current.tags("desktop");

class OtenyArticle extends models.Model {
    _name = "oteny.article";
    _order = "sequence, id";

    name = fields.Char();
    icon = fields.Char();
    body = fields.Html();
    sequence = fields.Integer();
    parent_id = fields.Many2one({ relation: "oteny.article" });

    _records = [
        { id: 1, name: "Manual", icon: "📘", sequence: 1, body: "<p>Start here</p>" },
        { id: 2, name: "Planning", sequence: 1, parent_id: 1, body: "<p>The crew planning</p>" },
        { id: 3, name: "Slots", sequence: 1, parent_id: 2, body: "<p>A slot is a place on a ship</p>" },
        { id: 4, name: "Skills", sequence: 2, body: "<p>Skills for agents</p>" },
        { id: 5, name: "Billing", sequence: 1, parent_id: 4, body: "<p>Invoices and planning costs</p>" },
    ];
}

defineModels([OtenyArticle]);

const arch = `
    <form js_class="oteny_article_form">
        <sheet>
            <field name="name" />
        </sheet>
    </form>`;

test("the tree opens the branch of the open article", async () => {
    await mountView({ type: "form", resModel: "oteny.article", resId: 2, arch });

    expect(queryAllTexts(".o_oteny_article_row .text-truncate")).toEqual(["Manual", "Planning", "Slots", "Skills"]);
    expect(".o_oteny_article_row[data-article-id='1'] .o_oteny_article_row_icon").toHaveText("📘");
    expect(".o_oteny_article_row.active").toHaveText("Planning");
    // The tree replaces the record pager.
    expect(".o_pager").toHaveCount(0);
});

test("a click in the tree loads the article in place and saves the open one first", async () => {
    onRpc("web_save", ({ args }) => expect.step(`save ${args[0]} ${args[1].name}`));
    await mountView({ type: "form", resModel: "oteny.article", resId: 2, arch });

    await contains(".o_field_widget[name=name] input").edit("Crew Planning");
    await contains(".o_oteny_article_row[data-article-id='4']").click();
    await animationFrame();

    expect.verifySteps(["save 2 Crew Planning"]);
    expect(".o_field_widget[name=name] input").toHaveValue("Skills");
    expect(".o_oteny_article_row.active").toHaveText("Skills");
    // The tree reloaded after the save: the new title shows.
    expect(".o_oteny_article_row[data-article-id='2']").toHaveText("Crew Planning");
});

test("the search shows title matches first, then body matches, with their place", async () => {
    await mountView({ type: "form", resModel: "oteny.article", resId: 1, arch });

    await contains(".o_oteny_article_search input").edit("planning", { confirm: false });
    await runAllTimers();
    await animationFrame();

    expect(queryAllTexts(".o_oteny_article_result .o_oteny_article_row_title")).toEqual(["Planning", "Billing"]);
    expect(queryAllTexts(".o_oteny_article_result_path")).toEqual(["Manual", "Skills"]);

    await press("Enter");
    await animationFrame();
    expect(".o_field_widget[name=name] input").toHaveValue("Planning");
    expect(".o_oteny_article_result").toHaveCount(0);
    expect(".o_oteny_article_row.active").toHaveText("Planning");
});
