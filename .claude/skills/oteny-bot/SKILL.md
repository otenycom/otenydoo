---
name: oteny-bot
sync_to_knowledge: false
description: >
  Generic Odoo host for a company's Oteny bot. Covers oteny.bot, Bot Activity
  sessions, the login-dance latch, and the derived one-live-slot operator UI
  on /odoo/oteny-bots. Use when a staff operator debugs a stuck Hand, a stuck
  sign-in dance, or silent Discuss. Agent-only (not published to Knowledge).
---

# Oteny Bot host (`oteny_bot`)

The generic Odoo host for a company's Oteny bot. The bot runs on its own
machine. It talks to this Odoo over a scoped `/json/2/` uplink. Staff review
exchanges on **Oteny Bots** without leaving Odoo.

This skill is the operator surface. The one-live-slot **gate** stays in
[riverflow](../riverflow/SKILL.md). A consuming business's own
bot-specific workflow — its scope-lock, harness, and consumption rules —
stays in that business's own skill bundle. Do not fork occupancy here.

## When to Use This Skill

- A Hand-to-Barney sticks in *With Barney* and the operator needs the occupant
- A sign-in dance latch looks stuck after a cancelled login
- Discuss is silent and staff must open the bot row, not the website login
- A second bot for a company needs dance chrome without postedworkers names
- A bot must browse a standard Odoo list or form through the views a person already has. The recipe the bot reads is the module Talent [`talents/oteny-odoo-access-talent/`](../../../talents/oteny-odoo-access-talent/SKILL.md)

## Why the operator UI lives here

The MFNL service form already tells HR that Barney is queued (`bot_queue_note`).
The Oteny Bots list is the staff map of the **bot**. A leftover *Register Login*
or a live claim is otherwise invisible until someone hunts MFNL cards.

Dequeue means **the slot is free after a fill ends**. It does not mean a
browser is available. A SLA-less *Needs Login* leftover must not paint as the
occupant. The note may still name leftover parks. The badge must not.

## Operator walk

Audience: `oteny_bot.group_oteny_bot_manager`. Not HR on the MFNL header.

1. Open `/odoo/oteny-bots`. Do not create a New bot.
2. Read the **Slot** badge on Barney.
3. Open the Barney form. The group **One live slot** repeats the badge, the
   note, the occupant service, the occupant state name, since-when, and the
   queued count.
4. **Open holding service** lands on that `riverflow.service` form. Use the
   service header to Hand back or Cancel. This screen does not fire a
   transition.
5. **Open waiting services** lists same-workflow `bot_stage == queue` rows.
   The occupant is excluded. Invisible when the count is 0.
6. **Clear login dance** is visible only while `login_dance_is_active`. It
   empties the latch. It does not call `login_dance_stop` without a token.
7. **Open Discuss** and **Exchanges** stay as they were. Watch live stays on
   the session form. Do not mint a Steel watch from this list.
8. On a Bot Activity form, **Open related record** opens the origin form
   (a `riverflow.service` in the MFNL case). Summary shows the model's
   friendly name and the record's display name. The stored pair stays
   `origin_model` + `origin_res_id`.

Reload the list after a change. There is no ticker. There is no chatter on
`oteny.bot`. `oteny_audit` already logs a force-clear write.

### Slot badge

| Key | Label | Meaning |
|---|---|---|
| `unknown` | Unknown | Compute could not read truth. Danger badge. Never Idle. |
| `idle` | Idle | No dance, no occupying login-hold, no `in_progress` claim. |
| `dance` | Signing in | `login_dance_until` is still in the future. Wins over leftover parks. |
| `live` | Live | Occupant `bot_stage == in_progress`, including *Relogin*. |
| `login_hold` | Login hold | Occupant is *Register Login* (queue + SLA). |

Compute priority: `unknown` → `dance` → live occupant → login-hold occupant →
`idle`. *Relogin* paints `live`. A SLA-less *Needs Login* never wins.

Layer 3 fills these fields only when the row is
`crewradar_cuneus_sign.oteny_bot_barney`. A non-Barney `oteny.bot` leaves
occupant fields empty. That is honest. The code does not guess another
workflow.

### Fail-closed

The list must not say Idle when the compute cannot see the truth.

- Missing workflow or a helper raise → `unknown` and “Slot status could not be read.”
- Broker / Steel must **not** be called from the compute. A 401 must not flip
  the badge to Idle.
- Prefer `login_dance_active()` for the badge. A list read must not wait on
  `login_dance_hold`.
- Session `outcome` is write-back. Paint the **service** occupant.

### What this screen must not do

No Start MFNL. No Refresh login. No Ask Barney Confirm. No `bot_role` claim
token. No Watch live. No Stop-the-run. No Release-slot that writes a fake
free state. The only honest release is the occupant's existing exit.

