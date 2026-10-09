/** @odoo-module **/

import { KanbanRecord } from "@web/views/kanban/kanban_record";
import { patch } from "@web/core/utils/patch";

/**
 * Tints a kanban card that a user changed a few seconds ago, as list_renderer_patch.js
 * tints the row in a list (radar plan crewradar-hr/plans/kanban-moved-card.md).
 *
 * A model that inherits `riverflow.highlight.row.mixin` flags such a record in
 * `highlight_row`; `highlight_row_type` picks the colour (by default `info`, a light
 * blue). The card gets the same `row-bg-*` class as a list row, so one sign means
 * one thing on lists and boards.
 * A board shows the tint only when its view loads the two fields, as a list does.
 */
patch(KanbanRecord.prototype, {
    getRecordClasses() {
        let classNames = super.getRecordClasses();
        const { data } = this.props.record;
        if (data.highlight_row) {
            classNames += " row-bg-" + (data.highlight_row_type || "info");
        }
        return classNames;
    },
});
