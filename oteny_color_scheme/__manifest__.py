{
    "name": "Oteny Light and Dark Mode",
    "version": "19.0.1.4",
    "summary": "Light and dark mode for Odoo Community: a Theme preference per user.",
    "description": """
Odoo Community can load a dark style bundle (web.assets_web_dark), but has no
dark colours and no way to choose it. This module adds both: a Theme field on
the user's Preferences (System, Light or Dark) and a dark colour set. System
follows the colour scheme of the operating system.
""",
    "author": "Oteny",
    "website": "https://oteny.com",
    "license": "LGPL-3",
    "category": "Productivity",
    "depends": ["web"],
    "data": [
        "views/res_users_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "oteny_color_scheme/static/src/**/*.js",
        ],
        # The dark colours overrule the defaults of the variables, so they load
        # before the files that define those defaults.
        "web.dark_mode_variables": [
            (
                "before",
                "web/static/src/scss/primary_variables.scss",
                "oteny_color_scheme/static/src/scss/primary_variables.dark.scss",
            ),
        ],
        "web.assets_web_dark": [
            ("include", "web.dark_mode_variables"),
            (
                "before",
                "web/static/src/scss/bootstrap_overridden.scss",
                "oteny_color_scheme/static/src/scss/bootstrap_overridden.dark.scss",
            ),
            (
                "after",
                "web/static/lib/bootstrap/scss/_functions.scss",
                "oteny_color_scheme/static/src/scss/bs_functions.dark.scss",
            ),
            "oteny_color_scheme/static/src/scss/webclient.dark.scss",
        ],
        "web.assets_backend_lazy_dark": [
            ("include", "web.dark_mode_variables"),
            (
                "before",
                "web/static/src/scss/bootstrap_overridden.scss",
                "oteny_color_scheme/static/src/scss/bootstrap_overridden.dark.scss",
            ),
            (
                "after",
                "web/static/lib/bootstrap/scss/_functions.scss",
                "oteny_color_scheme/static/src/scss/bs_functions.dark.scss",
            ),
        ],
        "web.assets_unit_tests": [
            "oteny_color_scheme/static/tests/**/*",
        ],
    },
    "installable": True,
    "application": True,
}
