import { describe, expect, test } from "@odoo/hoot";
import { waitFor } from "@odoo/hoot-dom";
import { animationFrame } from "@odoo/hoot-mock";
import {
    makeServerError,
    mountWithCleanup,
    onRpc,
    patchWithCleanup,
} from "@web/../tests/web_test_helpers";
import { browser } from "@web/core/browser/browser";
import { MainComponentsContainer } from "@web/core/main_components_container";
import { user } from "@web/core/user";

// The Oops Log (oteny_oops): every error dialog sends its fault to /oteny_oops/log
// when it opens and shows a link to the record; a business message, a portal user
// and a failed call send or show nothing more.

describe.current.tags("desktop");

const RECORD_URL = "/odoo/action-oteny_oops.action_oteny_oops_log/7";

function serverFault() {
    return makeServerError({
        errorName: "builtins.AttributeError",
        message: "Odoo Server Error",
        description: "'bool' object has no attribute 'rstrip'",
    });
}

test("server error writes one record", async () => {
    expect.errors(1);
    onRpc("/oteny_oops/log", async (request) => {
        const { params } = await request.json();
        expect.step("log");
        expect(params.exception_name).toBe("builtins.AttributeError");
        expect(params.message).toBe("'bool' object has no attribute 'rstrip'");
        // The server traceback comes first, then the client part, as the dialog shows it.
        expect(params.traceback).toMatch(/^traceback\nThe above server error caused the following client error:/);
        expect(params.error_type).toBe("server");
        expect(params.url).toBe(browser.location.href);
        return { id: 7, url: RECORD_URL };
    });
    await mountWithCleanup(MainComponentsContainer);

    Promise.reject(serverFault());
    await waitFor(".o_oteny_oops_log");
    expect.verifyErrors(["Odoo Server Error"]);
    expect.verifySteps(["log"]);
    expect(".o_oteny_oops_log a").toHaveText("Oops Log 7");
    expect(".o_oteny_oops_log a").toHaveAttribute("href", `${browser.location.origin}${RECORD_URL}`);
    expect(".o_oteny_oops_log .o_clipboard_button").toHaveCount(1);
});

test("failed call opens no second dialog", async () => {
    expect.errors(1);
    onRpc("/oteny_oops/log", () => {
        expect.step("log");
        throw makeServerError({ errorName: "builtins.ValueError", message: "The log failed" });
    });
    await mountWithCleanup(MainComponentsContainer);

    Promise.reject(serverFault());
    await animationFrame();
    await animationFrame();
    expect.verifyErrors(["Odoo Server Error"]);
    expect.verifySteps(["log"]);
    expect(".modal").toHaveCount(1);
    expect(".o_oteny_oops_log").toHaveCount(0);
});

test("warning dialog sends nothing", async () => {
    expect.errors(1);
    onRpc("/oteny_oops/log", () => expect.step("log"));
    await mountWithCleanup(MainComponentsContainer);

    Promise.reject(makeServerError({ type: "UserError", message: "Not allowed" }));
    await animationFrame();
    await animationFrame();
    expect.verifyErrors(["Not allowed"]);
    expect(".modal").toHaveCount(1);
    expect.verifySteps([]);
});

test("portal user sends nothing", async () => {
    expect.errors(1);
    patchWithCleanup(user, { isInternalUser: false });
    onRpc("/oteny_oops/log", () => expect.step("log"));
    await mountWithCleanup(MainComponentsContainer);

    Promise.reject(serverFault());
    await animationFrame();
    await animationFrame();
    expect.verifyErrors(["Odoo Server Error"]);
    expect.verifySteps([]);
});
