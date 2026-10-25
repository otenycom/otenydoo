import { onMounted } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { CopyButton } from "@web/core/copy_button/copy_button";
import {
    ErrorDialog,
    NetworkErrorDialog,
    RequestEntityTooLargeErrorDialog,
} from "@web/core/errors/error_dialogs";
import { rpc } from "@web/core/network/rpc";
import { user } from "@web/core/user";
import { patch } from "@web/core/utils/patch";

// The type of the Oops Log for the type that an RPC error names itself.
const ERROR_TYPES = { server: "server", script: "client", network: "network" };

patch(ErrorDialog.components, { CopyButton });

// Every error dialog ("Oops!") writes its fault to the Oops Log when it opens, and
// shows a link to the record. A business message (UserError, ValidationError, …)
// opens WarningDialog, which is no ErrorDialog, so it writes nothing.
patch(ErrorDialog.prototype, {
    setup() {
        super.setup();
        this.state.oopsLog = null;
        // After the whole setup chain: RPCErrorDialog builds this.traceback (the
        // server part first) in its own setup, after this one.
        onMounted(() => this.writeOopsLog());
    },

    async writeOopsLog() {
        if (!user.isInternalUser) {
            return;
        }
        try {
            const { id, url } = await rpc("/oteny_oops/log", {
                exception_name: this.props.data?.name || this.props.exceptionName || this.props.name || "",
                message: this.props.data?.message || this.props.message || "",
                traceback: this.traceback || this.props.traceback || "",
                error_type: this.oopsErrorType(),
                model: this.props.model || "",
                url: browser.location.href,
                context: user.context,
            });
            this.state.oopsLog = { id, url: new URL(url, browser.location.origin).href };
        } catch {
            // A failed call shows nothing. A second error dialog would send the
            // error again, and a fault in the route would loop.
        }
    },

    oopsErrorType() {
        if (this.props.type in ERROR_TYPES) {
            return ERROR_TYPES[this.props.type];
        }
        if (this instanceof NetworkErrorDialog || this instanceof RequestEntityTooLargeErrorDialog) {
            return "network";
        }
        return "client";
    },
});
