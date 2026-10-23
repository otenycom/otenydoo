import { describe, expect, test } from "@odoo/hoot";
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
 * Mark as Reviewed in the chatter top bar (radar plan
 * riverflow/plans/mark-as-reviewed-in-chatter.md). The Python tests prove the
 * count in the thread data and the bus notice; this test proves the top bar:
 * the pill shows while the count is above 0, a click calls the action and the
 * pill goes, and a count that arrives later (the bus) shows the pill again.
 */

describe.current.tags("desktop");
defineMailModels();

test("the chatter top bar shows the review count and Mark as Reviewed", async () => {
    const pyEnv = await startServer();
    const partnerId = pyEnv["res.partner"].create({ name: "Gacotano" });
    onRpc("res.partner", "action_mark_external_messages_reviewed", ({ args }) => {
        asyncStep(`mark ${args[0]}`);
        return true;
    });
    await start();
    await openFormView("res.partner", partnerId);
    await contains(".o-mail-Chatter-topbar");
    await contains(".o-riverflow-ChatterReview", { count: 0 });

    const store = getService("mail.store");
    store.Thread.insert({ model: "res.partner", id: partnerId, unreviewedMessageCount: 2 });
    await contains(".o-riverflow-ChatterReview-count", { text: "2 to review" });
    // Between Activity / Log note on the left and the spacer before the icons.
    expect(".o-mail-Chatter-logNote ~ .o-riverflow-ChatterReview + .o-mail-Chatter-topbarGrow").toHaveCount(1);

    await click(".o-riverflow-ChatterReview-button");
    await waitForSteps([`mark ${partnerId}`]);
    await contains(".o-riverflow-ChatterReview", { count: 0 });

    store.Thread.insert({ model: "res.partner", id: partnerId, unreviewedMessageCount: 1 });
    await contains(".o-riverflow-ChatterReview-count", { text: "1 to review" });
});
