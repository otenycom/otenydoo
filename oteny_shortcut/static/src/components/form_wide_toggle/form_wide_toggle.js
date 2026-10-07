/** @odoo-module **/

import {
    Component,
    useEffect,
    useExternalListener,
    useState,
    useSubEnv,
} from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { SIZES } from "@web/core/ui/ui_service";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";
import { createElement, setAttributes } from "@web/core/utils/xml";
import { session } from "@web/session";
import { FormCompiler } from "@web/views/form/form_compiler";
import { FormController } from "@web/views/form/form_controller";
import { FormRenderer } from "@web/views/form/form_renderer";

// Why this exists:
// The Odoo form sheet is capped at 1400px so wide tables (e.g. employee
// vacation tab) become unreadable on big screens. A global cap removal
// breaks the attachment-preview sidebar (which only activates at XXL).
// This module gives users a per-form toggle and lets specific forms
// declare a default-wide preference via the o_form_default_wide marker.

const STORAGE_PREFIX = "oteny_shortcut.form_wide.";
const XML_DEFAULT_WIDE_CLASS = "o_form_default_wide";

// The system parameter oteny_shortcut.form_wide_toggle reaches the client in
// the session info (oteny_form_wide_toggle, models/ir_http.py), read once per
// page load on the server. The form used to fetch it with an RPC cached for
// the session: a user without the Settings right was refused, so every form
// failed for that user, and a request bound to the first form never settled
// when that form was destroyed while it ran (2026-10-07).
function isToggleEnabled() {
    return session.oteny_form_wide_toggle !== false;
}

function readStoredPreference(resModel) {
    try {
        return localStorage.getItem(STORAGE_PREFIX + resModel);
    } catch {
        return null;
    }
}

function writeStoredPreference(resModel, value) {
    try {
        localStorage.setItem(STORAGE_PREFIX + resModel, value);
    } catch {
        // localStorage may be unavailable (private mode, quota exceeded).
        // Falling back to session-only state is still useful within the
        // page's lifetime, so we silently ignore the write failure.
    }
}

// ---------------------------------------------------------------------
// FormWideToggle component
// ---------------------------------------------------------------------
// Small icon button anchored inside .o_form_sheet (top-right corner).
// Reads state from env.formWide, populated by the FormController patch
// below via useSubEnv. Hidden when env.formWide.state.enabled is false
// (system param disabled) -- handled by the t-if in the compiled sheet.
//
// IMPORTANT: useState(this.env.formWide.state) is required for reactive
// re-rendering. OWL's useState only auto-subscribes the component that
// CALLS useState (the FormController). Reading the same proxy from a
// different component (this toggle) does not by itself trigger
// re-renders -- the toggle has to opt in by wrapping the shared state
// in its own useState call to register its subscription. Without this
// the icon would freeze on its initial state even though the form
// layout would correctly flip on each click.
export class FormWideToggle extends Component {
    static template = "oteny_shortcut.FormWideToggle";
    static props = {};

    setup() {
        this.formWideState = useState(this.env.formWide.state);
        this.toggle = this.env.formWide.toggle;
        this.titleNarrow = _t("Switch to narrow view");
        this.titleWide = _t("Switch to wide view");
    }
}