## Module map

| Piece | Owns |
|---|---|
| `oteny.bot` | Bot row, Discuss home channel, dance latch |
| `oteny.bot.session` | One exchange + outcome. `origin_ref` is the clickable origin. `browser_status` is not the slot. |
| `oteny.bot.turn` | Per-LLM-call detail |
| `login_dance_until` / `login_dance_user_id` / `login_dance_token` | TTL latch. Never show the token. |
| `login_dance_is_active` | Computed from wall clock |
| `login_dance_start` / `login_dance_stop` | Mint / compare-and-clear |
| `login_dance_force_clear` | Manager override. Take `login_dance_hold`, then empty the three fields. |
| `live_slot_*` | Layer 3 compute on the Barney xmlid |
| `oteny.form.session` | Transient list/form adapter. Holds `view_state` and `fields_spec`. The photo lists only visible amendable fields. |

`LOGIN_DANCE_MINUTES` is 15. An abandoned dance unlatches on wall clock.

## Bot Activity rows: one row per claim epoch, signed by OdooBot

An `oteny.bot.session` row with outcome `dispatched` paints the **Working** pill.
The dispatch opens it, and the exit of the bot in-progress state closes it
(`crewradar_cuneus_sign/models/riverflow_service_bot_activity.py`). Two rules
keep that honest.

- **One row per claim epoch.** The work token names the epoch. A re-dispatch of
  the standing token (the 3-min belt re-posting a never-consumed dispatch) reuses
  the open row. The exit closes every open row of the token. Before
  `crewradar_cuneus_sign` 19.0.6.100 the re-post opened a second row, and the
  close hook closed only the newest one. The older row then stayed **Working**
  for ever. `migrations/19.0.6.100` closed those leftovers on every tier.
- **OdooBot signs every dispatch.** `oteny.bot.dispatch_isolated_turn` posts as
  the superuser, whichever user's transaction fires it. A drain fires inside the
  bot's own uplink call (the escalate that freed the slot), and `sudo()` keeps
  that user as the author. The bot's gateway drops a message its own partner
  authored (the echo guard in hh-discuss `select_inbound`), so a dispatch signed
  by the bot is never consumed. Before `oteny_bot` 19.0.1.56 such a dispatch sat
  until the belt re-posted it as OdooBot three minutes later.

**Diagnosis of a stale Working row.** Read the row's `work_token` and search the
sibling rows with the same token. A closed sibling means the duplicate-row
fault above; the migration closes it, or close it by hand with the sibling's
outcome. No sibling and an empty `bot_run_started_at` on the origin record means
the dispatch was never consumed: read `mail.message.author_id` on the flagged
message. It must be OdooBot, never the bot's partner. Then check the bot's
`uplink_ref` and the dispatch cron (a restored database severs both).

The live proof of this mechanism, and its test-session history, is a
consuming business's own record, kept in that business's own bot skill.

## Layer split

```
oteny_bot                 no riverflow depend. Dance fields, dance chrome, form session. Depends on `web`.
riverflow                 no oteny_bot depend. Gate + occupant helpers.
crewradar_cuneus_sign     depends on both. Fills the Slot on the Barney xmlid.
```

Current manifests: `oteny_bot` **19.0.1.62**, `riverflow` **19.0.1.1242**,
`crewradar_cuneus_sign` **19.0.6.108**.

Riverflow helpers: `_bot_one_live_slot_occupant_of_workflow`,
`_bot_one_live_slot_queued_of_workflow`, `_bot_login_hold_occupies_slot`,
`_bot_drain_peers`, `_bot_resume_login_park`. Occupy and drain rules live
in [riverflow — One live slot](../riverflow/SKILL.md). Do not copy them into
`oteny_bot`. Ask consults `_bot_one_live_slot_defers` before a persist-true
preflight mint. A held slot paints `slot_queued`. Confirm queues. Do not
mint. The MFNL override of `_bot_resume_login_park` skips the login
wizard and skips when a dance is live. Why: the sibling fill already signed
the Steel jar in, and a live Open must not be stolen.

A second bot for a company inherits the gate by flagging its login-park states.
It inherits this UI when a later bridge maps that bot to its workflow.

## Host Talent Delivery

`oteny_bot` and `riverflow` are standalone modules. Any Odoo project that
installs either one is usable by a bot: the tooling the bot needs is a
Talent that lives **inside the module itself**, at
`oteny_bot/talents/oteny-odoo-access-talent/` and
`riverflow/talents/riverflow-execute-talent/`. Delivery is a Talent git
path — the platform pulls the folder from the module's own git ref onto
the box. The bot never clones and never needs `terminal` or
`execute_code`. `odoo_client` stays the RPC tool; it does not download
the Talent from an Odoo HTTP API.

