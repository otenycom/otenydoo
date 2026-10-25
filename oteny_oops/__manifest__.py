{
    "name": "Oteny Oops Log",
    "version": "19.0.1.0",
    "summary": "Every fault that a user meets in the web client, saved for the administrator, on Odoo Community.",
    "description": """
When the web client opens its error dialog ("Oops! Something went wrong…"),
this module writes the text of the dialog to the Oops Log: the error, the
message, the server and client traceback, the page, the user and the time.
The dialog shows a link to the record, which the user can paste in a bug
report. Business messages (a UserError, a ValidationError, an AccessError) open
another dialog and are not written.

Only Settings administrators read the Oops Log, under Settings › Technical ›
Oteny › Oops Log (debug mode). One record holds at most 1 MB of text. The daily
auto-vacuum deletes the records whose fault nobody met for
oteny_oops.max_age_days days (default 30).

Odoo Enterprise saves the same text as a file in Documents when the user
clicks Share in the dialog; Odoo Community saves nothing.
""",
    "author": "Oteny",
    "website": "https://oteny.com",
    "license": "LGPL-3",
    "category": "Hidden",
    "depends": ["base", "web", "oteny_base"],
    "data": [
        "security/ir.model.access.csv",
        "data/ir_config_parameter.xml",
        "views/oteny_oops_log_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "oteny_oops/static/src/**/*",
        ],
        "web.assets_unit_tests": [
            "oteny_oops/static/tests/**/*",
        ],
    },
    "installable": True,
    "application": False,
}
