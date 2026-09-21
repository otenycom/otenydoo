---
name: riverflow-execute-talent
description: "Run a riverflow strip the way a person does."
version: 0.1.1
---

# Riverflow execute

A bot works a riverflow card the same way a person does. It lists
the strip. It opens the same wizard. It saves with the same footer.

This skill is the execute layer. Dispatch (`bot_role`, claim token,
isolated turn) is optional. Leave `bot_role` empty and the card
still executes.

Form verbs (`open` / `set` / `save` / `discard`) live in
`oteny-odoo-access-talent`. Load that skill. Do not copy those
verbs here.

This Talent is the copy the bot reads. A client Talent loads it. It
does not copy this recipe.

## When to use

Load this skill before you press a riverflow button. Reach this Odoo
the way `oteny-odoo-access-talent` says: `odoo_client` when the project
bound the connection, its script when the owner set it up on this bot.
Name the connection on every call. You reach this Odoo as an
employee does: through your own login and its rights. Do not write to
its database or run its server commands.

## Why one strip

The form paints `transition_buttons_json`. A second list method
would drift. The form would gain a button. The bot list would not.

The pit of failure is a Talent that `search_read`s
`riverflow.transition` by name. The pit of success is: fold this
JSON into the DTO `search_read` the Talent already does.

## Master triage

| The job is… | Do |
| --- | --- |
| See the same buttons a person sees | Read `transition_buttons_json` on the card. |
| Open the wizard | Call `prepare_transition_action`. Then part 1 `open` on that dict. |
| Change a visible wizard field | Part 1 `set` on that handle. |
| Confirm | Part 1 `save`, then wizard `action_save`. |
| Cancel | Part 1 `discard`. |
| The reaper, or the bridge's `work_release` | Call `bot_claim` on the timeout exit. It is open-and-save. |
| A person clicks while a claim is live | The fence refuses them. You pass only with `riverflow_bot_caller`. |

## Checklist — a bot that must fill

1. Read `transition_buttons_json` on the card.
2. Pick the button by `index` / `caption` / `context.transition_id`.
3. Call `prepare_transition_action` on the card.
4. Call `oteny.form.session` `open` with the returned
   `ir.actions.act_window` dict. Keep the context.
5. Copy `effects` from that dict onto the photo.
6. If a field must change, `set` it. Read `warning`.
7. `save`, then `action_save` with the session's stored context.
8. If you must stop, `discard`.

## Checklist — no fields to fill

1. Read the strip.
2. Open as above.
3. `save` and `action_save` with no `set`.

## List

Read `transition_buttons_json` on the card. Each button has
`index`, `caption`, `help`, `primary`, `action`, and
`context.transition_id`.

The same compute also puts `has_visible_fields` and
`action_context_keys` on each button. The form widget ignores
those keys. A default action is `has_visible_fields` False unless
`followup_in_days` or `snooze_deadline_days` is present.

A live claim blanks the strip for a person. The bot user still
sees it. `bot_role` `claim` / `work` stay hidden from a person.
Execute does not require those roles.

Do not add `bot_list_transitions`. Do not `search_read`
`riverflow.transition` by name.

## Open

Call `prepare_transition_action` on the card. Then call
`oteny.form.session` `open` with the returned
`ir.actions.act_window` dict. Do not open an xmlid and drop the
context. `/json/2/` refuses the underscore name. Python still
calls `_prepare_transition_action`.

The prepare step refuses a from-state mismatch. It copies
`riverflow_bot_caller` into the wizard context when the caller set
it. It sets `active_model` / `active_id` / `active_ids` so the
wizard `default_get` binds the card.

The photo is the part 1 form photo. Copy `effects` from the
act_window dict onto that photo. `effects` are the parsed
`action_context` key names. They are not `transition_id`. They are
not entity `default_*` copies. A Talent can freeze a deadline to
today from those names. It does not invent a date.

No token consume on open.

## Set and act

`set` is part 1 `set` on that handle. Use it when a field must
change before OK. The email sender is the proof: a raw write of
`mail_template_id` leaves recipients stale.

Act is part 1 `save` plus the wizard `action_save`. Call
`action_save` with the session's stored context. The human fence
reads `riverflow_bot_caller` there. A flagged open that then
calls OK without that context is still refused.

When a claim token exists, `bot_claim` runs the fences first.
Then it open-and-saves with no pause. It runs `action_save`. It
does not write `state_id` raw. It does not copy deadline keys
onto `bot_claim`. The wizard `action_context` still applies.

When no token exists, skip that belt. Cancel is part 1 `discard`.

A tester who skips the look still uses `_fire_transition`
(`default_get`, `create`, `action_save`). That path stays.

## Human fence

`_bot_assert_human_transition_allowed` runs at
`prepare_transition_action` and at `action_save`. A person who
clicks while a live claim is on the card is refused. The claiming
bot passes both points when `riverflow_bot_caller` is set.

## Deadline key `set_deadline_relative`

Bare `action_context` key on the base wizard `action_save` elif
chain:

`{'from': <use_project_deadline_from value>, 'days': <int>}`

It writes those two fields only. It drops the email sender
`project_deadline` write, so `today + 1` does not survive. It
does not touch `weekend_deadline_rule`. The key is generic. The
values stay in workflow XML.

## Who calls what

| Caller | Calls |
| --- | --- |
| Person | 0 extra — the form already has the JSON |
| Reaper, or `work_release` through the bridge | 1 — `bot_claim` |
| Agent, no fields | 1 after the DTO read — open-and-act |
| Agent that must fill | 1 open, N `set`, 1 `save` |
| Forbidden | A second strip method. Talent-built `fields_spec`. |

`oteny_bot` does not import `riverflow`. `riverflow` does not
import `oteny_bot`. Glue is: prepare the action, then `open` the
dict.

## Hard rules

- Work this Odoo through the lane `oteny-odoo-access-talent` names and
  your own login only. Never its database, never its server commands.
- Do not `search_read` `riverflow.transition` by name.
- Do not write `state_id` raw. `bot_claim` is open-and-save.
- Do not copy deadline keys onto `bot_claim`.
- Do not require `bot_role` for execute.
- Load `oteny-odoo-access-talent` for form verbs. Do not copy them.
- A client Talent loads this skill. It does not copy this recipe.
