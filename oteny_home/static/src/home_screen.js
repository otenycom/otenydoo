import { Component, onMounted, useRef, useState } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useBus, useService } from "@web/core/utils/hooks";
import { standardActionServiceProps } from "@web/webclient/actions/action_service";
import { computeAppsAndMenuItems } from "@web/webclient/menus/menu_helpers";

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
        const { apps } = computeAppsAndMenuItems(this.menuService.getMenuAsTree("root"));
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
