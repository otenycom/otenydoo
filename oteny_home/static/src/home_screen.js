import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { session } from "@web/session";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { computeAppsAndMenuItems } from "@web/webclient/menus/menu_helpers";

/**
 * The apps in the order of the home screen: the apps of ``first_apps`` in that
 * order, then the other apps by name; the apps of ``hidden_apps`` are left out.
 * Both lists hold menu xmlids (the system parameters oteny_home.first_apps and
 * oteny_home.hidden_apps, sent in the session info).
 */
export function orderApps(apps, { first_apps = [], hidden_apps = [] } = {}) {
    const rank = (app) => {
        const index = first_apps.indexOf(app.xmlid);
        return index === -1 ? first_apps.length : index;
    };
    return apps
        .filter((app) => !hidden_apps.includes(app.xmlid))
        .sort((a, b) => rank(a) - rank(b) || a.label.localeCompare(b.label));
}

/**
 * The start screen: every app the user can open, as an icon with its name.
 * The search box filters the apps by name; Enter opens the first match.
 */
export class HomeScreen extends Component {
    static template = "oteny_home.HomeScreen";
    static props = { ...standardActionServiceProps };
    static path = "home";
    static displayName = _t("Home");

    setup() {
        this.menuService = useService("menu");
        this.state = useState({ search: "" });
        this.searchRef = useRef("search");
        useBus(this.env.bus, "MENUS:APP-CHANGED", () => this.render());
        onMounted(() => {
            if (!this.env.isSmall) {
                this.searchRef.el.focus();
            }
        });
    }

    get apps() {
        const { apps: allApps } = computeAppsAndMenuItems(this.menuService.getMenuAsTree("root"));
        const apps = orderApps(allApps, session.oteny_home);
        const search = this.state.search.trim().toLowerCase();
        if (!search) {
            return apps;
        }
        return apps.filter((app) => app.label.toLowerCase().includes(search));
    }

    openApp(app) {
        return this.menuService.selectMenu(app);
    }

    onSearchKeydown(ev) {
        if (ev.key === "Enter") {
            const [firstApp] = this.apps;
            if (firstApp) {
                this.openApp(firstApp);
            }
        }
    }
}

registry.category("actions").add("oteny_home.home_screen", HomeScreen);
