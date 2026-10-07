import { registry } from "@web/core/registry";

// Opens a team from the team list and waits for its Members list. The team form
// carries the Members list in two variants (editable for access-rights
// administrators, read-only for everybody else), so the Python side runs this
// tour once per variant.
registry.category("web_tour.tours").add("riverflow_team_form_opens", {
    steps: () => [
        {
            content: "Open the Human Resources team from the team list",
            trigger: ".o_list_view .o_data_row .o_data_cell:contains(Human Resources)",
            run: "click",
        },
        {
            content: "The team form shows its Members list",
            trigger: ".o_form_view .o_field_widget[name=member_ids]",
        },
    ],
});
