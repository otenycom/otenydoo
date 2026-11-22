import { registry } from "@web/core/registry";
import { useService } from "@web/core/utils/hooks";
import { StatusBarField, statusBarField } from "@web/views/fields/statusbar/statusbar_field";
import { _t } from "@web/core/l10n/translation";

const fieldRegistry = registry.category("fields");

export class RiverflowStatusBar extends StatusBarField {
    static template = "riverflow.RiverflowStatusBar";
    static props = {
        ...StatusBarField.props,
        statusbarJsonField: { type: String, optional: true },
        captionField: { type: String, optional: true },
    };

    setup() {
        super.setup();
        this.action = useService("action");
        this.orm = useService("orm");
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

    /**
     * caption_field: a Many2one to the linked record whose workflow the header
     * shows. Its name stands above the states, so the user sees whose states and
     * buttons they are.
     */
    get captionRecord() {
        return this.props.captionField && this.props.record.data[this.props.captionField];
    }

    /** Open the linked record, as a click on a read-only Many2one does (many2one.js). */
    async openCaptionRecord() {
        const action = await this.orm.call(
            this.props.record.fields[this.props.captionField].relation,
            "get_formview_action",
            [[this.captionRecord.id]],
            { context: this.props.record.context }
        );
        await this.action.doAction(action);
    }
}

export const riverflowStatusBar = {
    ...statusBarField,
    component: RiverflowStatusBar,
    // Load the name of the caption record: Odoo reads only the id of a Many2one
    // that is always invisible in the view (relational_model/utils.js, getFieldsSpec).
    fieldDependencies: ({ options }) =>
        options.caption_field ? [{ name: options.caption_field, type: "many2one" }] : [],
    extractProps: (fieldInfo, dynamicInfo) => ({
        ...statusBarField.extractProps(fieldInfo, dynamicInfo),
        statusbarJsonField: fieldInfo.options.statusbar_json_field,
        captionField: fieldInfo.options.caption_field,
    }),
};

fieldRegistry.add("riverflow_statusbar", riverflowStatusBar);