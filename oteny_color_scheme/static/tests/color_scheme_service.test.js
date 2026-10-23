import { expect, mockMatchMedia, test } from "@odoo/hoot";
import { animationFrame } from "@odoo/hoot-mock";
import { patchWithCleanup } from "@web/../tests/web_test_helpers";
import { browser } from "@web/core/browser/browser";
import { cookie } from "@web/core/browser/cookie";
import { registry } from "@web/core/registry";
import { _makeUser, user } from "@web/core/user";

import { colorSchemeService } from "@oteny_color_scheme/color_scheme_service";

// The color_scheme service (oteny_color_scheme): the server renders the page in
// the scheme of the color_scheme cookie, or light without one. The service
// reloads the page once when the user wants another scheme: the Theme
// preference, or the operating system's scheme for System.

function setup({ theme, system, renderedIn }) {
    mockMatchMedia({ "prefers-color-scheme": system });
    patchWithCleanup(user, _makeUser({ user_settings: { id: 1, color_scheme: theme } }));
    patchWithCleanup(browser.location, { reload: () => expect.step("reload") });
    if (renderedIn) {
        cookie.set("color_scheme", renderedIn);
    }
}

test("System on a light system keeps a page that was rendered light", async () => {
    setup({ theme: "system", system: "light" });
    await colorSchemeService.start();
    expect(cookie.get("color_scheme")).toBe(undefined);
    expect.verifySteps([]);
});

test("System on a dark system stores dark and reloads once", async () => {
    setup({ theme: "system", system: "dark" });
    colorSchemeService.start(); // never settles: the page reloads
    await animationFrame();
    expect(cookie.get("color_scheme")).toBe("dark");
    expect.verifySteps(["reload"]);
});

test("Dark on a page rendered dark does not reload, whatever the system", async () => {
    setup({ theme: "dark", system: "light", renderedIn: "dark" });
    await colorSchemeService.start();
    expect(cookie.get("color_scheme")).toBe("dark");
    expect.verifySteps([]);
});

test("Light chosen in another tab reloads a page that was rendered dark", async () => {
    setup({ theme: "light", system: "dark", renderedIn: "dark" });
    colorSchemeService.start();
    await animationFrame();
    expect(cookie.get("color_scheme")).toBe("light");
    expect.verifySteps(["reload"]);
});

test("A browser that refuses the cookie gets no reload", async () => {
    setup({ theme: "dark", system: "light" });
    patchWithCleanup(cookie, { set: () => {} });
    await colorSchemeService.start();
    expect.verifySteps([]);
});

test("the service has its own key, so web_enterprise's color_scheme service can load beside it", async () => {
    expect(registry.category("services").get("oteny_color_scheme")).toBe(colorSchemeService);
});
