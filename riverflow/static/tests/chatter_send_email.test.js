import { describe, test } from "@odoo/hoot";
import {
    click,
    contains,
    defineMailModels,
    openFormView,
    start,
    startServer,
} from "@mail/../tests/mail_test_helpers";
import { asyncStep, getService, onRpc, waitForSteps } from "@web/../tests/web_test_helpers";

/**
 * Send Email in the chatter (plan riverflow/plans/chatter-send-message.md). The Python
 * tests prove the mode in the thread data and the actions; this test proves the top bar:
 * Odoo's Send message stays while the mode is false, Send Email takes its place for
 * "send_email" and calls the action, and "note_only" leaves only Log note.
 */

describe.current.tags("desktop");
defineMailModels();

test("Send Email takes the place of Send message by the thread's e-mail mode", async () => {
    const pyEnv = await startServer();
    const partnerId = pyEnv["res.partner"].create({ name: "Client Contact" });
    onRpc("res.partner", "action_riverflow_send_email", ({ args }) => {
        asyncStep(`send email ${args[0]}`);
        return { type: "ir.actions.act_window_close" };
    });
    await start();
    await openFormView("res.partner", partnerId);
    await contains(".o-mail-Chatter-sendMessage");
    await contains(".o-riverflow-Chatter-sendEmail", { count: 0 });

    const store = getService("mail.store");
    store.Thread.insert({ model: "res.partner", id: partnerId, riverflowEmailMode: "send_email" });
    await contains(".o-riverflow-Chatter-sendEmail", { text: "Send Email" });
    await contains(".o-mail-Chatter-sendMessage", { count: 0 });
    await click(".o-riverflow-Chatter-sendEmail");
    await waitForSteps([`send email ${partnerId}`]);

    store.Thread.insert({ model: "res.partner", id: partnerId, riverflowEmailMode: "note_only" });
    await contains(".o-riverflow-Chatter-sendEmail", { count: 0 });
    await contains(".o-mail-Chatter-sendMessage", { count: 0 });
    await contains(".o-mail-Chatter-logNote");
});
