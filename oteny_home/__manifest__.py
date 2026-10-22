{
    "name": "Oteny Home Screen",
    "version": "19.0.1.1",
    "summary": "A start screen with every app as an icon, for Odoo Community.",
    "description": """
Odoo Community opens the first app after login and lists the apps only in a
dropdown. This module adds a home screen at /odoo/home: a grid of every app the
user can open, with its icon, and a search box that filters the apps by name.
The web client opens it when the URL names no action, and the apps button in the
top bar opens it.
""",
    "author": "Oteny",
    "website": "https://oteny.com",
    "license": "LGPL-3",
    "category": "Hidden",
    "depends": ["web"],
    "assets": {
        "web.assets_backend": [
            "oteny_home/static/src/**/*",
        ],
        "web.assets_unit_tests": [
            "oteny_home/static/tests/**/*",
        ],
    },
    "installable": True,
    "application": True,
}
