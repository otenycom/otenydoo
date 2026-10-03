import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component } from "@odoo/owl";

/**
 * The findings box of the save check (riverflow.save.check.mixin).
 *
 * Shows the findings the server stored when it held the OK: errors in a red
 * box (the save cannot go on until they are fixed), warnings in a yellow box
 * (the save goes on when the user ticks the confirmation field below it).
 * Each message is prefixed by its subject, e.g. the file it is about.
 */
export class SaveCheckField extends Component {
    static template = "riverflow.SaveCheckField";
    static props = { ...standardFieldProps };

    get findings() {
        const value = this.props.record.data[this.props.name];
        return Array.isArray(value) ? value : [];
    }

    get errors() {
        return this.findings.filter((f) => f.level === "error");
    }

    get warnings() {
        return this.findings.filter((f) => f.level !== "error");
    }
}

export const saveCheckField = {
    component: SaveCheckField,
    supportedTypes: ["json"],
};

registry.category("fields").add("save_check", saveCheckField);
