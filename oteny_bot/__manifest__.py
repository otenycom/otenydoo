{
    "name": "Oteny Business Bot",
    "version": "19.0.1.199",
    "depends": ["base", "mail", "web"],
    "external_dependencies": {"python": ["mistune"]},
    "author": "Oteny",
    "category": "Productivity",
    "summary": "Generic host for an Oteny business bot: talk to it over Discuss, review its activity log.",
    "description": """
The generic Odoo side of an Oteny business bot — the layer any Odoo instance installs to host a
bot it talks to over Discuss, independent of any workflow engine or domain.

An Oteny business bot runs on its own isolated machine and reaches this Odoo over a scoped
/json/2/ uplink; because it is external, it writes its activity back here so the bot's owner (an
Odoo user) can review every exchange without leaving Odoo — the same visibility an in-process
ai.agent (Wilma) has natively.

Models: oteny.bot (a bot connected to this Odoo), oteny.bot.session (one request/response
exchange + outcome), oteny.bot.turn (per-LLM-call detail). The record_activity() seam is what the
bot calls over /json/2/ to log an exchange. A session's origin is a soft (model, id) reference, so
a workflow module (riverflow) or an app module attaches its own record without this addon
depending on it.

The platform speaks to this Odoo only through this module. Its work contract is three
@api.model methods keyed by the work token of a dispatched session: work_consume (turn start,
fail-closed), work_probe (once a minute: mine / released / next_token) and work_release (a
hand-back with the run's values). An engine inherits oteny.bot and implements _work_consume,
_work_probe and _work_release; a missing engine answers ok=False. See README.md.
""",
    "data": [
        "security/oteny_bot_groups.xml",
        "security/ir.model.access.csv",
        "views/oteny_bot_views.xml",
    ],
    "assets": {
        "web.assets_backend": [
            "oteny_bot/static/src/scss/oteny_bot.scss",
        ],
    },
    "auto_install": False,
    "license": "LGPL-3",
}
