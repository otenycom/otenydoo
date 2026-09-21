<!-- Humans and addon authors. The bot loads SKILL.md, not this file. -->

# Odoo list and form — host Talent

This folder is the Talent for `oteny_bot`. A bot reads it so it can
work a standard Odoo list and form.

Delivery is a talent git path at this directory. Oteny pulls the
commit. The bot never clones.

Point an `hh.talent.source` row at:

- repo: the git that contains this `oteny_bot` module
- `repo_subpath`: `talents/oteny-odoo-access-talent`
- ref: the same branch or tag the addon is deployed from

The recipe is `SKILL.md`. Operator pages under `.claude/skills`
point here. They are not the runtime copy.

A riverflow bot also needs
`talents/riverflow-execute-talent/`. A client Talent is a
third path. That client Talent composes these skills. It does not
copy them.

## Two ways the bot reaches the Odoo

The project can bind the connection on the platform. The bot then
calls `odoo_client`. Or the owner sets the connection up on the bot
itself: the address, database and login go in a small file in the
bot's data folder, and the key arrives through the secure connect
page. The bot then calls `scripts/odoo_call.py`, which answers as
`odoo_client` does. The bot's checklist for that second way is
`references/bot-held-connection.md`.

## Give the bot its own login

For the Odoo admin who gives a bot access:

1. Create a user for the bot. Give it no password, so it cannot
   sign in to the web client. The API key is its only way in.
2. Give it the groups a person doing the same work would have, and
   no more. The bot can do nothing its login cannot, so the groups
   are the fence that holds even when the bot is misled.
3. Add "Technical Documentation" (`api_doc.group_allow_doc`) when
   the bot should map your models and their relations. That group
   opens Odoo's `/doc` pages and nothing else.
4. Never add "Access Rights" or "Administrator". Access Rights lets
   the login edit users and groups, so a misled bot could grant
   itself anything. Administrator also reads system settings by
   value, which is where secrets live.
5. Create an API key for that user with no scope. The bot's calls
   authenticate only with a key that has no scope. Hand the key over
   through the secure connect page the bot sends you, never in chat.

Odoo refuses any edit to a contact that belongs to another internal
user unless the caller may write users. A bot never holds that right,
so a staff member's own contact stays theirs to edit.

To stop the bot at once, archive its user. Every call then fails,
because Odoo resolves the user before it reads anything else.

## The bridge's work contract

The Oteny platform never calls a workflow engine. It calls three
`@api.model` methods on `oteny.bot`, each keyed by the work token the
bridge posted with the dispatch:

| Verb | When the box calls it | Answer |
| --- | --- | --- |
| `work_consume(work_token)` | Once, at the start of the isolated turn, before any model call | `{ok, reason, state}`; a not-ok answer drops the turn |
| `work_probe(work_token)` | About once a minute while the turn runs | `{ok, mine, released, reason, state, next_token}` |
| `work_release(work_token, reason, run)` | On a model-stream timeout, with the run's values | `{ok, released, reason, state}` |

An engine module answers by inheriting `oteny.bot` and implementing
`_work_consume`, `_work_probe` and `_work_release`. Each hook receives the
session's origin model, origin record id and the token. A missing engine
answers `ok: False`, so a box never runs work no engine owns. riverflow's
implementation is `riverflow/models/riverflow_oteny_bot.py`.