**Pit of failure.** The bot-facing recipe sits only in `.claude/skills`.
An operator reads it, the running bot never sees it — or a consuming
Talent copies the host recipe, and the copy drifts from the addon.

**Pit of success.** Configure a Talent git path at the module folder. The
platform pulls that ref, so the addon and the skill the bot reads move
together.

A bot that must fill a standard form needs the `oteny_bot` bundle. A bot
that must press a riverflow button needs both. A consuming business adds
its own Talent as a third path, which **composes** the host bundles — it
does not copy their recipe.

**How a bot gets the path.** Each bundle is one `hh.talent.source` row:
same git repo, a subdirectory (`repo_subpath`), and a ref matching the
branch the addon is deployed from (`pin_mode: follow`). A second Odoo
project that vendors either module uses *that* project's own git URL and
the same `repo_subpath`. These folders are git content, not Odoo data —
do not add them to `__manifest__.py`, and do not add a controller that
serves the bundle over HTTP.

**What lives where.** The Talent (runtime) holds numbered checklists,
verb tables, worked examples, and the fail-closed rules — `odoo_client`
only, no `terminal`, no `execute_code`, no client-app facts. This
`.claude/skills` page covers how to change the addon itself; it points
at the bundle rather than duplicating the bot's own recipe.

## Form session

`oteny.form.session` is the list and form adapter: a bot browses the
views a person already has on a model, then uses list and form the way
that person does. It does not invent a second write path. A bot opens
the same `act_window` a person opens (an xmlid, or a prepared
`ir.actions.act_window` dict, so a wizard door can keep its context). It
reads a list, opens a form, sets visible fields, and saves. `set` refuses
a field the photo does not list as amendable (invisible, readonly, or
absent) — it never writes one silently. The session row holds
`view_state` and `fields_spec`; those values never go back to the model,
and the host does not import `odoo.tests.form.Form`
(`Form._perform_onchange` calls `self._env.clear()`, which would wipe a
live request cache). Create-save keeps invisible defaults and x2many ids.

The session row is transient, so Odoo's vacuum can remove an old handle.
A verb on a dead handle returns a clear handle-expired error, and the bot
re-opens.

**The `/json/2/` wire the bot sees:**

| Verb | Job |
| --- | --- |
| `views` | Given a model, return the act_windows and list/form xmlids the bot user may open. Name, `view_mode`, xmlid. No arch. |
| `list` | Open a list view. `web_search_read` with that view's `fields_spec`. Visible columns only. Domain and limit. |
| `open` | Open a form. No `res_id` = first `onchange` (new). With `res_id` = `web_read` then a handle. |
| `set` | Overlay visible fields. Run `onchange` on the stored snapshot. Return the visible photo. Surface `warning`. |
| `save` | `web_save`. Create or write the business row. |
| `discard` | Drop the handle. No `write`. |
| `unlink` | Delete when the person could delete from that view. |

`open` / `set` return a `handle` (opaque), `model`, `description` (plain
help text when the action has it), the visible amendable `fields` (each
with `name`, `type`, `required`, `readonly`, `value`, `help`, and
`selection` when it has one), and `actions` (footer methods the view
already shows). `actions` is a photo, not a verb — a generic `click`
verb waits until a Talent needs one. The adapter never returns the view
arch, `view_state`, or `fields_spec` to the model, and the agent never
calls `fields_get` or reads `ir.ui.view` directly.

`oteny_bot` depends on `base`, `mail`, and `web` (the live `onchange` and
`web_save` live in `web/models/models.py`; `BaseModel.onchange` itself
raises `NotImplementedError`). It does not depend on `riverflow`.

**Proof.** `oteny_bot/tests/test_form_session_partner.py`
(`test_form_session`, `post_install`, `-at_install`) drives the adapter
against `res.partner`: open the views, list and search, create via an
empty-`res_id` open + set + save, prove an onchange effect survives save
(`is_company` from `company_type`), refuse a `set` on an injected
readonly field or a field the photo does not list, edit, and delete.
Odoo's own twin is `test_form_create.py`'s `test_create_res_partner` and
`TestPartnerForm` in `test_res_partner.py`.

