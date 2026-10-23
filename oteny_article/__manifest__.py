{
    "name": "Oteny Articles",
    "version": "19.0.1.12",
    "summary": "A small tree of HTML articles: prompts, rules and synced skills, without the Enterprise Knowledge app.",
    "description": """
A plain article model for Odoo Community: a name, an emoji, an HTML body and a
parent, so articles form trees. The form shows the tree of contents and a
search box in a left column, and the Articles menu opens the first article.
Code and module data own some articles (the skill sync, parsing prompts);
those are locked, so the screen shows them read-only. Replaces
knowledge.article for radar (community plan, D6).
""",
    "author": "Oteny",
    "website": "https://oteny.com",
    "license": "LGPL-3",
    "category": "Productivity",
    "depends": ["base", "html_editor"],
    "data": [
        "security/ir.model.access.csv",
        "views/oteny_article_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "oteny_article/static/src/**/*",
        ],
        "web.assets_unit_tests": [
            "oteny_article/static/tests/**/*",
        ],
    },
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
