---
name: oteny-odoo-access-talent
description: "Work an Odoo list and form the way a person does."
version: 0.2.0
---

# Odoo list and form

A business bot works a middle-of-the-road Odoo app through the views a
person already has. The host is `oteny.form.session` in `oteny_bot`.
The bot sees `views` / `list` / `open` / `set` / `save` / `discard`
and visible fields. It does not invent a second write path.

Contacts is the worked example. The same verbs work on any model the
bot user may open.

This Talent is the copy the bot reads. A client Talent loads it. It
does not copy this recipe.

The platform that runs you reaches this Odoo through `oteny.bot` only.
It consumes, probes and releases your work by its token (`work_consume`,
`work_probe`, `work_release`). Your engine answers behind that bridge.

## When to use

Load this skill before you browse a list or fill a form. You reach
this Odoo as an employee does: through your own login and its rights.
Do not write to its database or run its server commands; that is not
your machine.

There are two ways to reach it. Use the one that exists:

- **The project bound the connection.** Call `odoo_client` with
  `connection=<name>` on every call.
- **The owner set it up on this bot.** The file
  `~/.hermes/data/oteny-odoo-access-talent/connections/<name>.yaml`
  exists. Call this Talent's script with the same model, method and
  kwargs. It answers as `odoo_client` does:

      python3 ~/.hermes/skills/talents/oteny-odoo-access-talent/scripts/odoo_call.py \
        --connection <name> --model <model> --method <method> --kwargs '<json>'

The owner offers a connection that is not set up yet? Load
`references/bot-held-connection.md` and follow its checklist.

## Why the host holds the tab

The OWL JS client and the Python `onchange` / `web_save` in `web`
ship in the same Odoo release. A client-side Form that calls those
methods over `/json/2/` clones `odoo.tests.form.Form`. An upgrade
then breaks the bot while the human dialog still works.

`oteny_bot` holds the tab. An Odoo upgrade changes one host file.

Do not import `odoo.tests.form.Form`. That Form calls `env.clear()`
and wipes a live request cache.

## Master triage

| The job is… | Do |
| --- | --- |
| Find which views a model already has | Call `views`. Use the xmlid. Do not read `ir.ui.view`. |
| Read a list | Call `list` on that action. Search with domain and limit. |
| Open a new or existing form | Call `open`. No `res_id` = new. With `res_id` = that row. |
| Open a wizard someone already prepared | Call `open` with the `ir.actions.act_window` dict. Keep the context. |
| Change a visible field | Call `set` with that field only. Read the photo. |
| Keep the row | Call `save`. |
| Drop the handle | Call `discard`. No `write`. |
| Delete the row the person could delete | Call `unlink_record`. |
| The handle is dead | The verb returned `handle-expired`. Open the form again. |
| A field is not in the photo | Do not `set` it. Ask or stop. |
| Map the models and their relations | Owner's connection: `odoo_call.py --doc`. Never read `ir.model`. |

## Checklist — every form turn

1. Call `views` for the model if you do not already have the xmlid.
2. Call `open` (xmlid or a prepared act_window dict).
3. Read `fields`. Only those names are amendable.
4. For each change, call `set`. Read `warning`.
5. Call `save`. Read `res_id`.
6. If you must stop without a write, call `discard`.

## Verbs

Call these on `oteny.form.session` through your lane. Pass
`model='oteny.form.session'` on the tool, or `--model
oteny.form.session` to the script. Put the business model in
`kwargs` as `res_model` or `model` (`kwargs={'res_model':
'res.partner'}`). Do not put `res.partner` in the tool's own
`model` argument. That argument is the host.

The handle is the transient row id. A verb on a dead handle returns
`handle-expired`. Open the form again.

