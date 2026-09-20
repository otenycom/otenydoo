{
    "name": "View Shortcuts",
    "version": "19.0.1.227",  # 19.0.1.221: the Shortcut Icon of a filter is chosen from a searchable list of every Font Awesome icon the backend has loaded, instead of being typed by hand (new fa_icon widget on a Char field, static/src/fields/fa_icon; the stored value stays the bare class, e.g. fa-clock-o).  # 19.0.1.220: shortcuts everywhere and inside forms (Thijs 2026-09-14). The banner is rendered by Odoo's Layout on every multi-record view (no js_class any more; the shortcut_list / shortcut_calendar views are removed; ListController and CalendarController are patched to expose their layout through env.shortcutLayout) and reads its buttons from the favorites the view already loads (get_filters carries the shortcut fields, so no get_shortcuts request per view open). A shortcut also shows as a button row above every list-mode x2many field of its model inside a form (X2ManyField patch; the rows its domain does not match are hidden by the list renderer; Default Filter = active at open; the last click is not remembered). New ir.filters.shortcut_show_when (Text; on the filter form its own Show When group: the names it can use, then a Python code box), a Python expression evaluated in the browser (subject, subject_id, field, view, uid, context; empty = everywhere) that says where a shortcut's button shows; a shortcut's Default Filter applies only where it is true. Self-service (2026-09-15): the shortcut settings are fields on the filter form (is_shortcut, shortcut_show_preset Everywhere / Views only / Forms only / One form only / Custom rule, shortcut_subject_model_id) that read and write the position and the expression, a new button goes last; no gear or button in the views, the settings are changed when editing the filter (both ir.filters forms carry the Shortcut group). The in-form row moves into the toolbar of buttons directly above the list, right-aligned, when there is one.  # 19.0.1.163: Shortcut View field is now a dynamic selection (ir.filters._shortcut_view_type_selection) listing every switchable view type from ir.ui.view — custom view types (e.g. the credential timeline) appear automatically, no per-module selection_add. New shortcut_view_type_whitelist (Json, per-model compute) + the filterable_selection widget (whitelist_fname) narrow the dropdown to view types the filter's model actually has.  # 19.0.1.162: onShortcutClick only calls switchView when the target shortcut_view_type is among the current action's viewSwitcherEntries — a favorite whose preferred view isn't exposed by this action no longer throws ViewNotFoundError; the filter is applied in place instead.  # 19.0.1.161: shortcut banner view-type icon now resolved from session.view_info (the server-side ir.ui.view._get_view_info map the view switcher uses) instead of a hardcoded list/calendar t-if, so custom view types (e.g. rivercreds_plan_timeline) get their registered icon automatically.
    "depends": ["web"],
    "author": "Oteny",
    "category": "Tools",
    "description": """
    Extends ir.filters with shortcut fields so saved Favorites can appear
    as quick-access buttons: in a banner above every multi-record view
    (list, kanban, calendar, pivot, graph, gantt, custom views), and, for
    filters placed in Forms, as a button row above lists of the model
    inside a form (x2many fields in list mode).
    """,
    "data": [
        "security/ir.model.access.csv",
        "wizard/store_layout_wizard_views.xml",
        "views/ir_filters_views.xml",
        "data/ir_config_parameter_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "oteny_shortcut/static/src/components/**/*",
            "oteny_shortcut/static/src/fields/**/*",
            "oteny_shortcut/static/src/views/**/*",
        ],
        "web.assets_unit_tests": [
            "oteny_shortcut/static/tests/**/*",
        ],
    },
    "auto_install": False,
    "license": "OEEL-1",
}
