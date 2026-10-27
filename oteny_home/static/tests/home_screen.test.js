import { describe, expect, test } from "@odoo/hoot";
import { queryAllTexts } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import {
    contains,
    defineActions,
    defineMenus,
    defineModels,
    fields,
    models,
    mountWebClient,
    patchWithCleanup,
    webModels,
} from "@web/../tests/web_test_helpers";
import { session } from "@web/session";

// The home screen (oteny_home): the web client opens it when the URL names no
// action, it shows every app with its icon, by name after the apps that the
// system parameter oteny_home.first_apps puts first and without the apps of
// oteny_home.hidden_apps, its search box filters the apps by name, and the apps
// button of the top bar brings it back.

describe.current.tags("desktop");

class Partner extends models.Model {
    _name = "partner";
    name = fields.Char();
    _records = [{ id: 1, name: "First record" }];
    _views = { list: `<list><field name="name"/></list>` };
}

const { ResCompany, ResPartner, ResUsers } = webModels;

defineModels([Partner, ResCompany, ResPartner, ResUsers]);

defineActions([
    { id: 1, name: "Partners", res_model: "partner", views: [[false, "list"]] },
    { id: 2, name: "Contacts", res_model: "partner", views: [[false, "list"]] },
    { id: 3, name: "Radar", res_model: "partner", views: [[false, "list"]] },
    { id: 4, name: "Calendar", res_model: "partner", views: [[false, "list"]] },
]);

defineMenus([
    // An app with an image icon and an app with a Font Awesome icon on a colour.
    { id: 1, name: "Partners", actionID: 1, xmlid: "test.partners",
      webIconData: "/web/static/img/default_icon_app.png" },
    { id: 2, name: "Contacts", actionID: 2, xmlid: "test.contacts", webIcon: "fa fa-address-book,#ffffff,#875A7B" },
    { id: 3, name: "Radar", actionID: 3, xmlid: "test.radar" },
    { id: 4, name: "Calendar", actionID: 4, xmlid: "test.calendar" },
]);

test("the web client starts on the home screen with every app and its icon", async () => {
    await mountWebClient();
    await animationFrame();

    expect(".o_oteny_home").toHaveCount(1);
    expect(queryAllTexts(".o_oteny_home_caption")).toEqual(["Calendar", "Contacts", "Partners", "Radar"]);
    // The test page replaces every image URL with a blank image, so only the
    // presence of the image is checked.
    expect(".o_oteny_home_app:contains(Partners) img.o_oteny_home_icon").toHaveCount(1);
    expect(".o_oteny_home_app:contains(Contacts) .o_oteny_home_icon i").toHaveClass("fa-address-book");
    expect(".o_oteny_home_app:contains(Contacts) .o_oteny_home_icon").toHaveStyle({
        backgroundColor: "rgb(135, 90, 123)",
    });
});

test("the apps of first_apps come first, the apps of hidden_apps are not shown", async () => {
    patchWithCleanup(session, {
        oteny_home: { first_apps: ["test.radar", "test.unknown"], hidden_apps: ["test.calendar"] },
    });
    await mountWebClient();
    await animationFrame();

    expect(queryAllTexts(".o_oteny_home_caption")).toEqual(["Radar", "Contacts", "Partners"]);
});

test("the search box filters the apps by name and Enter opens the first match", async () => {
    await mountWebClient();
    await animationFrame();

    await contains(".o_oteny_home_search input").edit("cont", { confirm: false });
    expect(queryAllTexts(".o_oteny_home_caption")).toEqual(["Contacts"]);

    await contains(".o_oteny_home_search input").edit("nothing like it", { confirm: false });
    expect(".o_oteny_home_app").toHaveCount(0);
    expect(".o_oteny_home_no_result").toHaveCount(1);

    await contains(".o_oteny_home_search input").edit("part", { confirm: false });
    await contains(".o_oteny_home_search input").press("Enter");
    await animationFrame();
    expect(".o_oteny_home").toHaveCount(0);
    expect(".o_list_view").toHaveCount(1);
    await animationFrame();
    expect(".o_menu_brand").toHaveText("Partners");
});

test("a click on an app opens it and the apps button brings the home screen back", async () => {
    await mountWebClient();
    await animationFrame();

    await contains(".o_oteny_home_app:contains(Contacts)").click();
    await animationFrame();
    expect(".o_oteny_home").toHaveCount(0);
    await animationFrame();
    expect(".o_menu_brand").toHaveText("Contacts");

    await contains(".o_navbar_apps_menu .o_menu_toggle").click();
    await animationFrame();
    expect(".o_oteny_home").toHaveCount(1);
    // The top bar no longer shows the app that was open before.
    await animationFrame();
    expect(".o_menu_brand").toHaveCount(0);
});

test("a click on the name of the open app brings the home screen back", async () => {
    await mountWebClient();
    await animationFrame();

    await contains(".o_oteny_home_app:contains(Contacts)").click();
    await animationFrame();
    await animationFrame();
    // The name is in the link of the apps button, so a hover on the name turns
    // the grid into a back arrow, as in Odoo Enterprise.
    expect(".o_menu_toggle").toHaveAttribute("href", "/odoo/home");
    expect(".o_menu_toggle .o_menu_toggle_icon").toHaveCount(1);
    expect(".o_menu_toggle .o_menu_brand").toHaveText("Contacts");

    await contains(".o_menu_brand").click();
    await animationFrame();
    expect(".o_oteny_home").toHaveCount(1);
});

test("the home screen hides the apps button, an open app shows it", async () => {
    await mountWebClient();
    await animationFrame();
    expect(".o_oteny_home").toHaveCount(1);
    expect(".o_menu_toggle").toHaveCount(0);

    await contains(".o_oteny_home_app:contains(Contacts)").click();
    await animationFrame();
    await animationFrame();
    expect(".o_menu_toggle").toHaveCount(1);

    await contains(".o_menu_toggle").click();
    await animationFrame();
    await animationFrame();
    expect(".o_oteny_home").toHaveCount(1);
    expect(".o_menu_toggle").toHaveCount(0);
});
