import { registry } from "@web/core/registry";
import { StatusBarField, statusBarField } from "@web/views/fields/statusbar/statusbar_field";
import { _t } from "@web/core/l10n/translation";

const fieldRegistry = registry.category("fields");

export class RiverflowStatusBar extends StatusBarField {
    static props = {
        ...StatusBarField.props,
        statusbarJsonField: { type: String, optional: true },
    };

    setup() {
        super.setup();
    }

    /**
     * Override the getAllItems method to provide custom status values
     * @override
     * @returns {Array}
     */
    getAllItems() {
        // statusbar_json_field: the statusbar of a linked record, for a form that
        // shows that record's workflow in its header (default: the record's own).
        const json = this.props.record.data[this.props.statusbarJsonField || "state_id_statusbar_json"];
        const statuses = json && json["states"];
        if (!statuses) {
            // This happens in new-record mode, where the record is not saved yet
            return [];
        }
        return statuses;
    }
}

export const riverflowStatusBar = {
    ...statusBarField,
    component: RiverflowStatusBar,
    extractProps: (fieldInfo, dynamicInfo) => ({
        ...statusBarField.extractProps(fieldInfo, dynamicInfo),
        statusbarJsonField: fieldInfo.options.statusbar_json_field,
    }),
};

fieldRegistry.add("riverflow_statusbar", riverflowStatusBar);