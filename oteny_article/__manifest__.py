{
    "name": "Oteny Articles",
    "version": "19.0.1.3",
    "summary": "A small tree of HTML articles: prompts, rules and synced skills, without the Enterprise Knowledge app.",
    "description": """
A plain article model for Odoo Community: a name, an emoji, an HTML body and a
parent, so articles form trees. Code and module data own some articles (the
skill sync, parsing prompts); those are locked, so the screen shows them
read-only. Replaces knowledge.article for radar (community plan, D6).
""",
    "author": "Oteny",
    "website": "https://oteny.com",
    "license": "LGPL-3",
    "category": "Productivity",
    "depends": ["base", "mail", "html_editor"],
    "data": [
        "security/ir.model.access.csv",
        "views/oteny_article_views.xml",
    ],
    "post_init_hook": "post_init_hook",
    "installable": True,
    "application": False,
}
