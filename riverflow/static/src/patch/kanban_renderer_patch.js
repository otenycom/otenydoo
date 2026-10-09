/** @odoo-module **/

import { KanbanRenderer } from "@web/views/kanban/kanban_renderer";
import { patch } from "@web/core/utils/patch";
import { useService } from "@web/core/utils/hooks";

/**
 * A kanban grouped by the workflow state never writes the state when a card is
 * dropped in another column (radar pipeline applicants plan, decision 127).
 *
 * A state changes only through a step, because the step screen does the step's
 * work. Odoo's own drop writes the grouped field straight onto the record, so
 * an employee could reach Employed without a started contract. Here the drop
 * asks the server for the step into the column (`action_kanban_drop` on
 * riverflow.state.mixin) and opens its screen, as the button would. The card
 * stays in its column while the screen is open; the board reloads when the
 * screen closes, so after OK the card moves and after Discard nothing changed.
 *
 * Every board grouped by `state_id` to `riverflow.state` follows this, so a
 * consuming app needs no setting per board. A drop inside one column, and every
 * board grouped by another field, keep Odoo's behaviour.
 */
patch(KanbanRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        this.riverflowOrm = useService("orm");
        this.riverflowAction = useService("action");
    },

    get isRiverflowStateBoard() {
        const { isGrouped, groupByField } = this.props.list;
        return (
            isGrouped &&
            groupByField.name === "state_id" &&
            groupByField.relation === "riverflow.state"
        );
    },

    async sortRecordDrop(dataRecordId, dataGroupId, params) {
        const { parent } = params;
        if (
            !this.isRiverflowStateBoard ||
            !parent?.classList.contains("o_kanban_hover") ||
            parent.dataset.id === dataGroupId
        ) {
            return super.sortRecordDrop(...arguments);
        }
        parent.classList.remove("o_kanban_hover");

        const { list } = this.props;
        const sourceGroup = list.groups.find((group) => group.id === dataGroupId);
        const targetGroup = list.groups.find((group) => group.id === parent.dataset.id);
        const record = sourceGroup?.list.records.find((r) => r.id === dataRecordId);
        if (!record || !targetGroup) {
            // The board re-rendered during the drag, as in Odoo's own drop.
            return;
        }

        this.toggleProcessing(dataRecordId, true);
        let action;
        try {
            action = await this.riverflowOrm.call(list.resModel, "action_kanban_drop", [
                [record.resId],
                sourceGroup.value || false,
                targetGroup.value || false,
            ]);
        } finally {
            this.toggleProcessing(dataRecordId, false);
        }
        await this.riverflowAction.doAction(action, {
            onClose: () => list.model.root.load(),
        });
    },
});
