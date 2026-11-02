/** @odoo-module **/

import { registry } from "@web/core/registry";
import { standardFieldProps } from "@web/views/fields/standard_field_props";
import { Component, onWillRender, useRef, useState } from "@odoo/owl";
import { useService } from "@web/core/utils/hooks";
import { DateTimeInput } from "@web/core/datetime/datetime_input";

export class TransitionButtons extends Component {
    static template = "riverflow.TransitionButtons";
    static components = { DateTimeInput };
    static props = {
        ...standardFieldProps,
        maxButtons: { type: String, optional: true },
        layout: { type: String, optional: true },
    };

    setup() {
        this.orm = useService("orm");
        this.action = useService("action");
        this.notification = useService("notification");
        this.inputRef = useRef("inputElement");
        this.projectDeadlineInput = useRef("projectDeadlineInput");
        this.expandedTemplates = new Set();
        this.state = useState({
            projectDeadline: null,
            searchQuery: "",
        });


        onWillRender(() => {
            this.fieldValueState = this.fieldValue(this.props);
        });
    }

    fieldValue(props) {
        const value = props.record.data[props.name];
        if (!value) {
            return {
                buttons: [],
                text: "",
            };
        }
        return value;
    }

    buttonDefs() {
        return this.fieldValueState.buttons;
    }

    buttonIconClass(button) {
        let class_name = "icon_span";
        if (button.icon)
            class_name += " fa " + button.icon;
        return class_name;
    }

    iconClass() {
        if (this.fieldValueState.workflow_icon)
            return "fa " + this.fieldValueState.workflow_icon;
        else return "";
    }

    useFullListLayout() {
        return this.layout() === "full_list";
    }
    useFormHeaderLayout() {
        return this.layout() === "form_header";
    }

    layout() {
        // full_list -> list of all start transitions displayed in a start transition selection wizard
        // form_header -> show transition buttons for a record's form header
        // list -> show transition link buttons for a record in a grid
        if (this.fieldValueState.layout)
            return this.fieldValueState.layout;
        else if (this.props.layout)
            return this.props.layout;
        else return "list";
    }

    text() {
        return this.fieldValueState.text;
    }

    datalistId() {
        return this.props.record.resModel + this.props.record.resId + "_transition_options";
    }

    reloadOnClose() {
        return this.fieldValueState.reload_on_close == true;
    }

    /**
     * The onClose of the transition wizard's dialog: reload the record behind it.
     *
     * Not when the wizard opened another record in the main window (an action
     * with target current, for example the new service of the radar Change
     * Appointment): Odoo then closes the dialog with noReload, as its own
     * controllers expect (form_controller.js, kanban_controller.js). The form
     * behind the dialog is already destroyed, and a load through its services
     * never resolves (useService in core/utils/hooks.js). The dialog service
     * removes the dialog only after this callback resolves (overlay_service.js),
     * so the dialog stayed on the screen over the new record, now and then:
     * whenever the form was destroyed before its load answered.
     */
    async reloadAfterTransition({ noReload } = {}) {
        if (!noReload && this.reloadOnClose()) {
            await this.props.record.model.root.load();
        }
    }

    stateClass() {
        if (this.fieldValueState.is_end_state)
            return "riverflow_end_state";
        else return "riverflow_pending_state";
    }


    maxButtons() {
        return parseInt(this.props.maxButtons) || 5;
    }

    async saveRecord(node) {
        if (node.props.record) {
            await node.props.record.save();
        }
        if (node.parent)
            await this.saveRecord(node.parent);
    }

    async saveRecords() {
        await this.saveRecord(this.__owl__)
    }

    async onTransitionChange(event) {
        const selectedValue = event.target.value;
        const options = event.target.list.options;
        let selectedButton;

        for (let i = 0; i < options.length; i++) {
            if (options[i].value === selectedValue) {
                const buttonIndex = options[i].getAttribute('data-index');
                selectedButton = this.buttonDefs()[buttonIndex];
                break;
            }
        }

        if (selectedButton) {
            await this.executeTransition(selectedButton);
            // Clear the input after execution
            event.target.value = '';
        }
    }

