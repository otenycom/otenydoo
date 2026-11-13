{
    "name": "oteny_audit",
    "version": "19.0.1.737",
    "depends": ["base", "mail"],
    "author": "Oteny.com",
    "category": "Extra Tools",
    "description": """
    Audit Module by Oteny.com for Odoo
    """,
    "data": [
        "security/ir.model.access.csv",
        "views/res_config_settings_views.xml",
        "views/oteny_audit_log_views.xml",
        "data/ir_cron_data.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "oteny_audit/static/src/scss/oteny_audit.variables.scss",
            "oteny_audit/static/src/scss/oteny_audit.scss",
            "oteny_audit/static/src/scss/oteny_audit.dark.scss",
            "oteny_audit/static/src/fields/**/*",
        ],
        "web.assets_unit_tests": [
            "oteny_audit/static/tests/**/*",
        ],
    },
    "demo": [],
    "application": True,
    "auto_install": False,
    "license": "LGPL-3",
}
