{
    "name": "Oteny Home Screen",
    "version": "19.0.1.10",
    "summary": "A start screen with every app as an icon, for Odoo Community.",
    "description": """
Odoo Community opens the first app after login and lists the apps only in a
dropdown. This module adds a home screen at /odoo/home: a grid of every app the
user can open, with its icon, and a search box that filters the apps by name.
The web client opens it when the URL names no action, and the apps button in the
top bar opens it. The order is the same for every user: the apps of the system
parameter oteny_home.first_apps first (menu xmlids, comma-separated), then the
other apps by name; the apps of oteny_home.hidden_apps are not shown.
""",
    "author": "Oteny",
    "website": "https://oteny.com",
    "license": "LGPL-3",
    "category": "Hidden",
    "depends": ["web"],
    "assets": {
        "web.assets_backend": [
            "oteny_home/static/src/**/*",
            ("remove", "oteny_home/static/src/**/*.dark.scss"),
        ],
        # Used when a module gives Odoo Community a dark mode (oteny_color_scheme).
        "web.assets_web_dark": [
            "oteny_home/static/src/**/*.dark.scss",
        ],
        "web.assets_unit_tests": [
            "oteny_home/static/tests/**/*",
        ],
    },
    "installable": True,
    "application": True,
}
