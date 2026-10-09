/** @odoo-module **/

import { onPatched } from "@odoo/owl";
import { Domain } from "@web/core/domain";
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
 *
 * After the reload a moved card shows first in its new column, until the page is
 * refreshed (radar plan crewradar-hr/plans/kanban-moved-card.md). A user who
 * moves several cards, or the same card on through the columns, finds them at the
 * top instead of at their place by the column's order, which in a long column
 * (265 employees in Employed, 80 loaded per page) is often not even loaded. Every
 * step reloads the board, so the board remembers the moved cards and puts them
 * first again after each reload.
 */
patch(KanbanRenderer.prototype, {
    setup() {
        super.setup(...arguments);
        this.riverflowOrm = useService("orm");
        this.riverflowAction = useService("action");
        // The cards a drop moved on this board, the latest first. They stay first
        // in their column until the page is refreshed (riverflowPinMovedCards).
        this.riverflowMovedIds = [];
        // The record to scroll to once the board has drawn it.
        this.riverflowCardToShow = null;
        onPatched(() => this.riverflowScrollToCard());
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
            onClose: async () => {
                await list.model.root.load();
                await this.riverflowPinMovedCards(record.resId, sourceGroup.value);
            },
        });
    },

    /**
     * Puts the moved cards first in their columns after a reload, the latest move
     * on top, and scrolls to the card of this drop.
     *
     * After OK the step moved the card out of the column it was dragged from;
     * after Discard it is still there and is not added. A moved card that the
     * reload did not load (beyond the first page of a long column) is read and
     * added on top of its column. A card that left the board's filter, or sits in
     * a folded column, is left out. "Load more" in a column shows its own order
     * again until the next drop.
     */
    async riverflowPinMovedCards(resId, sourceGroupValue) {
        const { list } = this.props;
        const loadedCards = () => list.groups.flatMap((group) => group.list.records);
        const sourceGroup = list.groups.find((group) => group.value === sourceGroupValue);
        if (!sourceGroup?.list.records.some((r) => r.resId === resId)) {
            this.riverflowMovedIds = [
                resId,
                ...this.riverflowMovedIds.filter((id) => id !== resId),
            ];
        }

        const missing = this.riverflowMovedIds.filter(
            (id) => !loadedCards().some((r) => r.resId === id)
        );
        if (missing.length) {
            const groupBy = list.groupByField.name;
            const rows = await this.riverflowOrm.searchRead(
                list.resModel,
                Domain.and([list.domain, [["id", "in", missing]]]).toList(),
                [groupBy],
                { context: list.context }
            );
            for (const row of rows) {
                const value = Array.isArray(row[groupBy]) ? row[groupBy][0] : row[groupBy];
                const group = list.groups.find((g) => g.value === (value || false));
                if (group && !group.isFolded) {
                    await group.list.addExistingRecord(row.id, true);
                    // The column's count already holds the card.
                    group.list.count--;
                }
            }
        }

        // Oldest move first, so each later move lands above it.
        for (const id of [...this.riverflowMovedIds].reverse()) {
            for (const group of list.groups) {
                const records = group.list.records;
                const index = records.findIndex((r) => r.resId === id);
                if (index > 0) {
                    records.unshift(...records.splice(index, 1));
                }
            }
        }

        const card = loadedCards().find((r) => r.resId === resId);
        if (card) {
            this.riverflowCardToShow = card.id;
            // Draw again, so the scroll in onPatched also runs when the reload had
            // already drawn the board before the card was known.
            this.render();
        }
    },

    riverflowScrollToCard() {
        if (!this.riverflowCardToShow) {
            return;
        }
        const cardEl = this.rootRef.el?.querySelector(
            `.o_kanban_record[data-id="${this.riverflowCardToShow}"]`
        );
        if (cardEl) {
            this.riverflowCardToShow = null;
            // "nearest" leaves a card that is already in view where it is.
            cardEl.scrollIntoView({ block: "nearest", inline: "nearest" });
        }
    },
});
