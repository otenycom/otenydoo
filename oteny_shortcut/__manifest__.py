{
    "name": "View Shortcuts",
    "version": "19.0.1.350",
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
    "license": "LGPL-3",
}
