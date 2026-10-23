import { Chatter } from "@mail/chatter/web_portal/chatter";
import { Thread } from "@mail/core/common/thread_model";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";

/**
 * Mark as Reviewed in the chatter top bar (plan
 * riverflow/plans/mark-as-reviewed-in-chatter.md). A record with
 * riverflow.mail.thread.review.mixin sends unreviewedMessageCount with its thread
 * data and through the bus; the top bar shows the count and the button while it
 * is above 0. A record without the mixin sends no count and shows nothing.
 */
patch(Thread.prototype, {
    setup() {
        super.setup(...arguments);
        /** @type {number|undefined} */
        this.unreviewedMessageCount = undefined;
    },
});

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.riverflowOrm = useService("orm");
    },

    get requestList() {
        return [...super.requestList, "unreviewedMessageCount"];
    },

    async onClickMarkReviewed() {
        const thread = this.state.thread;
        await this.riverflowOrm.call(thread.model, "action_mark_external_messages_reviewed", [[thread.id]]);
        thread.unreviewedMessageCount = 0;
        await this.load(thread, ["unreviewedMessageCount"]);
        await this.reloadParentView();
    },
});