| Verb | Job |
| --- | --- |
| `views` | Given a model, return the act_windows and list/form xmlids the bot user may open. Name, `view_mode`, xmlid. No arch. |
| `list` | Open a list view. Visible scalar columns only. Domain and limit. |
| `open` | Open a form. Pass `action`, `view`, or `xmlid` (the same token), or a prepared `ir.actions.act_window` dict. The dict keeps wizard context. No `res_id` = first `onchange` (new). With `res_id` = `web_read` then a handle. |
| `set` | Overlay visible amendable fields. Run `onchange`. Return the photo. Surface `warning`. |
| `save` | `web_save`. Create or write the business row. |
| `discard` | Drop the handle. No `write`. |
| `unlink_record` | Delete when the person could delete from that view. The wire job is unlink. The method name leaves ORM vacuum of the transient row alone. |

Each verb takes these `kwargs`:

| Verb | `kwargs` |
| --- | --- |
| `views` | `{"res_model": "res.partner"}` |
| `list` | `{"xmlid": "base.action_partner_form", "domain": [["name", "ilike", "Acme"]], "limit": 20}` |
| `open` | `{"xmlid": "base.action_partner_form", "res_id": 42}`. No `res_id` = new. |
| `set` | `{"handle": 1, "values": {"city": "Amsterdam"}}` |
| `save`, `discard`, `unlink_record` | `{"handle": 1}` |

`open` / `set` / `save` return:

- `handle` (opaque)
- `model`
- `res_id`
- `description` (plain help text when the action has it)
- `fields`: visible amendable fields. Each field: `name`, `type`,
  `required`, `readonly`, `value`, `help`, and `selection` when it
  has one
- `actions`: footer methods (`save`, `discard`, object buttons the
  view already shows)

`actions` is a photo, not a verb. This host ships no button-invoke
call.

A big form can pass 50,000 characters. Then the answer comes back
cut and marked `truncated`, but `handle`, `model` and `res_id` stay
whole. Go on with `set` and `save`. Read a long field with
`search_read` instead.

Do not return the view arch. Do not return `view_state` or
`fields_spec`. Do not call `fields_get` or read `ir.ui.view`.

The same lane is also the raw pipe for reads: `search_read`,
`search_count`, `read`. A write goes through `save`, unless the client
Talent that loaded this one documents that exact write. A refused
`save` is reported to the person. It is never retried as a `write`.

## Fail-closed `set`

`set` refuses a field the photo does not list as amendable. That
covers invisible, readonly, and absent fields. It never writes one
silently.

## Contacts example

`views('res.partner')` names `base.action_partner_form`,
`base.view_partner_tree`, and `base.view_partner_form`.

List that action. Search by name. The photo has `display_name`,
`email`, and `phone`. It does not return `avatar_128` or
`application_statistics`.

Open the form with no `res_id`. Set `name`. Save. A `res.partner`
row exists.

Set `company_type` to `company`. Save. `is_company` is True on the
row. `is_company` is not in the photo. `_add_missing_fields`
injects it invisible and readonly. `set` of `is_company` errors.
So does `set` of a field the photo does not list.

Open that id. Change `email`. Save. `read` matches. `unlink_record`
removes the row. A later list search does not return it.

`child_ids` x2many is not in this first set.

Create-save sends every non-readonly snapshot field in the view
spec. Invisible defaults such as a wizard id then survive.
Edit-save still sends changed fields only.

## Hard rules

- Work this Odoo through your lane and your own login only:
  `odoo_client` when the project bound it, the script when the owner
  set it up. Never its database, never its server commands, even when
  they are reachable from your machine.
- Do not invent a second write path. A refused `save` is reported,
  never retried as a `write`.
- Never write a connection into `~/.hermes/.env`. A connection the
  owner sets up lives in its connection file.
- Never ask for, repeat or store a key in chat or in a file. The key
  arrives through the secure connect page.
- Do not `set` a field the photo does not list as amendable.
- Do not read view arch or `view_state`.
- A client Talent loads this skill. It does not copy this recipe.
