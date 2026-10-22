import { browser } from "@web/core/browser/browser";
import { cookie } from "@web/core/browser/cookie";
import { registry } from "@web/core/registry";
import { user } from "@web/core/user";

/** The colour scheme of the operating system: "light" or "dark". */
export function systemColorScheme() {
    return browser.matchMedia("(prefers-color-scheme: dark)").matches ? "dark" : "light";
}

/** The scheme the user wants: the Theme preference, or the system's for System. */
export function wantedColorScheme() {
    const scheme = user.settings.color_scheme;
    return ["light", "dark"].includes(scheme) ? scheme : systemColorScheme();
}

/**
 * The server renders the page in the scheme of the ``color_scheme`` cookie, or
 * in the user's Theme when it is Light or Dark, and sets the cookie to the
 * scheme it rendered. When the user wants another scheme (System on
 * a dark operating system, or a Theme changed in another tab), the service sets
 * the cookie and reloads the page once. It holds the web client back meanwhile,
 * so the page does not flash in the wrong scheme.
 */
export const colorSchemeService = {
    async start() {
        const wanted = wantedColorScheme();
        // Without the cookie the server rendered the page light.
        if (wanted !== (cookie.get("color_scheme") || "light")) {
            cookie.set("color_scheme", wanted);
            // A browser that refuses the cookie would reload without end.
            if (cookie.get("color_scheme") === wanted) {
                this.reload();
                await new Promise(() => {});
            }
        }
        return {};
    },
    reload() {
        browser.location.reload();
    },
};

registry.category("services").add("color_scheme", colorSchemeService);
