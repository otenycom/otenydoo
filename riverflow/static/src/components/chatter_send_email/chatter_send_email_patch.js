import { Chatter } from "@mail/chatter/web_portal/chatter";
import { Composer } from "@mail/core/common/composer";
import { messageActionsRegistry, registerMessageAction } from "@mail/core/common/message_actions";
import "@mail/core/web/message_actions_patch";
import { Thread } from "@mail/core/common/thread_model";
import { toRaw } from "@odoo/owl";
import { _t } from "@web/core/l10n/translation";
import { useService } from "@web/core/utils/hooks";
import { patch } from "@web/core/utils/patch";

/**
 * Send Email in the chatter (plan riverflow/plans/chatter-send-message.md).
 *
 * The server sends riverflowEmailMode with the thread data:
 * - "send_email": the Send Email button takes the place of Send message (§ 5);
 * - "note_only": no e-mail button, only Log note (a document record, decision 9);
 * - false: Odoo's Send message stays (an invoice, decision 10), with the guard below.
 */
patch(Thread.prototype, {
    setup() {
        super.setup(...arguments);
        /** @type {"send_email"|"note_only"|false|undefined} */
        this.riverflowEmailMode = undefined;
        /** @type {number[]} */
        this.riverflowIncomingEmailIds = [];
        /** @type {number[]} the e-mails that went out from the record to an outside contact */
        this.riverflowSentEmailIds = [];
    },
});

patch(Chatter.prototype, {
    setup() {
        super.setup(...arguments);
        this.riverflowSendEmailOrm = useService("orm");
        this.riverflowSendEmailAction = useService("action");
    },

    get requestList() {
        return [...super.requestList, "riverflowEmailMode"];
    },

    get childSubEnv() {
        const res = super.childSubEnv;
        if (res.inChatter) {
            Object.assign(res.inChatter, {
                riverflowReply: (messageId) => this.onRiverflowReply(messageId),
            });
        }
        return res;
    },

    get riverflowHidesSendMessage() {
        const thread = this.state.thread;
        const mode = thread?.riverflowEmailMode;
        // Until the thread has loaded the mode is unknown; showing Send message then
        // flashes the old button on every riverflow record while it loads. Once loaded, an
        // unknown mode (a server without this field) keeps Odoo's Send message.
        if (thread?.id && mode === undefined && !thread.isLoaded) {
            return true;
        }
        return mode === "send_email" || mode === "note_only";
    },

    async onClickRiverflowSendEmail() {
        await this.riverflowRunEmailAction("action_riverflow_send_email", []);
    },

    async onRiverflowReply(messageId) {
        await this.riverflowRunEmailAction("action_riverflow_reply", [messageId]);
    },

    async riverflowRunEmailAction(method, args) {
        const thread = this.state.thread;
        if (!thread.id) {
            const saved = await this.props.saveRecord?.();
            if (!saved) {
                return;
            }
        }
        const action = await this.riverflowSendEmailOrm.call(thread.model, method, [
            [thread.id],
            ...args,
        ]);
        await this.riverflowSendEmailAction.doAction(action, {
            onClose: async () => {
                await this.load(thread, [...this.requestList, "messages"]);
                this.reloadParentView();
            },
        });
    },
});

/**
 * Reply on an incoming e-mail (§ 5 B) or on an e-mail that went out (§ 10, as Reply All on a
 * sent e-mail), where the record uses Send Email.
 */
registerMessageAction("riverflow-reply", {
    condition: ({ message, thread, owner }) =>
        Boolean(owner.env.inChatter?.riverflowReply) &&
        thread?.riverflowEmailMode === "send_email" &&
        [...(thread.riverflowIncomingEmailIds || []), ...(thread.riverflowSentEmailIds || [])].includes(
            message.id
        ),
    icon: "fa fa-reply",
    name: _t("Reply"),
    onSelected: ({ message, owner }) => owner.env.inChatter.riverflowReply(message.id),
    sequence: 15,
});

/**
 * Odoo's Reply All and Forward open its own full composer: more doors for an e-mail beside
 * Send Email. On a record with Send Email they stay hidden; a follow-up before a reply goes
 * through the state's e-mail transition (Resend, Send Reminder), else through the reply
 * wizard on the sent e-mail; a reply goes through the reply wizard, and a new e-mail through
 * Send Email (one structure, decision 1).
 */
for (const actionId of ["reply-all", "forward"]) {
    const odooAction = messageActionsRegistry.get(actionId, null);
    if (!odooAction) {
        continue;
    }
    messageActionsRegistry.add(
        actionId,
        {
            ...odooAction,
            condition: (args) =>
                !["send_email", "note_only"].includes(args.thread?.riverflowEmailMode) &&
                odooAction.condition(args),
        },
        { force: true }
    );
}

/**
 * Where Odoo's Send message stays (an invoice), it may not reach no one: an empty "To" and
 * no follower besides the writer saves the message and e-mails nobody (decision 10).
 */
patch(Composer.prototype, {
    async sendMessage() {
        const composer = toRaw(this.props.composer);
        const thread = composer.thread;
        if (
            this.props.type === "message" &&
            !composer.message &&
            thread &&
            thread.model !== "discuss.channel" &&
            thread.model !== "mail.box" &&
            thread.followersCount !== undefined
        ) {
            const recipients = [
                ...(thread.suggestedRecipients || []),
                ...(thread.additionalRecipients || []),
            ];
            // followersCount counts every follower; selfFollower is the writer, whom Odoo
            // never e-mails.
            const otherFollowerCount = thread.followersCount - (thread.selfFollower ? 1 : 0);
            if (recipients.length === 0 && otherFollowerCount <= 0) {
                this.env.services.notification.add(
                    _t(
                        "This message reaches no one: the To field is empty and nobody else follows this record. Add a recipient."
                    ),
                    { type: "danger" }
                );
                return;
            }
        }
        return super.sendMessage(...arguments);
    },
});
