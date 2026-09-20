{
    "name": "Oteny Knowledge Sync",
    "version": "19.0.2.4",
    "summary": "Publish every repository's agent skills (.claude/skills) as locked Knowledge articles, one tree per root.",
    "description": """
Walks the ``.claude/skills`` folder of every configured root and mirrors it into
the Knowledge app as locked articles, one top-level tree per root, so the key
users of an Odoo read the same skills the AI agents read. Roots are configured
in Settings (one ``Label=path`` per line); the default is every addons-path
entry whose parent folder carries ``.claude/skills``. The sync runs on every
upgrade of a module that lives under a configured root.
""",
    "author": "Oteny",
    "website": "https://oteny.com",
    "license": "LGPL-3",
    "category": "Productivity",
    "depends": ["base", "mail", "knowledge"],
    "external_dependencies": {"python": ["mistune", "pyyaml"]},
    "data": [
        "views/res_config_settings_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "oteny_knowledge_sync/static/src/scss/skill_knowledge.scss",
        ],
    },
    "installable": True,
    "application": False,
}
