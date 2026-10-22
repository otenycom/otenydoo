import { patch } from "@web/core/utils/patch";
import { WebClient } from "@web/webclient/webclient";

// A URL without an action opens the home screen, not the first app.
patch(WebClient.prototype, {
    _loadDefaultApp() {
        return this.actionService.doAction("oteny_home.home_screen", {
            clearBreadcrumbs: true,
        });
    },
});