// ---------------------------------------------------------------------
// FormController patch -- per-form-controller wide state
// ---------------------------------------------------------------------
// Each FormController instance carries its own reactive state for the
// wide/narrow preference of the form it shows. The state is exposed to
// child components (including FormWideToggle inside the renderer) via
// useSubEnv. The className getter is overridden so the o_form_wide CSS
// class is added/removed on the .o_form_view wrapper as the user toggles.
patch(FormController.prototype, {
    setup() {
        super.setup(...arguments);

        const ui = useService("ui");
        const resModel = this.props.resModel;

        // The XML marker class signals the view's default preference.
        // It travels through computeViewClassName() into props.className
        // as part of a space-separated string.
        const xmlDefaultsWide = (this.props.className || "")
            .split(/\s+/)
            .includes(XML_DEFAULT_WIDE_CLASS);

        // localStorage preference, when set, always wins over the XML
        // default so a user choice persists across page loads per model.
        const stored = readStoredPreference(resModel);
        const initialIsWide = stored ? stored === "wide" : xmlDefaultsWide;

        const state = useState({
            // The system parameter, from the session info: when it is
            // off the toggle button stays hidden, but the wide/narrow
            // class is still applied so a default-wide form stays wide.
            enabled: isToggleEnabled(),
            isWide: initialIsWide,
            // Below the XXL breakpoint stock Odoo already renders the
            // chatter beneath the sheet (no SIDE_CHATTER), so the wide
            // toggle has nothing to do -- track the breakpoint so the
            // button can be hidden in that case. Updated by the resize
            // listener below.
            atXXL: ui.size >= SIZES.XXL,
        });
        useExternalListener(window, "resize", () => {
            state.atXXL = ui.size >= SIZES.XXL;
        });
        const formWide = {
            state,
            toggle: () => {
                state.isWide = !state.isWide;
                writeStoredPreference(
                    resModel,
                    state.isWide ? "wide" : "narrow"
                );
            },
        };
        this.formWide = formWide;
        useSubEnv({ formWide });

        // Several form-internal components (status bar overflow detection,
        // list view column widths, chatter attachment preview) measure
        // their container on window resize. Toggling wide/narrow changes
        // the available width without firing a resize, so they keep stale
        // measurements -- e.g. the status bar segments visibly overflow
        // after wide -> narrow until the user actually resizes the
        // window. useEffect runs after OWL has patched the DOM with the
        // new className, and the rAF inside it lets the browser apply
        // layout and paint before we synthesize the resize event.
        useEffect(
            () => {
                requestAnimationFrame(() => {
                    window.dispatchEvent(new Event("resize"));
                });
            },
            () => [state.isWide]
        );
    },

    get className() {
        const result = super.className;
        if (this.formWide?.state.isWide) {
            result.o_form_wide = true;
        }
        return result;
    },
});

// ---------------------------------------------------------------------
// FormRenderer patch -- expose the FormWideToggle class to compiled tpl
// and force chatter below the sheet when wide mode is on
// ---------------------------------------------------------------------
// The compiled form template references the toggle component via
// __comp__.otenyComponents.FormWideToggle, mirroring how mail's chatter
// uses __comp__.mailComponents.Chatter.
//
// The mailLayout override addresses why merely removing the 1400px
// max-width on .o_form_sheet_bg looked like it had no effect on common
// monitors: at XXL breakpoint the mail module returns "SIDE_CHATTER",
// putting the chatter beside the sheet (~530px wide). On a 1920px
// screen the sheet then gets ~1390px regardless of its own max-width,
// because the chatter aside is the binding constraint. Forcing the
// chatter back to "BOTTOM_CHATTER" when wide mode is on hands the full
// renderer-row width to the sheet -- the same effect the legacy
// riverflow_force_xl.js had, but only when the user opts in. Layouts
// other than SIDE_CHATTER (COMBO with attachment preview aside,
// EXTERNAL_COMBO, BOTTOM_CHATTER, NONE) already place chatter below or
// elsewhere, so they need no intervention.
patch(FormRenderer.prototype, {
    setup() {
        super.setup();
        this.otenyComponents = Object.assign({}, this.otenyComponents, {
            FormWideToggle,
        });
    },

    mailLayout(...args) {
        // Optional chaining guards the case where the mail module is not
        // installed and mailLayout was never patched onto the prototype.
        const result = super.mailLayout?.(...args);
        if (result === "SIDE_CHATTER" && this.env.formWide?.state.isWide) {
            return "BOTTOM_CHATTER";
        }
        return result;
    },
});

// ---------------------------------------------------------------------
// FormCompiler patch -- inject the toggle into every form sheet
// ---------------------------------------------------------------------
// compileSheet returns the .o_form_sheet_bg wrapper; the inner sheet
// element (.o_form_sheet) is its first child and already has
// position-relative, so an absolutely-positioned toggle anchors there.
// The t-if guards: hide in dialogs (toggling a wizard form makes no
// sense) and hide while the system param disables the feature.
patch(FormCompiler.prototype, {
    compileSheet(el, params) {
        const sheetBG = super.compileSheet(el, params);
        const sheetFG = sheetBG.querySelector(".o_form_sheet");
        if (sheetFG) {
            const toggleNode = createElement("t");
            setAttributes(toggleNode, {
                "t-component": "__comp__.otenyComponents.FormWideToggle",
                // Hidden when:
                //  - inside a dialog (target='new' wizards have no
                //    chatter / no horizontal-space concern)
                //  - the system parameter disabled the feature
                //  - the viewport is below XXL, where Odoo already
                //    stacks the chatter below the sheet anyway
                "t-if":
                    "!__comp__.env.inDialog and __comp__.env.formWide" +
                    " and __comp__.env.formWide.state.enabled" +
                    " and __comp__.env.formWide.state.atXXL",
            });
            sheetFG.insertBefore(toggleNode, sheetFG.firstChild);
        }
        return sheetBG;
    },
});
