import { _t } from "@web/core/l10n/translation";
import { registry } from "@web/core/registry";
import { formatInteger } from "@web/views/fields/formatters";
import { standardFieldProps } from "@web/views/fields/standard_field_props";

import { Component } from "@odoo/owl";

/**
 * The transaction ID of an audit row, shown as a link. A click adds the filter
 * "Transaction ID = <value>" to the search of the view, so the list shows every
 * change of that database transaction. The click does not open the row.
 */
export class TransactionLinkField extends Component {
    static template = "oteny_audit.TransactionLinkField";
    static props = { ...standardFieldProps };

    get value() {
        return this.props.record.data[this.props.name];
    }

    get formattedValue() {
        return formatInteger(this.value);
    }

    onClick() {
        this.env.searchModel.splitAndAddDomain([[this.props.name, "=", this.value]]);
    }
}

export const transactionLinkField = {
    component: TransactionLinkField,
    displayName: _t("Transaction Link"),
    supportedTypes: ["integer"],
};

registry.category("fields").add("audit_transaction_link", transactionLinkField);
