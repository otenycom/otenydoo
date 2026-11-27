/** @odoo-module **/

import { useEffect, useSubEnv } from "@odoo/owl";
import { browser } from "@web/core/browser/browser";
import { patch } from "@web/core/utils/patch";
import { ListController } from "@web/views/list/list_controller";

// Why this exists:
// The shortcut banner (rendered by Layout on every list view) can store and
// restore a list layout: the optional columns and the column width ratios.
// The list controller owns that layout, so it exposes getViewLayout and
// applyViewLayout to the banner through env.shortcutLayout. This used to be
// the ShortcutListController behind js_class="shortcut_list"; since the
// banner shows everywhere, every list controller carries it.
patch(ListController.prototype, {
    setup() {
        super.setup();
        // Stored column width ratios to apply after each render, keyed by field name.
        // Kept as long as the corresponding shortcut is active; cleared on deactivation.
        this._storedColumnWidths = null;

        // After renders where column widths or loaded records changed, apply stored
        // column widths via requestAnimationFrame so they override the base
        // column-width hook's computed values before paint.
        useEffect(
            () => {
                if (this._storedColumnWidths) {
                    requestAnimationFrame(() => {
                        this._applyColumnWidthsToDOM();
                    });
                }
            },
            () => [this._storedColumnWidths, this.model.root.records]
        );

        useSubEnv({
            shortcutLayout: {
                getViewLayout: () => this.getViewLayout(),
                applyViewLayout: (layout) => this.applyViewLayout(layout),
            },
        });
    },

    /**
     * Build the same localStorage key the ListRenderer uses for optional fields,
     * so we can read/write column selection consistently.
     */
    _buildOptionalFieldsKey() {
        const fieldNames = [...this.model.root.fieldNames].sort();
        const parts = [this.model.root.resModel, "list", this.env.config.viewId];
        parts.push(...fieldNames);
        return `optional_fields,${parts.join(",")}`;
    },

    /**
     * Capture the current list view layout: active optional columns and
     * column width ratios read from the DOM.
     */
    getViewLayout() {
        const layout = {};
        const key = this._buildOptionalFieldsKey();
        const stored = browser.localStorage.getItem(key);
        if (stored !== null) {
            layout.optional_columns = stored.split(",").filter(Boolean);
        }

        // Read current column widths from the rendered <th> elements
        const table = this.rootRef.el?.querySelector("table.o_list_table");
        if (table) {
            const headers = [...table.querySelectorAll("thead th[data-name]")];
            if (headers.length) {
                const widths = {};
                let totalWidth = 0;
                for (const th of headers) {
                    const w = th.getBoundingClientRect().width;
                    widths[th.dataset.name] = w;
                    totalWidth += w;
                }
                if (totalWidth > 0) {
                    layout.column_widths = {};
                    for (const [name, w] of Object.entries(widths)) {
                        layout.column_widths[name] = Math.round((w / totalWidth) * 10000) / 10000;
                    }
                }
            }
        }

        return layout;
    },

    /**
     * Restore a stored list layout: write optional columns to localStorage
     * and queue stored width ratios for DOM application.
     * Pass null to clear any previously applied layout (e.g. when the
     * shortcut is deactivated).
     */
    applyViewLayout(layout) {
        if (!layout) {
            this._storedColumnWidths = null;
            return;
        }

        // Restore optional column selection via the same localStorage key
        // the renderer reads on each render
        if (layout.optional_columns) {
            const key = this._buildOptionalFieldsKey();
            browser.localStorage.setItem(key, layout.optional_columns.join(","));
        }

        // Store width ratios; the useEffect will apply them after each render
        if (layout.column_widths && Object.keys(layout.column_widths).length) {
            this._storedColumnWidths = layout.column_widths;
        } else {
            this._storedColumnWidths = null;
        }
    },

    /**
     * Apply stored width ratios to the table's <th> elements.
     * Called via requestAnimationFrame after the column-width hook has set
     * its computed widths, so our values take precedence before paint.
     */
    _applyColumnWidthsToDOM() {
        if (!this._storedColumnWidths) {
            return;
        }
        const table = this.rootRef.el?.querySelector("table.o_list_table");
        if (!table) {
            return;
        }
        const headers = [...table.querySelectorAll("thead th[data-name]")];
        if (!headers.length) {
            return;
        }

        // Compute total available width for data columns
        let totalDataWidth = 0;
        for (const th of headers) {
            totalDataWidth += th.getBoundingClientRect().width;
        }
        if (totalDataWidth <= 0) {
            return;
        }

        // Apply stored ratios scaled to the available width
        table.style.tableLayout = "fixed";
        for (const th of headers) {
            const name = th.dataset.name;
            const ratio = this._storedColumnWidths[name];
            if (ratio !== undefined) {
                th.style.width = `${Math.floor(ratio * totalDataWidth)}px`;
            }
        }
    },
});
