{
    "name": "Oteny Business Bot",
    "version": "19.0.1.86",  # 19.0.1.80: login_dance_stop_own — the dance owner's save from a fresh dialog releases the latch.  # 19.0.1.69: Stop and Reset browser — close this bot's live cloud browser and clear its saved sign-in, gated on a jar-clear credential.  # 19.0.1.62: Bot Activity Summary shows the origin's friendly model name and clickable display name, plus Open related record.  # 19.0.1.56: OdooBot signs every isolated-turn dispatch, whichever user fires it; a drain inside the bot's own uplink call posted as the bot, and the gateway dropped its own post as an echo.  # 19.0.1.52: form session lists the catalog without action/view ACL.  # 19.0.1.51: form session accepts a prepared act_window dict; create-save keeps invisible defaults.  # 19.0.1.50: oteny.form.session list/form adapter.  # 19.0.1.44: render the official cell so the left/right points stay sharp on the Oteny Bots tile.  # 19.0.1.32: Oteny honeycomb on the Oteny Bots app tile.  # 19.0.1.31: login_dance_is_active + manager force-clear; dance chrome on the bot form.  # 19.0.1.19: OtenyBotSession._markdown_to_html — a consumer view can render response/request as sanitized HTML instead of literal ** markers.  # 19.0.1.18: drop Request expander chrome that leaked under Response.  # 19.0.1.17: full-width Summary request/response + generic Discuss home-channel action.  # 19.0.1.16: last-session helpers + Summary/Technical Bot Activity form.  # 19.0.1.15: a bot may not authorize its own room.
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
    "license": "OEEL-1",
}
