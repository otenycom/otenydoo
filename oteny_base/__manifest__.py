{
    "name": "Oteny Base",
    "version": "19.0.1.0",
    "summary": "The Oteny section under Settings › Technical, for the menus of the Oteny modules.",
    "description": """
A small module that the Oteny modules depend on. Today it holds one menu: the
section "Oteny" under Settings › Technical. A menu record belongs to one
module, so the modules that put a menu in the section depend on this one
instead of on each other.
""",
    "author": "Oteny",
    "website": "https://oteny.com",
    "license": "LGPL-3",
    "category": "Hidden",
    "depends": ["base"],
    "data": [
        "views/oteny_base_menus.xml",
    ],
    "installable": True,
    "application": False,
}