The recipe the bot reads is the module Talent
[`talents/oteny-odoo-access-talent/`](../../../talents/oteny-odoo-access-talent/SKILL.md).
Delivery is a Talent git path — see [Host Talent Delivery](#host-talent-delivery)
above. This skill is the operator surface. It is not the runtime copy.

**Commissioning a pilot bot.** Fast checklist:
[`pilot-bot-commissioning.md`](references/pilot-bot-commissioning.md),
distilled from a live proof recorded in the pilot business's own skill
bundle.

**Author footguns (found wiring up a first pilot bot).**

- A pilot bot needs its **own** seam login before its first live turn.
  `ensure_bot` calls `_canonical_same_user_bot()`, which assumes **one
  bot per seam user**: it finds the oldest active `oteny.bot` row whose
  `bot_user_id` matches the *calling* login, and a caller's harness
  typically calls `ensure_bot(uplink_ref=ref, name=ref)` on every run,
  not just on first contact. Two bots sharing one seam login means the
  first live run of the newer one **rehomes the older bot's**
  `uplink_ref` onto the newer box and deactivates the older one.
  `oteny_bot/tests/test_oteny_bot.py`
  `test_ensure_bot_rehomes_stale_same_user_uplink_ref` proves the exact
  mechanism. Mint a pilot bot's own seam login and API key before its
  first live turn; do not skip that step to save one.
- On a locked database, `group_service_reader` alone may not create or
  unlink `res.partner`; a pilot bot also needs
  `base.group_partner_manager` for a Contacts-style probe.
- `views()` reads the action/view catalog as **sudo**, then filters to
  what the user may open. Without that, a seam login hits 403 on
  `ir.actions.act_window`.
- Isolated Discuss posts do **not** create `oteny.bot.session` rows.
  Grade with `oteny traces --ref <box>` on channel traffic instead.
- `odoo_client` passes the host as its first positional arg
  (`oteny.form.session`). A business model name belongs in **kwargs** as
  `model` or `res_model` — a name collision on the positional arg raises
  `TypeError` until the calling library renames that arg (e.g. to
  `odoo_model`).

Operator pointers:
[talent-author-host.md](references/talent-author-host.md),
[talent-author-odoo-library.md](references/talent-author-odoo-library.md).
The riverflow door is
[`talents/riverflow-execute-talent/`](../../../talents/riverflow-execute-talent/SKILL.md).

## Tests

| Tag | File |
|---|---|
| `oteny_bot` | `oteny_bot/tests/test_oteny_bot.py` — dance active, force-clear, stale `login_dance_stop` no-op, manager-only, `test_oteny_bots_app_uses_brand_icon` |
| `test_form_session` | `oteny_bot/tests/test_form_session_partner.py` — Contacts list/form/set/save/refuse/edit/delete. Does not import `Form`. |
| `test_bot_one_live_slot` | `riverflow/tests/test_bot_one_live_slot.py` — occupant-of-workflow, SLA-less park is not occupant |
| `test_oteny_bot_live_slot_ui` | `crewradar_cuneus_sign/tests/test_oteny_bot_live_slot_ui.py` — idle / live / Relogin / Register Login / leftover park / dance / unknown / non-Barney |

Do not start Happypath. Do not use service `19319`.

## Idle-state visibility

Open holding service and Open waiting services stay hidden while idle and
while the queued count is 0. A consuming business's own live test-session
state (ids, holding status) is tracked in that business's own bot skill,
not here.

## Key files

- `oteny_bot/models/oteny_form_session.py` — list/form adapter. Verbs `views` / `list` / `open` / `set` / `save` / `discard` / `unlink_record`. `open` accepts an xmlid or a prepared act_window dict. Bot recipe: [`talents/oteny-odoo-access-talent/`](../../../talents/oteny-odoo-access-talent/SKILL.md)
- `talents/oteny-odoo-access-talent/` — host Talent. Git path delivery. Not Odoo data.
- `oteny_bot/models/oteny_bot.py` — dance latch, force-clear, Discuss actions
- `oteny_bot/views/oteny_bot_views.xml` — list, form, manager menu. Root `oteny_bot_menu_root` uses `web_icon="oteny_bot,static/description/icon.png"`
- `oteny_bot/static/description/icon.png` — Oteny Bots home-menu tile. Render from the official flat-top cell (`oteny-cell.svg` in the hermeshost brand mark). Points are left and right. Do not scale a PNG that already clipped those points. Do not add a large PNG margin — the home menu already pads 10 px. `test_oteny_bots_app_uses_brand_icon` checks `web_icon` / `web_icon_data`, that the mark fills most of the canvas, and that the left and right vertices stay sharp
- `crewradar_cuneus_sign/models/oteny_bot.py` — derived `live_slot_*`
- `crewradar_cuneus_sign/views/oteny_bot_live_slot_views.xml` — Slot badge + form group
- `riverflow/models/riverflow_state_bot_mixin.py` — occupy, drain, occupant-of-workflow, wizard `bot_claim`
- The one-live-run queue gate and its UI history are a consuming
  business's own record, kept in that business's own bot skill.
- Standard Odoo access (the `oteny.form.session` form/list adapter): see
  [Form session](#form-session) above.
- Host Talent delivery: see [Host Talent Delivery](#host-talent-delivery) above.
- Riverflow's execute door: [`talents/riverflow-execute-talent/`](../../../talents/riverflow-execute-talent/SKILL.md)
