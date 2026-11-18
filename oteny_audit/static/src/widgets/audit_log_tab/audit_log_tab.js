import { deserializeDateTime } from "@web/core/l10n/dates";
import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { useRecordObserver } from "@web/model/relational_model/utils";
import { standardWidgetProps } from "@web/views/widgets/standard_widget_props";

import { Component, markup, useState } from "@odoo/owl";

/**
 * The Audit tab of a form: the newest rows of the record's audit log, grouped per database
 * transaction, and a "Full Screen" link below them for the whole list. The rows are the same as the Action
 * menu's "Audit Log for …": the record's own changes and those of its children.
 *
 * Each transaction (one save, one button) gets a header with the user and the time, and the
 * changes of that transaction below it; the background alternates per transaction. The server
 * leaves "by <user> on <time>" out of the record headers of the captions for this widget
 * (context `oteny_audit_caption_without_user`), so they are not repeated on every record.
 *
 * Odoo draws a notebook page only when the user opens it, so the rows are read only then. They
 * are read again when the record is saved (its write_date changes) or another record is shown.
 */
export class AuditLogTab extends Component {
    static template = "oteny_audit.AuditLogTab";
    static props = { ...standardWidgetProps };
    static LIMIT = 160;
    static DATETIME_FORMAT = "dd-LLL-yy HH:mm:ss";

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.state = useState({ transactions: [], rowCount: 0, count: 0, loaded: false });
        this.loadedKey = null;
        useRecordObserver(async (record) => {
            // Read here, so the observer runs again when one of them changes. It also runs
            // when other values of the record are touched; the key keeps that from a reload.
            const key = `${record.resModel},${record.resId},${record.data.write_date}`;
            if (key === this.loadedKey) {
                return;
            }
            this.loadedKey = key;
            await this.load(record.resModel, record.resId);
        });
    }

    domain(resModel, resId) {
        return [
            ["model_name", "=", resModel],
            ["record_id", "in", [resId]],
        ];
    }

    async load(resModel, resId) {
        if (!resId) {
            Object.assign(this.state, { transactions: [], rowCount: 0, count: 0, loaded: true });
            return;
        }
        const { records, length } = await this.orm.webSearchRead(
            "oteny.audit.log.aggregated",
            this.domain(resModel, resId),
            {
                specification: {
                    caption: {},
                    transaction_id: {},
                    create_date: {},
                    create_uid: { fields: { display_name: {} } },
                },
                limit: AuditLogTab.LIMIT,
                context: { oteny_audit_caption_without_user: true },
            }
        );
        Object.assign(this.state, {
            transactions: this.groupByTransaction(records),
            rowCount: records.length,
            count: length,
            loaded: true,
        });
    }

    /**
     * The rows come newest first, and the rows of one transaction are next to each other
     * (they share their time; the log row id orders them). A new group starts where the
     * transaction or the time changes: the transaction number alone can repeat after a
     * database move (see the oteny-audit skill, "The transaction number is a 64-bit column").
     */
    groupByTransaction(records) {
        const transactions = [];
        for (const row of records) {
            let group = transactions.at(-1);
            if (!group || group.transactionId !== row.transaction_id || group.createDate !== row.create_date) {
                group = {
                    key: `${row.transaction_id}-${row.create_date}-${row.id}`,
                    transactionId: row.transaction_id,
                    createDate: row.create_date,
                    when: row.create_date
                        ? deserializeDateTime(row.create_date).toFormat(AuditLogTab.DATETIME_FORMAT)
                        : "",
                    user: row.create_uid?.display_name || "",
                    rows: [],
                };
                transactions.push(group);
            }
            group.rows.push({ id: row.id, caption: markup(row.caption || "") });
        }
        return transactions;
    }

    openFullScreen() {
        const { record } = this.props;
        this.action.doAction({
            type: "ir.actions.act_window",
            name: _t("Audit Log for %s", record.data.display_name || ""),
            res_model: "oteny.audit.log.aggregated",
            views: [
                [false, "list"],
                [false, "form"],
            ],
            domain: this.domain(record.resModel, record.resId),
            context: { create: false, edit: false },
            target: "current",
        });
    }
}

export const auditLogTab = {
    component: AuditLogTab,
    fieldDependencies: [
        { name: "write_date", type: "datetime" },
        { name: "display_name", type: "char" },
    ],
};

registry.category("view_widgets").add("oteny_audit_log", auditLogTab);
