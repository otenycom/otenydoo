# Commission a pilot host-Talent bot — the fast path

This page is the fast checklist for the next pilot bot. It strips the
discovery narrative down to only the commands and the guardrails; a
consuming business keeps the full live-proof worked example, with exact
values, in its own skill bundle.

**Scope.** This is Path B: an author's own `/json/2/` account key plus
`dev_bot.ensure(...)` reuse on an existing dev-bot slot. It is not a real
customer commission. Do not use
`python -m hermeshost commission --internal`. Do not use
`provision_barney.py`. Full background on the reuse mechanism is in the
platform's own dev-bot-queue documentation.

---

## 0. Decide where the pilot lives

A pilot that needs a platform fix still on a hermeshost feature branch lives in
the **lab**, not the prod pool. A prod-pool dev bot takes its plugins from the
prod router's `dev` commit on every converge, and the 5-minute talent belt
re-converges it whenever a consuming business's ref moves, so a feature-branch
fix never sticks there. The lab router runs the feature branch. The lab
recipe, with the three account gates and the `lab` ref prefix, is the
platform's own **Commission a Path B dev bot in the lab** documentation. No
pilot stands today; a consuming business documents its own worked example.

## 1. Decide these five values next

| Token | What it is |
| --- | --- |
| `<pilot_login>` | New `res.users` login, e.g. `hr.betty`. Never an existing bot's login. |
| `<dev_slot>` | The durable slot the box already runs on, e.g. `host-talents-crmain-ries`. |
| `<discuss_channel>` | A fresh channel id. Never an existing bot's channel. |
| `<uplink_url>` / `<uplink_db>` | The tunnel host and database the slot already serves. |
| `<talent_slugs>` | The `hh.talent.source` slugs already on that slot, at a ref that exists on the remote (`dev`). |

---

## 2. Mint the pilot's own seam login

**Guardrail.** `ensure_bot` rehomes the oldest `oteny.bot` row for the
*calling* login. If the pilot authenticates as an existing bot's login,
its first live run steals that bot's `uplink_ref`. The pilot needs its
own `res.users` and its own key before the first live turn — no
exceptions, no shortcuts.

In the target database's Odoo shell (commit after each write):

1. Create `res.users` with `login=<pilot_login>`, company =
   `base.main_company`. Groups: only `riverflow.group_service_reader`
   and `riverflow.group_service_writer`. Add
   `base.group_partner_manager` only if the pilot will save a Contacts
   record. Do not add `hr.group_hr_user`,
   `oteny_bot.group_oteny_bot_operator`, or `base.group_system`.
2. Mint the key: `Key.with_user(pilot).sudo()._generate(None,
   '<pilot_login>-uplink', None)`. Scope `None` (global), no expiry.
   Write the plaintext to a 0600 file. Never echo it.
3. Probe the key with `POST /json/2/res.users/search_read` against the
   box. Expect HTTP 200 and the pilot's login back. A 401 means the
   mint failed or you hit the wrong database — fix this before step 3.

## 3. Deliver the key onto the box (Path B reuse)

Do not call `hh.tenant.set_uplink_key` — that method is system-only.
Call `dev_bot.ensure(...)` (`talents/skills/_shared/scripts/dev_bot.py`)
with:

- `dev_slot=<dev_slot>`, `force_create=False`
- `uplink_key` = the pilot's key, read from the 0600 file in memory
- `uplink_url` / `uplink_db` / `discuss_channel=<discuss_channel>`
- the same talent repo / subpath / `source_ref` already on the box

Wait for the request to reach `active`, **and** for every
`hh.talent.source.last_status` on the box to read `delivered`.
`active` alone does not mean the Talent is on the box.
`force_create=True` destroys the slot's current holder — never pass it
here.

## 4. Name the Discuss friend

Do not call `ensure_bot` or `bind_discuss_channel` while authenticated
as the pilot or as an existing bot — both call `ensure_bot` first, and
`ensure_bot` is the rehome trap from step 1. Create the `oteny.bot` row
directly, as an admin:

- `name=<pilot display name>`, `uplink_ref=<box ref>`,
  `bot_user_id=<pilot's res.users>`, `discuss_channel_id=<discuss_channel>`

Add the pilot's partner and a human operator's partner to that channel.
Do not add either to an existing bot's channel.

## 5. Confirm Discuss allow-all is on the box

Path B reuse can deliver the key without writing
`DISCUSS_ALLOW_ALL_USERS` / `GATEWAY_ALLOW_ALL_USERS` into the box's
`.env` — a known reuse gap (`dev-bot-queue.md` "Path B host-Talent
author loop"). If an isolated turn (step 5) authz-drops silently,
host-write both flags onto the `.env` and `polite_restart` the unit.
`oteny shell` is not reliable on every box (dropbear/cloudflared
inject, exit 2) — prefer host-write + unit bounce over retrying it.

## 6. Grade with isolated turns

Post each proof as one `message_post` on the pilot's channel,
`message_type='comment'`, prefixed with the adapter's isolated
sentinel `[oteny:isolated]`. Isolated posts do **not** create
`oteny.bot.session` rows — grade with `oteny traces --ref <box ref>`
on channel traffic, not with Bot Activity.

Minimum proof set, in order: `skill_view` on each host Talent slug,
one `oteny.form.session` Contacts save + unlink, one riverflow
`prepare_transition_action` execute on a disposable service. A consuming
business's own live-proof record carries the full scripts for each turn,
with the exact guardrails per turn.

---

## Footgun quick table

| Symptom | Fix |
| --- | --- |
| A bot's `uplink_ref` moved to the pilot's box | You authenticated as an existing bot's login somewhere in steps 2–4. Revert; mint a real pilot login. |
| 403 on `res.partner` create/unlink | Add `base.group_partner_manager`. `group_service_reader` alone is not enough. |
| 403 on `ir.actions.act_window` inside `views()` | Already fixed in the host Talent — `views()` reads the catalog as sudo, then filters. If you see this, the box is on a stale ref. |
| `TypeError` on `odoo_client` calls into `oteny.form.session` | Already fixed in `hh-odoo-client` — the host arg is `odoo_model`, not `model`, so it never collides with the business model name in `kwargs`. Stale plugin on the box if you still see this. |
| Isolated post never answered, no error | Check `.env` for `DISCUSS_ALLOW_ALL_USERS` / `GATEWAY_ALLOW_ALL_USERS` (step 4). |
| `oteny shell` exits 2 or hangs | Host-write + `polite_restart` instead of retrying. |

---

## Do not

- Do not run any of this against `hr.otenybot` or another live bot's
  login, key name, or Discuss channel.
- Do not pass `force_create=True` on an existing pilot's slot.
- Do not call `ensure_bot` / `bind_discuss_channel` from the pilot's or
  an existing bot's own session.
- Do not open an MFNL / postedworkers card, or Hand to a production
  bot, from a pilot proof.
- Do not skip the seam-login step to save a step.
