import { useBus } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { NavBar } from "@web/webclient/navbar/navbar";

const HOME_SCREEN_TAG = "oteny_home.home_screen";

patch(NavBar.prototype, {
    setup() {
        super.setup();
        // The top bar shows no app while the home screen is open, so it must
        // render again when the action changes, not only when the app changes.
        useBus(this.env.bus, "ACTION_MANAGER:UI-UPDATED", () => this.render());
    },

    get isHomeScreenOpen() {
        return this.actionService.currentController?.action.tag === HOME_SCREEN_TAG;
    },

    get currentApp() {
        if (this.isHomeScreenOpen) {
            return undefined;
        }
        return super.currentApp;
    },

    // The apps button of the top bar opens the home screen, not a dropdown of app names.
    openHomeScreen() {
        return this.actionService.doAction(HOME_SCREEN_TAG, { clearBreadcrumbs: true });
    },
});
