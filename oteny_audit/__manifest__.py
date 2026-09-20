{
    "name": "oteny_audit",
    # 19.0.1.519: redact credentials in audit values + skip messages in an audit-ignored container (migration scrubs stored secrets)
    # 19.0.1.474: perf: split aggregated audit view into UNION ALL (push value-search into log scan; 36s->~100ms)
    # 19.0.1.473: perf: partial snapshot GIN + (field_name, create_date) base index
    # 19.0.1.598: transaction_id becomes a 64-bit column (BigInteger, int8) on the log, the ref and the aggregated view; txid_current() passed 2^31 on production on 2026-09-12 and every write failed with "integer out of range" (white screen). migrations/19.0.1.598/pre-migrate.py converts the columns; the field type keeps fresh installs correct.
    "version": "19.0.1.610",
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
        ],
    },
    "demo": [],
    "application": True,
    "auto_install": False,
    "license": "OPL-1",
}