    async executeTransition(button) {
        await this.saveRecords();

        const action = {
            type: "object",
            resId: this.props.record.resId,
            name: button.action,
            resModel: this.props.record.resModel,
            context: button.context,
            onClose: (closeParams) => this.reloadAfterTransition(closeParams),
        }
        await this.action.doActionButton(action);
    }

    onSpanClick() {
        const inputElement = this.inputRef.el;
        if (inputElement) {
            inputElement.focus();
            inputElement.click();
        }
    }

    hasChildren(button) {
        const buttons = this.buttonDefs();
        const currentIndex = buttons.indexOf(button);
        if (currentIndex < buttons.length - 1) {
            return buttons[currentIndex + 1].indent_level > button.indent_level;
        }
        return false;
    }

    shouldShowButton(button) {
        if (button.indent_level === 0) return true;

        const buttons = this.buttonDefs();
        const currentIndex = buttons.indexOf(button);

        // Find parent template
        for (let i = currentIndex - 1; i >= 0; i--) {
            if (buttons[i].indent_level < button.indent_level) {
                // Found the parent, check if it's expanded
                return this.isTemplateExpanded(buttons[i].index);
            }
        }
        return true;
    }

    filteredByQuery(button) {
        const query = (this.state.searchQuery || "").trim().toLowerCase();
        if (!query) return true;
        const caption = (button.caption || "").toLowerCase();
        const icon = (button.icon || "").toLowerCase();
        return caption.includes(query) || icon.includes(query);
    }

    onSearchInput(value) {
        this.state.searchQuery = value || "";
    }

    toggleTemplateExpansion(index) {
        if (this.expandedTemplates.has(index)) {
            this.expandedTemplates.delete(index);
        } else {
            this.expandedTemplates.add(index);
        }
        this.render();
    }

    isTemplateExpanded(index) {
        return this.expandedTemplates.has(index);
    }

    toggleCheckbox(buttonIndex) {
        const buttons = this.buttonDefs();
        const button = buttons[buttonIndex];

        // Only toggle if it's a top-level item
        if (button.indent_level === 0) {
            button.checked = !button.checked;
            this.render();
        }
    }

    // Add method to handle deadline changes
    onDeadlineChanged(date) {
        this.state.projectDeadline = date;
    }

    async executeSelectedTransitions() {
        // Only consider top-level templates
        const selectedButtons = this.buttonDefs().filter(
            button => button.checked && button.indent_level === 0
        );

        if (selectedButtons.length === 0) {
            this.notification.add(
                "Please select at least one template.",
                { type: "warning" }
            );
            return false;
        }

        // Deadline is required when creating services from templates
        if (!this.state.projectDeadline) {
            this.notification.add(
                "Please set the Deadline before creating services.",
                { type: "warning" }
            );
            return false;
        }

        await this.saveRecords();

        // Read the deadline from the state instead of input field
        const projectDeadline = this.state.projectDeadline;

        // Execute transitions in sequence
        for (const button of selectedButtons) {
            // Add the deadline to the context if it exists
            const context = {
                ...button.context,
                ...(projectDeadline && { default_project_deadline: projectDeadline })
            };

            const action = {
                type: "object",
                resId: this.props.record.resId,
                name: button.action,
                resModel: this.props.record.resModel,
                context: context,
                onClose: (closeParams) => this.reloadAfterTransition(closeParams),
            };
            await this.action.doActionButton(action);
        }

        return true;
    }
}

export const transitionButtons = {
    component: TransitionButtons,
    displayName: "Transition Buttons",
    supportedTypes: ["json"],
    extractProps: ({ attrs, options, viewType }, dynamicInfo) => ({
        maxButtons: attrs.max_buttons || "5",
        layout: attrs.layout || "list",
    }),
};

registry.category("fields").add("transition_buttons", transitionButtons);