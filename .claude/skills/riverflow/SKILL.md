---
name: riverflow
description: Riverflow workflow engine for Odoo 19. Covers workflow concepts, state machines, transitions, services, and auto-add rules. Use when creating or modifying workflows, writing workflow XML files, or understanding the state machine architecture.
---

# Riverflow Workflow Engine

Riverflow is a workflow automation engine for Odoo 19. It provides state machines, transitions, services, and auto-add rules for managing business processes.

## When to Use This Skill

- **Developers**: Creating or modifying workflow definitions in XML
- **AI Agents**: Generating workflow XML, understanding state transitions
- **Business Analysts**: Understanding workflow architecture and state machines

## Quick Start

Workflow XML files define three main elements:

1. **Workflow** - The container (e.g., "Taxi Order", "Work Entry")
2. **States** - The stages in the workflow (e.g., "Not Started", "Ordered", "Completed")
3. **Transitions** - The allowed moves between states (e.g., "Order Now", "Cancel")

Basic structure:

```xml
<odoo noupdate="1">
    <!-- 1. Workflow definition -->
    <record id="workflow_example" model="riverflow.workflow">
        <field name="name">Example Workflow</field>
        ...
    </record>

    <!-- 2. States (define all first) -->
    <record id="state_not_started" model="riverflow.state">
        <field name="workflow_id" ref="workflow_example"/>
        <field name="name">Not Started</field>
        <field name="sequence">10</field>
    </record>
    ...

    <!-- 3. Transitions -->
    <record id="trans_to_started" model="riverflow.transition">
        <field name="from_state_id" ref="state_not_started"/>
        <field name="to_state_id" ref="state_started"/>
        ...
    </record>
</odoo>
```

## Core Concepts

### Workflows

A workflow is a state machine attached to an Odoo model. Key fields:

| Field | Purpose |
|-------|---------|
| `model_id` | The Odoo model this workflow applies to |
| `name` | Display name |
| `description` | User-facing description |
| `icon` | FontAwesome icon (e.g., `fa-taxi`, `fa-ship`) — see [Choosing the workflow icon](#choosing-the-workflow-icon) |
| `work_status` | For log entries: links to work status type |
| `has_supply_time` | Boolean flag enabling `supply_time_of_day` visibility on the Supply Order tab of services using this workflow. Set on Taxi and Train workflows. Related to service via `front_office_workflow_id.has_supply_time`. |

### Choosing the Workflow Icon

The icon renders as a small glyph before the workflow name in every service list and workflow picker. **Principle: the icon shows the ACTION the user must perform to complete the task — not the subject, and not the artifact produced.** Every workflow is attached to a service or log entry where something needs to *happen*; the glyph should answer "what do I have to go do?" at a glance. (Log-entry *status* workflows like Vacation/Sick describe a state rather than a task — there the glyph depicts the situation instead.)

Examples of the principle applied:

| Workflow | Icon | The action it depicts |
|----------|------|-----------------------|
| Arrange Work Permit | `fa-institution` | go to the Ausländerbehörde counter ("loket") to request the permit |
| Work Permit Handover | `fa-sign-in` | hand the retrieved card back in at the AB (arrow entering a doorway) |
| Arrange A1 Declaration | `fa-envelope-o` | post the signed application as a letter to the SVB |
| Arrange TFT Contract | `fa-pencil-square-o` | chase and collect the employee's signature |
| Arrange AUV Contract | `fa-handshake-o` | close the two-party placement agreement with the operator |
| Needs Crew | `fa-user-plus` | assign a crew member to the ship |
| Send Notification | `fa-bell` | fire a one-shot notification (vs. `fa-envelope` = Send Email, await reply) |
| Renew Passport | `fa-upload` | file the new scan when it arrives (notifying the agency never closes the case) |

**Ask "who performs the real-world act?"** When the employee or an authority does the actual thing (the employee renews their own passport in their home country; the AB issues the permit), the agent-side service is usually *remind / chase / collect* — icon THAT, not the document being renewed. This is how `fa-id-card-o` slipped onto Renew Passport: reasoning from the document family ("it's about an identity document") instead of the task. A passport is not even a card.

**Then ask it again when the process changes — and distinguish *an* action from the *completing* action.** Renew Passport went `fa-id-card-o` → `fa-bell-o` (19.0.2.166, "the service reminds") → `fa-upload` (19.0.2.172), because the reminding turned out to be done by the manning agency abroad. 19.0.2.173 then put a *Notify Agency* mail back in — and the icon stayed `fa-upload`, because notifying the agency never closes the service; filing the scan does. An icon justified by a step that no longer exists is as wrong as one justified by the artifact, but a workflow that mails somebody is not thereby a mail workflow. When transitions change, re-derive the glyph from whatever ends the case.

Solid/outline pairing convention: the solid glyph belongs to the generic communication workflow, the outline variant to a specific service performing that kind of action — `fa-envelope` (Send Email) vs `fa-envelope-o` (A1: post the application letter). `fa-bell` (Send Notification) currently has no outline counterpart.

Anti-patterns caught by the 2026-08-10 icon audit: `fa-id-card` on Arrange Work Permit (the card is the *outcome*, not the action), `fa-certificate` on A1 (ditto), `fa-id-card-o` on Renew Passport (it never touches the document), `fa-file-text` shared by three document workflows (says "a document is involved" — true of everything, disambiguates nothing), `fa-exchange` on No Replacement Needed (depicts the exact thing the status rules out).

Hard rules:

- **FontAwesome 4.7 only.** Odoo 19 ships FA 4.7; FA5+ class names (e.g. `fa-calendar-plus`, `fa-file-invoice-dollar`) silently render as an empty box. Verify a candidate exists with `rg '^\.fa-NAME:before' <odoo>/addons/web/static/src/libs/fontawesome/css/font-awesome.css`. `riverflow/tests/test_workflow_icons.py` guards every workflow/transition/transition-action icon on the database against that CSS.
- Avoid glyph collisions between workflows that appear in the same picker or service list (same model). Duplicates across different models are acceptable when the meaning is identical (e.g. `fa-info-circle` on both info-only workflows).
- Changing a shipped icon usually needs a post-migration: most workflow files are `noupdate="1"`, and even for files that are not, the `ir_model_data` **row** may carry `noupdate=true` from its creation context — check the row on the target DB, not the file, before trusting `-u` (see the 19.0.9.174 / 19.0.5.54 migrations for the guarded-update pattern that preserves manual overrides).

### States

States are the stages in a workflow. Key fields:

| Field | Purpose |
|-------|---------|
| `workflow_id` | Parent workflow |
| `name` | Display name |
| `sequence` | Order in statusbar (increment by 10) |
| `color_int` | Color index (0-11, see odoo-colors reference) |
| `is_end_state` | Marks workflow completion (e.g., Done, Ticket Booked, Completed, Cancelled) |
| `is_cancelled_state` | Marks cancelled items -- distinct from archived/inactive. A cancelled service is still `active=True` but excluded from business logic (e.g., Wilma ignores cancelled flights when looking up bookings) |
| `is_back_office_state` | Requires back-office handling (e.g., Registered state for taxi orders, where Finance processes the invoice) |
| `is_supply_order_delivered` | Supply order has been delivered (used for supply chain tracking) |
| `requires_onboarding_credentials` | Onboarding credential slots shown in credential plan for entities in this state |
| `counts_as_active_employment` | Marks an employee state as "actively/possibly employed" for credential-holder eligibility — an employee in such a state is a credential holder even before a contract row exists (catches onboarding crew, e.g. Cuneus DE Agreement Signed / Employed / Offboarding). A consuming business's own credential-planning skill documents its Holder Slot Lifecycle Filtering in full. |
| `show_state_in_crew_planning` | State shown as a badge on the slot label in crew planning -- set on non-normal states (e.g. not "Employed", not "Active") to alert planners |
| `hide_in_statusbar` | Don't show in status bar |
| `auto_progress_on_children_done` | When set, services in this state auto-progress to the next sequential workflow state once all active child services reach an end state. Used with `create_on_state_id` deferred children for parallel task patterns (e.g. parallel email-sending services). |
| `auto_done_children_on_enter` | Mirror of `auto_progress_on_children_done` in the opposite direction. When a service enters this state, all active non-end-state children are moved to the first non-cancelled end state of their own workflow (by sequence). Used on parent terminal states whose semantics imply child sub-tasks are also complete (e.g. AUV Done means the OPS Review-AUV child is moot). Children already in an end state are skipped (idempotent). |
| `execute_child_transitions` | Named value (`cancel`). When a service enters this state, every open child service below it executes its own transition marked `execute_on_parent_transitions` with the same value from its current state, through that transition's wizard. Set it only on a state that ends the case. See [Execute Child Transitions](#execute-child-transitions-the-parent-ends-the-case-1901321). |
| `is_owned_by_bot` | Marks a state worked by an **automated agent** (a bot like Barney), not a human. Ownership lives in the STATE, so the bot polls `is_owned_by_bot=True` for its queue and a human review filter excludes it with `is_owned_by_bot=False`. |
| `bot_login_hold` | A login-hold state. Register Login (queue + SLA) and Relogin occupy the one live slot. A SLA-less park does not occupy. Drain may resume a fresh SLA-less park via `_bot_resume_login_park`. Set this in the **workflow XML**. |

| `auto_done_children_on_enter` | Mirror of `auto_progress_on_children_done` in the opposite direction. When a service enters this state, all active non-end-state **direct** children are moved to the first non-cancelled end state of their own workflow (by sequence). Used on parent terminal states whose semantics imply child sub-tasks are also complete (e.g. AUV Done means the OPS Review-AUV child is moot). Children already in an end state are skipped (idempotent). **On a state that is also `is_cancelled_state`** the children are CANCELLED instead of completed and the **whole subtree** is walked — see [Cancel variant](#cancel-variant--the-flag-on-a-cancelled-state-1901201). |

**State flags in practice**: These flags allow business logic to query service status by meaning rather than by state name. For example, checking `state_id.is_cancelled_state` works across all workflows regardless of whether the cancelled state is named "Cancelled", "Rejected", or "Void".

**Encode bot-vs-human ownership in the STATE, not the responsible team.** When a workflow is worked by both a bot and a human, give each working stage a human-owned and a bot-owned variant (the bot-owned one flagged `is_owned_by_bot` + usually `hide_in_statusbar`), and make the hand-off transitions MOVE between them. Riverflow renders transition buttons per `from_state`, so a hand-off that only flipped `responsible_team_id` (a self-loop, same `from`/`to` state) can never hide its own button after the flip — the button stays visible and meaningless. A real state change (human-owned ↔ bot-owned) keeps the per-state buttons correct, and `responsible_team_id` stays a stable routing marker. The MFNL/Barney workflow (`crewradar_cuneus_sign/data/mfnl_workflow.xml`) is the worked example: `Not Started` ↔ `With Barney`, `Filed — HR reviewing` ↔ `Filed — awaiting confirmation`.

#### Bot Execute

**One live slot (derived).** Why: a bot that shares one browser and one login must not run two isolated turns at once. `_bot_dispatch_gate` defers when `_bot_one_live_slot_defers()` is true. The gate is generic. Which states hold the slot is a workflow XML flag (`bot_login_hold`). Strict at dispatch: any other claimed in-progress peer defers a fresh-work queue. A SLA-less login park does not occupy. Relaxed at consume: only a consumed, in-SLA peer defers (`_bot_claim_is_live`). Exclude-self: a parked login's own resume always admits. Fresh work never jumps it. Drain: `_bot_drain_peers` is the **primary** release. It runs in the occupant's own `write` transaction when that record leaves `in_progress` or `bot_login_hold`. A queued sibling wins. If none waits, drain resumes one fresh SLA-less login park (`_bot_resume_login_park`). Dequeue means the **slot is free after a fill ends**. It does not mean a browser is available. Linger reuse is how Relogin can skip a new sign-in. The mixin `_bot_resume_login_park` is a no-op. An owning app overrides it. The MFNL override writes *Needs Login* → *Register Login* (inline Relogin claim). It skips the login wizard. It skips when a dance is live. A leftover park older than `_BOT_LOGIN_PARK_FRESH_HOURS` (4) stays human-owned. The 3-min `bot_dispatch_queue` cron is the catch-up belt for queue. It does not promote parks. If the next adopt lands more than 2 min after the occupant's linger, the drain missed. The inline dispatch and the park resume each run under a savepoint, and every failure but one stays there, so a hand-off or an exit never breaks. The one exception is Odoo's own retryable class (`PG_CONCURRENCY_EXCEPTIONS_TO_RETRY`: a serialization failure, a deadlock, a lock timeout). That error propagates, because the HTTP layer rolls the whole request back and replays it up to five times. Why: the drain's channel post and the bot's own narration post bump the same `discuss_channel` row, and on test1 (2026-09-06 14:50:24 UTC) the collision was swallowed and became the belt's delay instead of a replay. Tests: `test_bot_one_live_slot` (drain and park resume propagate; a plain error still never breaks the exit) and `test_bot_harness_seam` (the hand-off write propagates). Mid-turn (`linger_until == 0` on the browser row) is not adoptable. No occupancy fields on `oteny.bot`. D238 lock mechanics stand. Which states set `bot_login_hold` stays in the owning app's workflow XML. The operator surface for the same derived slot lives on **Oteny Bots** (`/odoo/oteny-bots`), not in this engine. See [oteny-bot](../oteny-bot/SKILL.md). Helpers `_bot_one_live_slot_occupant_of_workflow` and `_bot_one_live_slot_queued_of_workflow` feed that UI. Current `riverflow` is **19.0.1.1236**.

**Button names are actions, not results.** `transition.name` is the form-header caption (lowest `sequence` = `btn-primary`). Name the action the clicker takes; prefix bot-driven transitions with the bot's display name (`Barney: …`). `_compute_transition_buttons_json` hides `bot_role` `claim` / `work` from HR. Why: those exits are the harness, not a human click. A live claim (`_bot_claim_is_live`) shows a working note plus **exactly one** button: that state's `is_bot_timeout` exit, resolved by `riverflow.state.bot_timeout_transition()`. Every other exit stays hidden, because the human fence would refuse it and a button that always errors is worse than none. The timeout exit is the exception because it is the door the reaper takes on that same record once the SLA passes, so a person taking it early makes the same judgement sooner and lands in the same state — one hand-back with two triggers, never a second meaning. A state that declares no timeout exit shows the note alone. A bot-owned `queue` / `in_progress` strip sets `no_primary`, so no blue primary sits on a wait or a fill, and the abort is never the primary either. The same JSON also carries `has_visible_fields` and `action_context_keys`. The form widget ignores those keys. A bot lists this field. It does not `search_read` `riverflow.transition` by name. Full rules + anti-patterns: [workflow-xml-guide — Transition / button naming](references/workflow-xml-guide.md#transition--button-naming).

**Execute is the wizard door.** `prepare_transition_action` (public on `/json/2/`;
the underscore method is private) returns the same `ir.actions.act_window` a person gets. A bot then calls `oteny.form.session` `open` on that dict. `bot_claim` is open-and-save with no pause. It runs `action_save`. It does not write `state_id` raw. `riverflow_bot_caller` admits the claiming bot at the human fence. A person who clicks while a claim is live is still refused. The recipe the bot reads is the module Talent [`riverflow/talents/riverflow-execute-talent/`](../../../riverflow/talents/riverflow-execute-talent/SKILL.md). Delivery is a talent git path. This skill is the operator surface. Operator pointer: [talent-author-workflow.md](references/talent-author-workflow.md).

**A transition wizard must carry the record's model** (19.0.1.1238). A wizard
resolves its records through `_workflow_model`. A service wizard opened on a
second, non-service carrier model (a consumer module's own log-entry model,
say) saw no record, built an empty in-memory service, and failed with
"Another user just updated this record". `_prepare_transition_action` now
refuses that pairing at the button and names both models. Give a second
carrier's transitions an action whose wizard applies to it, such as
`<carrier_module>.log_entry_transition_action`. A consumer module's own
generic-carrier test runs that way, and a second test pins the refusal.

**The wizard dialog title is one rule** (2026-10-03).
`riverflow.transition._wizard_dialog_title()` returns "workflow | transition",
or the transition name alone for a start transition (no from-state).
`_prepare_transition_action` uses it, and so must every wizard that reopens
itself (a warning, a retry): an `act_window` without `name` shows "Odoo" as
the dialog title. First consumer: rivercreds
`rivercreds.credential.wizard._reopen_wizard`, after an A1 Ship upload
reopened as "Odoo" on a manual-entry warning. Test:
`test_credential_wizard_round_trip` (crewradar_cuneus_sign, in radar).

**Execute vs. dispatch — two layers.** Execute (strip → open wizard →
footer) is always on: a person, a tester, and a bot share this layer,
and it never requires `bot_role`. Dispatch (`bot_role`, `is_owned_by_bot`,
the claim token, the isolated turn) is optional and layers on top — a
mailbox bot working a plain service can execute without ever touching
dispatch. Do not make execute depend on dispatch: today's hide ("a
person never sees `claim` / `work`") is a filter on the same compute,
and it fires only when a transition has those roles declared.

| Caller | Calls |
| --- | --- |
| Person | 0 extra — the form already has the JSON |
| Harness / reaper | 1 — `bot_claim` |
| Agent, no fields to fill | 1, after the DTO read — open-and-act |
| Agent that must fill fields | 1 open, N `set`, 1 `save` |
| Forbidden | A second strip method. Talent-built `fields_spec`. |

**`bot_token_check`.** Why: a filing skill must probe the claim epoch before an irreversible portal submit. `res_id` is optional. `work_token` alone resolves the record. Extra kwargs are ignored so a leftover `number` cannot 422. The Talent should still send both (`res_id` + `work_token`) in two-dispatch / filing Step 4A.

**Keep `state.name` unique inside one workflow.** The service list groups by `"<state> | <workflow>"`. `bot_work_queue` also returns `state.name` (not the xmlid) as `in_progress_state` / `expect_state_in`. Two states that share a label collapse into one list group, and a refusal or escalation can no longer name which state. A strip-friendly qualifier is fine (`Barney is filling (after login)`). Do not alias two states just to share one strip label.

### Transitions

Transitions define allowed state changes. Key fields:

| Field | Purpose |
|-------|---------|
| `name` | Button label (action-oriented; see naming rule above) |
| `from_state_id` | Source state (empty for initial) |
| `to_state_id` | Target state |
| `action_id` | Transition action to execute |
| `action_context` | Python dict literal with configuration keys for the wizard (e.g. deadline overrides) |
| `execute_on_parent_transitions` | Named value (`cancel`): this transition executes by itself when the parent service enters a state whose `execute_child_transitions` has the same value and the service is in this transition's from-state. Mark the transition that closes a service that is not started. |
| `sequence` | Button order (restart at 10 per from_state) |
| `icon` | FontAwesome icon for button — FA **4.7** classes only, same constraint as the workflow icon (guarded by `test_workflow_icons.py`; see [Choosing the workflow icon](#choosing-the-workflow-icon)) |
| `to_responsible_team_id` | Team assignment after transition |

**Footgun — a non-end transition with no `to_responsible_team_id` BLANKS the team.** The transition wizard writes `responsible_team_id = transition.to_responsible_team_id` for any **non-end-state** target (the field is editable when `to_state.is_end_state` is False; `update_write_values` in `riverflow_transition_wizard.py`). If the transition leaves `to_responsible_team_id` unset, the wizard writes `False` — silently clearing the responsible team. So **a team-using workflow must set `to_responsible_team_id` on EVERY non-end-state transition** (end-state transitions suppress the team field, so they omit it). To keep a team constant across a workflow (e.g. always Human Resources), set the same team on every non-end transition — the established pattern in `cuneus_de_employee_workflow.xml` and `mfnl_workflow.xml`. A bare direct `write({"state_id": …})` does NOT have this behavior (it only touches what you pass); the blanking is wizard-only.

### A Kanban Drop Is a Step

A state changes only through a step, because the step screen does the step's work (a contract started, a date asked, a note required). A kanban grouped by a workflow state therefore never writes `state_id` when a card is dropped in another column. The drop opens the step screen of the first transition, by sequence, from the card's state to the column's state, chosen from the transitions the record's own button strip offers (so bot claim and work steps stay hidden from a person, as on the buttons). OK runs the step and the board reloads; Discard changes nothing. When no transition leads there, a notice names the steps that exist from the card's state and the card stays. A drop on a card whose state changed since the board loaded is refused like a stale button. This holds for every board grouped by `state_id` to `riverflow.state`, so a consuming app needs no setting per board and must not set `records_draggable="false"` to protect the state (radar pipeline applicants plan, decision 127; the radar Employees board wrote the state directly before, 19.0.1.1311).

- Server: `riverflow.state.mixin.action_kanban_drop(from_state_id, to_state_id)` in `models/riverflow_state_mixin.py` returns the step screen (`_prepare_transition_action` with the chosen `transition_id`) or a `display_notification` warning.
- Browser: `static/src/patch/kanban_renderer_patch.js` patches `KanbanRenderer.sortRecordDrop`; it calls the method instead of saving the field and reloads the board when the screen closes. A drop inside one column keeps Odoo's behaviour.
- Limit: the patch keys on the field, not on the model. A model with a `state_id` to `riverflow.state` but without the mixin (`riverflow.state.record`, radar's `crewradar.plan.item`) answers a drop with a "method does not exist" error and writes nothing; neither has a kanban.
- After OK the moved card shows first in its new column until the page is refreshed, the latest move on top (19.0.1.1317; radar plan `crewradar-hr/plans/kanban-moved-card.md`, Ries: it makes it simpler to drag cards on between columns). Every step reloads the board, so `riverflowPinMovedCards` keeps `riverflowMovedIds` on the renderer (a page refresh, or leaving the board, empties it) and after each reload moves those cards to the top of their columns. A moved card the reload did not load (a long column: the radar Employed column holds 265 employees and a board loads 80 cards a page, the action's limit) is read with one `search_read` in the board's search and added on top with `group.list.addExistingRecord(id, true)`, whose count raise the patch undoes (the column already counts the card). The board then scrolls to the card (`onPatched`, `scrollIntoView` with `block`/`inline` `"nearest"`). After Discard the card did not move and is not put first; a folded column or a card outside the board's search is left alone; "Load more" shows the column's own order again until the next drop. The moved card also gets the tint of a changed record (next section).
- Tests: `tests/test_kanban_drop.py`, hoot `static/tests/kanban_drop.test.js`.

### Highlight of a Recently Changed Record (Lists and Boards)

A row or a card that a user changed a few seconds ago gets a light tint, so the user sees where the change landed.

- `riverflow.highlight.row.mixin` (`models/riverflow_highlight_row_mixin.py`) stores `user_write_date`. `create` sets it; `write` sets it when the values hold a field that is not compute-only; `riverflow.mail.thread.review.mixin` (which inherits the highlight mixin) also sets it when a chatter message changes the message summaries. The value is `cr.now()`, the start time of the save's transaction.
- `highlight_row` (not stored) is true when `user_write_date` is less than **10 seconds** old when the server reads the record; `highlight_row_type` (default `info`, a light blue) picks the colour; a model may return another type, for example `danger` for a row that needs attention. A brighter yellow was tried and dropped (Ries, 9-Oct-2026): on the dark theme it needs dark text, and every coloured text in the row with it. No timer removes the tint: it stays until the view reloads. The 10 seconds count from the start of the save, so a save that takes longer gives no tint; a Start Contract step on the radar production copy saved in 0.6 s.
- A list row gets `row-bg-{highlight_row_type}` (`static/src/patch/list_renderer_patch.js`); a kanban card gets the same class (`static/src/patch/kanban_record_patch.js`, `KanbanRecord.getRecordClasses`, 19.0.1.1317). The classes live in `static/src/scss/bg-decorations.scss` (10% of the colour; in dark mode the text stays white). A row's tint is transparent and lies over the table; a kanban card paints its own background, so for `.o_kanban_record.row-bg-*` the card keeps `$o-view-background-color` and the tint lies over it as a `background-image` gradient. Without that the tint replaced the card's background and was invisible on a dark board.
- A view shows the tint only when it loads both fields: `<field name="highlight_row" column_invisible="1"/>` in a list, `<field name="highlight_row" />` before `<templates>` in a kanban, the same for `highlight_row_type`.
- `riverflow.state.record` computes `user_write_date` from its service; a consuming app routes other subjects (radar: the log entry, the employee, the ship).

### Transition Actions

Actions executed when a transition occurs:

| Action | Purpose |
|--------|---------|
| `transition_action_default` | Simple state change |
| `transition_action_email_sender` | Open email composer with template rendering, recipient management, and attachment warning |
| `transition_action_register_service` | Register for billing |
| `transition_action_supplier_confirmed` | Mark supplier confirmation |

### Transition Action Context

The `action_context` Text field on `riverflow.transition` stores a Python dict literal that is parsed by `_prepare_action_context()` in `riverflow.transition.mixin` and merged into the wizard context. This allows per-transition configuration without custom wizard subclasses.

**Two mechanisms for passing configuration**: Keys in `action_context` are processed by two distinct mechanisms depending on their naming pattern. Understanding the distinction is essential when adding new configuration keys.

1. **Bare context keys** (e.g., `keep_service_name`, `followup_in_days`, `clear_deadline`) — These go into the wizard context WITHOUT a `default_` prefix and are picked up by explicit `self.env.context.get(...)` calls in wizard Python code (`action_save`, `update_write_values`, `default_get_using_records`). They are **context flags** that control wizard behavior but are NOT automatically set as field defaults. Adding a new bare key requires a corresponding Python code change.

2. **`default_*` prefixed keys** (e.g., `default_name_invisible`, `default_actual_start_date_invisible`) — These follow the standard Odoo pattern where `default_<field_name>` in context is automatically picked up by `BaseModel.default_get()` and set as the field's default value. No Python code change is needed — Odoo handles it natively.

**Why `default_*` is safe for wizard-only fields**: The transition mixin's `_prepare_transition_action` copies entity field values as `default_*` context keys to pre-populate the wizard form. Crucially, it only creates `default_*` keys for fields that exist on BOTH the entity model (`riverflow.service`) AND the wizard model. Wizard-only fields (like `name_invisible`, `tag_ids_invisible`, and other `*_invisible` visibility flags) exist only on the wizard — the entity-field defaults loop never touches them. This makes `default_*` keys for wizard-only fields collision-free: the value set in `action_context` is never overwritten by the mixin.

**Established pattern**: The log entry workflow (`crewradar/data/log_entry_workflow_available.xml`) uses `default_actual_start_date_invisible`, `default_actual_end_date_invisible`, and `default_planned_start_date_required` to control wizard field visibility per transition. The German Work Permit workflow (`crewradar_cuneus_sign/data/work_permit_workflow.xml`) uses `default_name_invisible: True` on all 28 transitions to hide the service name field — the name is template-set and never needs editing mid-workflow.

**Deadline overrides**: The base transition wizard's `action_save` checks four bare context keys (after `update_write_values`, so they override any wizard-set values):

| Context key | Effect | Use case |
|---|---|---|
| `clear_deadline` | Sets `use_project_deadline_from="self"`, `project_deadline=False`, `days_relative_to_project=0` — permanently removes the deadline | A waiting state with no due date. Caution: a row with no deadline stays in a team's Radar list with no signal. A1 Await Reply used this until 2026-10; it now uses `followup_in_days: 365` |
| `set_deadline_to_today` | Sets `use_project_deadline_from="self"`, `project_deadline=today`, `days_relative_to_project=0` — freezes deadline as today | A1 Done: records the completion date |
| `followup_in_days` | Pre-fills `project_deadline = today + N days` in `default_get` and makes it visible on the wizard form (labeled "Follow-up deadline" in the email sender). The user can adjust the date before confirming. In `action_save`, reads the wizard's `project_deadline` (possibly adjusted by user) and writes it to the service. Works for any transition type (email sender, default, custom wizard). For email sender transitions, overrides the hardcoded tomorrow default. A wizard with no `project_deadline` value (a custom action whose wizard has no deadline field, e.g. `crewradar_sign.fill.wizard`) gets `today + N` in `action_save`; before 2026-10 the key silently did nothing there. | Work Permit: 7-day follow-up for AB/pickup requests (weekly visit rhythm), 14-day for AT Applied skip. A1 Await Reply: 365 days, also on the fill-wizard "Regenerate and Resend" |
| `set_deadline_relative` | Dict `{'from': <use_project_deadline_from value>, 'days': <int>}`. Writes those two fields only. Drops the email sender `project_deadline` write, so `today + 1` does not survive. Does not touch `weekend_deadline_rule`. | A send that must restore a parent-relative deadline. A1 child Back and Restart: `{'from': 'root', 'days': 0}`, so a waiting child follows the application again |
| `cancel_children` | Not a deadline key, but it runs in the same place: after the state write and before the deferred children, it calls `_cascade_cancel_to_subtree()`, so every open descendant goes to its workflow's cancelled state. Done children stay Done. A state flag (`auto_done_children_on_enter`) does not fit a back step, because its target is not a cancelled state. Because the cancel runs first, a cancelled child never blocks a fresh deferred child in the same save (19.0.1.1284). | A1 Needs Correction and Back: the withdrawn application's open A1 children are cancelled; the next entry into Awaiting Issued A1 creates fresh ones |

**Email sender overrides**: The base email sender wizard's `update_write_values` checks one bare context key. The **rivercreds** module extends the wizard: `_compute_attachment_ids` reads an additional bare key for credential auto-attach — a consuming business documents this in its own rivercreds skill bundle. The **crewradar_sign** module extends the wizard: `_compute_attachment_ids` reads `chained_attachment_ids` for fill-then-email chaining — a consuming business documents this in its own crewradar-sign skill bundle.

| Context key | Effect | Use case |
|---|---|---|
| `keep_service_name` | Skips `vals["name"] = subject` — the service name is not overwritten with the email subject | Work Permit: "Arrange Work Permit at AB" should not be renamed to the German AB request email subject line |
| `email_credential_type_code` | Resolved in rivercreds to `rivercreds.credential.type` by `code`; that type is passed as `cred_type_override` into `_get_email_credential_attachment_ids()` so the composer auto-attaches that credential's documents instead of the service's `email_credential_type_id` | Work Permit: pickup request emails attach the AT Application (Kassabon) documents; AB request emails omit this key so nothing is auto-attached from credentials. Same parent service can use different transitions with different attachment behavior. |
| `chained_attachment_ids` | List of `ir.attachment` IDs injected by the fill wizard (crewradar_sign) when chaining from a fill transition to an email sender transition. The email sender's `_compute_attachment_ids` includes these alongside mail template attachments | AUV: fill wizard generates PDF, chains to email sender with the PDF auto-attached. Fill-then-Email Chaining is documented in the consuming business's own crewradar-sign skill bundle. |
| `snooze_deadline_days` | In `update_write_values`, overrides `project_deadline` to `today + N` after sending — overrides the email sender's default of tomorrow. Use for chase / reminder transitions that should "snooze" the overdue indicator while HR waits for the recipient | AUV Send Reminder: 3-day snooze after each chase |
| `snooze_deadline_cap_field` | Companion to `snooze_deadline_days`. Dotted path on the service whose Date/Datetime value caps the snoozed deadline. Missing/unresolvable paths silently fall through (snooze still applies, no cap). | AUV Send Reminder caps at `log_entry_id.start_date` — the snoozed deadline never slips past the crew change date |

**Wizard field defaults via `default_*` keys**: Any key prefixed with `default_` is picked up by Odoo's base `default_get` and set as a wizard field default (see collision-safety explanation above).

| Context key | Effect | Use case |
|---|---|---|
| `default_name_invisible` | Hides the service name field on the transition wizard form | Work Permit: service name is template-set and never needs editing during transitions |
| `default_actual_start_date_invisible` | Hides the actual start date field on the wizard form | Log entry Available workflow: actual dates not relevant for availability entries |
| `default_actual_end_date_invisible` | Hides the actual end date field on the wizard form | Log entry Available workflow: same as above |
| `default_planned_start_date_required` | Makes the planned start date required on the wizard form | Log entry Available workflow: planned date is required for availability entries |

Example XML: `<field name="action_context">{'keep_service_name': True, 'followup_in_days': 7, 'default_name_invisible': True}</field>`

**XML ID references**: Keys ending with `_id_ref` are resolved to record IDs via `_process_references()`. Example: `{'credential_type_id_ref': 'credential_type_de_at_handover_receipt'}` resolves the XML ID to a database ID. This is a general mechanism — any key with the `_id_ref` suffix is resolved, and the resulting integer ID is stored under the key without the `_ref` suffix (e.g. `credential_type_id_ref` → `credential_type_id`). Used by `crewradar_sign` for `chain_to_transition_id_ref` (fill-then-email chaining: resolves to a transition ID that the fill wizard chains to after PDF generation — documented in the consuming business's own crewradar-sign skill bundle).

#### Conditional Transition Routing (wizard rewrites the target state)

A transition has a **nominal** `to_state_id` in XML, but its wizard can rewrite the real target from runtime data. Use this when an action has a **branching outcome** (e.g. AT → Done vs FB → loop back) — model it as a single transition, **not** as separate resting states.

**Mechanism**:

1. The transition declares a routing flag in `action_context` (a bare key, e.g. `{'fb_loops_back': True}`).
2. `_prepare_action_context()` (`riverflow/models/riverflow_transition_mixin.py`) merges `action_context` into the wizard context.
3. The transition wizard overrides `update_write_values()`, reads the flag from `self.env.context`, inspects the runtime data, and **rewrites `vals["state_id"]`** to the chosen target (overriding the transition's nominal `to_state_id`). It can also adjust the deadline at the same time.

**Worked example** — the DE Work Permit "Upload Permit" transition (`crewradar_cuneus_sign`, `trans_wp_upload_permit_from_at_applied`, nominal `to_state` = Done, `action_context` `{'fb_loops_back': True}`): the upload wizard `cuneus.wp.upload.permit.wizard.update_write_values` reads the just-created credential (the harmonized Wilma path creates the AT/FB credential **before** the transition fires) and rewrites `state_id` — `de_at` → Done (deadline today), `de_fb` → AT Applied (the permit case stays open for the next bulk pickup). The non-Wilma path falls back to `self.permit_type`. The branch is factored into shared `riverflow.service` helpers (`crewradar_cuneus_sign/models/riverflow_service.py`): `_wp_upload_routing_vals(is_fb)`, `_uploaded_permit_routing_vals(credential)`, `_apply_uploaded_permit_outcome(credential)` (idempotent — also reused by the weekly bulk-upload path), `_latest_uploaded_permit()`.

**Design rule** (see [Action-with-Outcome = Transition, not State](#action-with-outcome--transition-not-state)): a transition whose outcome branches is a transition with a conditional target; do **not** model the branch as a resting state the service waits in.

### Services

Services are tasks attached to records (e.g., log entries). They have their own workflow for task management. Services inherit `riverflow.state.mixin` and `riverflow.state.record.tracker.mixin`, so they appear in the Radar view alongside their subject records.

**Indexes on `riverflow_service`** (4-Oct-2026). Two lookups run on nearly every service write and log-entry edit, so both columns are indexed: `(res_model, res_id)` as `_res_model_res_id_idx = models.Index(...)` (the auto-add dedup search, the `display_order` SQL, every "services of this record" read) and `root_id` (`index=True`; Odoo's `root_id in (...)` lookup for the stored fields that depend on the root, and the reference check on delete). Unindexed, each lookup scanned the whole table, 3–7 ms on 35k services. Measured on a prod copy together with the `wp_info_for_id` index of crewradar_cuneus_sign: five log-entry edits 3.19 s → 1.41 s, ten service deadline edits 0.232 s → 0.074 s. Note that `_order` sorts on the many2one `root_id`, so every service `search()` without an explicit `order` joins the root service. Test: `TestServiceIndexes` in `tests/test_service_tree.py`.

### Template Placement Guard Rails

Service templates have metadata fields that control where they can be instantiated, preventing users from creating services in the wrong context (e.g. log-entry-only templates on employees/ships, or child-only templates as root services).

**Why**: Services on Employee and Ship use the same template picker as log entries. Without guard rails, users see the full 40+ template catalog when adding a service — most templates are meaningless or broken outside their intended subject (mail templates reference `log_entry_id`, deadline modes depend on log entry dates, etc.).

**Three fields on `riverflow.service`** (template metadata, not copied to instances):

| Field | Type | Default | Purpose |
|-------|------|---------|---------|
| `can_be_root_service` | Boolean | True | When False, template cannot be created as a standalone root service |
| `can_be_child_service` | Boolean | True | When False, template cannot be added as a child of another service |
| `allowed_subject_model_ids` | Many2many(`ir.model`) | (empty) | Restricts which subject types (log entry, employee, ship) the template can be attached to. Empty = any subject allowed |

**Shared helper**: `_template_placement_allowed(template, res_model, is_child_add)` — evaluates placement rules for a single template. Returns True if the template is allowed in the given context. Used by both server-side validation and wizard filtering to avoid logic drift.

**Central validation**: `_check_template_placement()` runs inside `_create_service_member_from_template` — the single choke point used by the start service wizard, auto-add rules, and deferred children. Raises `UserError` with a descriptive message if placement is disallowed. Can be bypassed with `skip_service_template_placement_check` in context (for programmatic creation that intentionally breaks placement rules, e.g. Wilma AI).

**Wizard filtering**: `_add_template_start_transitions` in `riverflow.start.service.wizard` evaluates placement rules for each root template and excludes the entire subtree when the root is disallowed. No JS changes needed — the OWL component already restricts checkboxes to `indent_level === 0`, so Python-side filtering is sufficient.

**Deferred children**: `_create_deferred_children` always passes `parent_id` and a clean context with `default_res_model` from the parent service. For an ordinary `'default'`-subject child this is correct — it inherits the parent's subject. For a child that **derives its own subject** via a custom `subject_from` (e.g. the "AB Appointment" marker, `'most_fitting_log_entry'`, restricted to `crewradar.log.entry` but parented under an `hr.employee`-anchored WP root), the parent's `res_model` is only the derivation *source*, not the child's eventual subject — so `_template_placement_allowed` **skips the `allowed_subject_model_ids` gate when `is_child_add` and `subject_from != 'default'`**. Without this guard the deferred clone validated the parent's employee against the marker's log-entry allow-list and booking AB raised `'AB Appointment' can only be used on: Logbook Entry.` (regression test `TestABAppointmentMarker.test_marker_placement_allows_employee_anchored_root` in `crewradar_cuneus_sign`).

**Form visibility**: The three fields are visible in the "Template Settings" group on the service form (debug mode only).

### Subject Cascade and `subject_from`

The subject of a service (`res_model` + `res_id`) cascades from its parent
service, keyed off the `subject_from` Selection field. The base module ships
a **single** mode, `'default'`, which is parent-aware: a service **with** a
parent pulls the parent's subject (so a mid-chain change propagates to
descendants); a service with **no** parent (a root) keeps whatever subject it
was created with. Higher-layer modules extend the selection via `selection_add`
and override `_apply_custom_subject_from(mode)` to handle custom resolution
(e.g. crewradar's `'most_fitting_log_entry'` picks the parent's employee's
work log entry nearest the parent's `supply_actual_date`).

**Why one base mode, not two.** An earlier design split this into `'inherit'`
(copy the parent) and `'self'` (keep own). That was redundant: a single
parent-aware `'default'` reproduces the old riverflow behaviour exactly — a
root keeps its create-time `res_id`/`res_model`, a child cascades from its
parent — with no per-record-type default chosen in `create()`. The `'self'`
("Manual") option was never set by hand in practice, so the two were collapsed
to `'default'`. crewradar's `'most_fitting_log_entry'` is a genuinely distinct
**third** mode (custom resolution a generic cascade can't express) and stays.

**Default**: the field default is `'default'`; `create()` no longer picks a
per-record value. Root services are their own subject anchor (no parent → the
`'default'` branch keeps their own subject), so the cascade never clobbers it.

**Golden rule 1 — root anchoring.** A root service is tied to its subject,
fixed when the service is added; it cannot be re-pointed by hand. The only way
to move it is the **"Move Services to Log Entry"** wizard
(`crewradar.move.services.wizard`), which today can pick another log **entry**
(`target_log_entry_id`) but **not** an employee. *TODO:* also allow choosing an
employee where the template's `allowed_subject_model_ids` permits (see
[Template Placement Guard Rails](#template-placement-guard-rails)).

**Golden rule 2 — `most_fitting_log_entry` on appointment services.** A child
defaults to inheriting the parent's subject. Switching `subject_from` to
crewradar's `'most_fitting_log_entry'` (used on appointment services such as
"AB Appointment") instead resolves the subject to the parent employee's
best-fitting **work** log entry relative to the parent's `supply_actual_date`
(the appointment date, which is also the appointment service's deadline),
regardless of the service's or entry's status. `_find_most_fitting_log_entry`
walks a fallback chain: (1) a work entry whose `[start_date, end_date]` covers
the date → (2) the **single** work entry within a ±7-day margin
(`_APPOINT_MARGIN_DAYS`) of its `[start_date, end_date]` → (3) the next upcoming
work entry → (4) the most recent past work entry → (5) empty. The margin step
(2) exists because permit appointments are booked right around a
sign-off/sign-on: an appointment within a week of *exactly one* placement
belongs to it — e.g. one booked the day after sign-off resolves to the
just-ended placement, not a far-later upcoming one. When *both* a near-past and
a near-upcoming entry are within margin (a short gap), or *none* are, step 2 is
ambiguous and skipped, falling through to the plain next-then-past priority
(tests: `test_within_margin_picks_just_ended_placement`,
`test_both_within_margin_prefers_next`, `test_neither_within_margin_prefers_next`).
On the empty step — **or when the parent has no `supply_actual_date`
set at all** — the marker **floats** (`res_id = 0`, no subject) until OPS plans
a placement or the appointment date is filled in. A floating marker is by
design, not a bug (tested: `test_floats_when_supply_date_missing`); the open
question of *detecting* an appointment service that silently never got its date
is tracked in the consuming business's own Golden Rule & Renewal Waivers skill (§10/R6).

**Implementation**: the cascade lives in `_compute_subject` — a stored,
recursive, precomputed compute on the `res_id` and `res_model` fields with
`readonly=False` (so wizards, migrations, tests, and the `_set_resource_ref`
inverse can write through directly without needing a no-op inverse method).
Depends on `subject_from`, `parent_id.res_id`, `parent_id.res_model`.
Propagation through a chain (root → child → grandchild) is automatic via
the parent dependency — `root_id.res_id` is **not** a dependency; each
level pulls from its immediate parent.

**Critical field-shape gotcha**: `res_id` declares no literal `default=`.
A literal default (e.g. `default=0`) is applied in
`BaseModel._add_missing_default_values` **before** `_add_precomputed_values`,
which then sees `res_id` "already in vals" and skips its precompute. The
cascade silently fails — `res_id` stays at the default while `res_model`
(no literal default) cascades correctly, producing a half-set subject.
Letting precompute own the fallback (the column default 0 kicks in when
`subject_from='default'`, the service has no parent, and the caller didn't
supply `res_id`) sidesteps the collision.

**Why computed-stored on the target field, not a side-effect compute on a
separate trigger field**: an earlier iteration stored `res_id_computed` and
let its compute write `res_id`/`res_model` as side effects. That made module
upgrades very slow because populating the new column ran the side-effect
compute on every service row (~100k on production), with each cascade
writing `res_id`/`res_model`, which recursed through the tree. The current
design lets Odoo's own dependency machinery handle the cascade — populate
is per-row idempotent and only fires once. See
[coding-patterns.md — Computed-stored target vs side-effect compute](../odoo-development/references/coding-patterns.md#computed-stored-target-vs-side-effect-compute).

**Root guard**: the `'default'` branch only writes when `service.parent_id`
exists. This makes a root keep its own subject and makes orphaning safe
(clearing `parent_id` on a child preserves its subject) — a service with no
parent is never clobbered to `(False, 0)`.

**`most_fitting_log_entry` reactivity is driven from the log-entry side, not
via deep deps on the service.** crewradar's `_compute_subject` override keeps
only *cheap* extra deps (`parent_id.employee_id`, `parent_id.supply_actual_date`).
It deliberately does **not** depend on
`parent_id.employee_id.log_entry_ids.start_date/.end_date/.work_status/.active`.
Because `res_id`/`res_model` are stored, such deep o2m deps would make Odoo
mark and rewrite every service whose parent-employee owns a touched log entry,
and each recompute walks the employee's whole `log_entry_ids` o2m — a module
upgrade fans out to ~20k recomputes (3+ min) instead of ~27s. Instead, when an
employee's planning shifts, `crewradar.log.entry._compute_most_fitting_marker_trigger`
(a stored Boolean trigger, `@api.depends` on start/end/work_status/active/employee_id,
guarded by `registry.loaded` so it is a no-op during module load) searches the
*few* `most_fitting_log_entry` marker services for that employee and re-resolves
their subject synchronously (`invalidate_recordset` + `_compute_subject`).
The trigger fires on flush, i.e. at the request/commit boundary — tests that
need same-transaction reactivity call `env.flush_all()` to simulate it.

**Profiling the cascade**: a developer-mode-only server action **"Recompute
Subject (debug)"** (restricted to `base.group_no_one`) force-recomputes
`res_id`/`res_model` on the selected services via
`action_recompute_subject_debug` (`invalidate_recordset` + `add_to_compute` +
`flush_recordset`). Enable the UI profiler, select services, run the action,
inspect the trace. Use it to confirm the cascade is no longer a bottleneck
without running a full `-u`.

Cascade direction is **parent → child** (not root → child) so a mid-chain
subject break is respected: e.g. root on `hr.employee`, marker
(`'most_fitting_log_entry'`) on `crewradar.log.entry`, marker's `'default'`
descendants on `crewradar.log.entry` (from the marker, not the root).

`subject_from` is copied by `_get_template_clone_vals()` so templates marked
`'most_fitting_log_entry'` produce instances with the same mode.

### Template Cloning Extension Point (`_get_template_clone_vals`)

When `_create_service_member_from_template()` clones a template service into a concrete instance, it calls `_get_template_clone_vals(template_service)` on the new service's model. This hook returns a dict of additional field values to merge into the `create()` vals before the record is created.

**Base implementation** (in `riverflow/models/riverflow_service.py`): Returns an empty dict.

**Override pattern**: Inheriting modules override `_get_template_clone_vals()` to copy template-specific fields or compute derived values at clone time. Examples:

| Module | Copies | Additional logic |
|--------|--------|-----------------|
| `rivercreds` | `credential_type_group_id`, `email_credential_type_id` | Auto-links to matching plan item by `(log_entry_id, credential_type_group_id)` |
| `crewradar` | `is_service_with_journey` | Journey anchor flag for appointment services |
| `crewradar` (journey invoicing) | Journey-specific billing fields | Refactored from full `_create_service_member_from_template` override to hook pattern |

This hook is the proper extension point for adding template-to-instance field copying. Prefer it over overriding `_create_service_member_from_template()` directly.

**Template notes.** `_create_service_member_from_template()` also copies the template's attachments and each internal note (`mail.mt_note` comment) onto the new service, as an `auto_comment` so it stays out of the top internal notes. Since 19.0.1.1331 (radar pipeline applicants plan, decision 160) the copy is **dated at the copy, by OdooBot** (`base.partner_root`), and its first line is "From the service template". Before, it kept the template note's author, `create_uid` and date, so every new work permit service showed a note "Ries, 6-Oct-2025" that read as an old note of that person. The template note itself does not change; a template note that is wrong is fixed on the template (and, when it matters, on the open copies with a migration, as crewradar_cuneus_sign 19.0.6.245 did for the work permit template). Test: `test_template_note_copy`.

### Deferred Child Services (`create_on_state_id`)

Template children can be deferred until the parent service reaches a specific state. The `create_on_state_id` field (Many2one to `riverflow.state`) on template children controls this:

- When set, `_create_services_from_template()` skips the child during initial cloning
- When the parent transitions to the matching state, the base `action_save()` in the transition wizard automatically clones the deferred children via `_create_deferred_children()`
- Idempotent: children are not duplicated on repeated transitions to the same state

**Template link and dedup** (19.0.1.1284): every clone remembers its template
in `template_service_id` (set in `_create_service_member_from_template`, the
one helper for roots, children and deferred children). The link is the key:

- `_create_deferred_children` finds the parent's template through
  `template_service_id` first, then the auto-add rule, then the name. A parent
  renamed by hand still finds its template.
- A deferred child is skipped when the parent has a live child (active, not
  in a cancelled state) with the same template link. A child from before the
  link existed falls back to the name match.
- Why: the email sender renames a service to the email subject ("A1-verklaring
  voor …"). The name match then missed the child, so a parent that came back to
  the state cloned a second pair. Production had one such parent (17699, a Done
  Back into the A1 Send Issued A1 state).
- A cancelled child does not block. A parent that comes back after
  `cancel_children` gets fresh children.

This is a generic mechanism used by any workflow. Example: the "Arrange Work Permit" template has children (Inform Client, Arrange Transport, Review) with `create_on_state_id = AB Booked`. The service is auto-added without children at "Not Started". When the user books an AB appointment and transitions to "AB Booked", children are automatically created.

**Recursive clone**: `_create_deferred_children()` materialises the **full** descendant tree of each matched deferred template, not just one level of grandchildren. It delegates to the shared `_clone_template_children(template, parent)` helper that `_create_services_from_template()` also uses, so the initial-clone and deferred-clone paths produce identical subtrees. This is what allows "great-grandchild" placements like the Cuneus *Inform Employee Travel Plan* service — a non-deferred child of *AB Arrange Transport* (itself an immediate child of the deferred *AB Appointment* marker) — to materialise on a live service when the parent reaches AB Booked. Before this fix, the loop only cloned the immediate children of the deferred template, silently dropping anything deeper.

**Context isolation**: The transition mixin (`riverflow_transition_mixin.py` lines 35-39) copies ALL parent service fields as `default_*` context keys when opening the transition wizard (for form pre-population). This was never intended to cascade into child service creation. `_create_deferred_children()` builds a **clean context** that strips all `default_*` keys and explicitly passes only the two defaults that `_create_service_member_from_template` needs: `default_res_model` and `default_res_id` (sourced from the parent service). This prevents parent values (tags, deadlines, `is_service_with_journey`, credential links, supply prices, etc.) from leaking into children — children get their field values exclusively from the template and `_get_template_clone_vals()`.

**Test coverage**: `riverflow/tests/test_deferred_children.py::test_deferred_clone_recurses_into_great_grandchildren` pins the recursion contract — a grandchild template under a deferred child must materialise on transition.

**Mixin narrowing**: The transition mixin (`_prepare_transition_action`) now resolves the wizard model first, then only includes `default_*` keys for fields the wizard model declares. Critically, `getattr` is still called on ALL entity fields (the loop iterates every field) — this is required because reading stored computed fields triggers their compute, which the state_record tracker depends on for consistency. Only the OUTPUT (which defaults enter context) is filtered, not the INPUT (which fields are accessed).

### Auto-Progress on Children Done

When a state has `auto_progress_on_children_done = True`, the parent service automatically advances to the next sequential workflow state (by ascending sequence, skipping `hide_in_statusbar` states) once all its active child services have reached an end state (`is_end_state = True`). Both "Done" and "Not Needed" end states count.

**Mechanism**: The check runs in the transition wizard's `action_save()` after a child service transitions. `_check_parent_auto_progress()` on the child inspects the parent's state flag and siblings' end states. If the condition is met, `_auto_progress_to_next_state()` on the parent sets `state_id` to the next state and calls `_create_deferred_children()` for the new state.

**Typical pattern**: Combine `create_on_state_id` deferred children with `auto_progress_on_children_done` on the same state to create a fan-out/fan-in pattern — the state spawns parallel child tasks and auto-completes when all finish.

**Example**: The A1 workflow's "Awaiting Issued A1" state (seq 30) has two deferred children (Send A1 to Client, Send A1 to Employee), each in its own receive-and-send workflow, and `auto_progress_on_children_done = True`. When both children reach an end state, the parent auto-progresses. The next state, the old "Send Issued A1" (seq 40), is `hide_in_statusbar`, so the parent goes straight to Done (seq 50). A cancelled child is an end state too, so a parent whose children were all cancelled by hand also moves on.

### Auto-Done Children on Enter

`auto_done_children_on_enter` is the symmetric primitive: when the **parent** enters a state flagged with this boolean, all active non-end-state children are moved to the first non-cancelled end state of **their own** workflow (by sequence). Children already in any end state (Done, Not Needed, Cancelled) are skipped — idempotent.

**Mechanism**: After the wizard writes the parent's new state, `_cascade_done_to_children()` runs in `riverflow_transition_wizard.action_save()` (right after `_check_parent_auto_progress`). For each child still in a non-end state, the helper searches the child's workflow for the first state where `is_end_state=True` and `is_cancelled_state=False` ordered by sequence and writes it. The write is a plain `state_id` assignment, not a transition, so it reaches **direct children only** — a grandchild is cascaded only if its own parent also carries the flag. (The cancel variant below deliberately breaks this rule.)

**Typical pattern**: Use on a parent terminal state whose business meaning makes any open child sub-task moot. Example: AUV `state_auv_done` cascades the OPS Review-AUV child to Task `Done`. `Not Needed` and `Cancelled` deliberately do not get the flag — those parent terminal states still need the child to reflect OPS work (e.g. hotel coordination after a cancel).

**Edge cases**:

- Parent state has the flag but no children → no-op.
- Child workflow has multiple non-cancelled end states → first by `sequence` wins.
- Child already in an end state (Cancelled or another Done) → untouched.
- Inactive child → skipped.
- To re-open after auto-Done, use the child workflow's normal Re-start transition.

#### Cancel variant — the flag on a cancelled state (19.0.1.1201)

When the state entered carries `auto_done_children_on_enter` **and** `is_cancelled_state`, the cascade flips to `_cascade_cancel_to_subtree()`. Two differences, both driven by what cancelling a parent actually means for the work under it:

- **The child lands in its own workflow's CANCELLED state**, resolved by `_cancel_end_state()`: the `is_cancelled_state` end state if the workflow has one, otherwise the **LAST** end state by sequence (workflows here are sequenced "work finished" before "no work happened" — Done 30 → Not Needed 40 → Cancelled 50 — so the last one is the closest thing to a cancel on offer; Send Email therefore lands on `Not Needed`, not `Done`). Closing a taxi that was never taken as "Done" would read as work completed.
- **The FULL subtree is walked**, not just the direct children. A cancelled parent typically sits over a marker child that is already **Done** while the bookings under *that* marker are still open — an Arrange Work Permit root at AB Booked over a Done "AB Appointment" marker whose taxis are still Registered. Those bookings are exactly what has to be dropped, so an already-finished node is **descended through** instead of stopping the walk.

**First consumer**: `crewradar_cuneus_sign.state_wp_cancelled` (work permit Cancelled, 19.0.5.62). Because the AB Appointment marker and Arrange Transport are generic **Task** services, the Task workflow gained `state_task_cancelled` (hidden end state, `is_cancelled_state`) plus Cancel buttons on Not started / In Progress and a Re-start off it — it had only `Done`, so a called-off task could otherwise only be closed as work completed.

**What it does not do**: cancelling a taxi/train/hotel service records the cancellation in Radar; the booking still has to be cancelled with the supplier by hand. Say so in the cancel wizard's UI.

Tests: `riverflow/tests/test_auto_done_children.py` (cancel-variant cases at the end of the class).

### Execute Child Transitions: the Parent Ends the Case (19.0.1.1321)

A parent service whose case is over closes the child services that are not started **through their own transition** (radar pipeline applicants plan, Q162; Ries, 10-Oct-2026). Two named values, one shared Selection (`CHILD_TRANSITION_SIGNALS` in `riverflow_state.py`, first value `cancel`):

- `riverflow.state.execute_child_transitions` on the **parent's** state decides **when**. Only a state that means "the case is over" sets it. An end state that only ends the parent's own part must not: a Send Email in Done can hold a follow-up taxi, an A1 in "Application Sent" holds the A1 forwarding services, and AUV Not Needed / Cancelled keep the OPS service "Check if AUV is Received" open for the fallout. So the decision sits on the parent, not on the child.
- `riverflow.transition.execute_on_parent_transitions` on the **child's** transition decides **how**: the workflow marks its own transition that closes a service that is not started. Shipped: the Cancel transition from Not started of Task, Taxi Supply Order and Train Ticket and the Not Needed transition of Send Email (riverflow); Generate Document Cancel (crewradar_sign); Inform Employee Travel Plan Not Needed (crewradar_cuneus_sign). No riverflow state sets the parent side.

`riverflow.service._execute_child_transitions()` walks the tree below the service: an open child service with a marked transition from its current state executes it through its wizard (`_execute_transition_in_code`, opened and saved as the bot does in `_bot_claim_run_wizard`); a closed child service, or one the rule just closed, is descended through; a started one has no marked transition, so it stays open and the walk does not descend into it. The transition wizard calls it after `_cascade_done_to_children`, so a state with `auto_done_children_on_enter` keeps its cascade. **Code that writes such a state without a transition calls it itself** (radar: the weekly work permit bulk upload, `_apply_uploaded_permit_outcome`; the automatic close of holder services, `_auto_complete_holder_credential_services`).

**The context is replaced, not extended.** The rule runs inside the save of the parent's transition, whose context carries that transition's keys (`set_deadline_to_today`, `cancel_children`) and the parent's `default_*` values. `_execute_transition_in_code` builds the child's wizard from the user's `lang` / `tz` / `allowed_company_ids` plus the child transition's own context, as the browser does; extending the context would set the parent's deadline key on every child.

**Best practice (Ries, 10-Oct-2026).** A new rule where a parent service's state moves its child services uses this pair; do not add another direct write of a child's state. The older direct writes (`_cascade_done_to_children`, `_cascade_cancel_to_subtree`, `cancel_children`, and the radar computes for the HR info and Travel Leg services) are listed, each with its design question, in [roadmap.md](references/roadmap.md) ("Move the hard-coded moves of child service states").

**What it does not do**: a Back from the parent's end state does not reopen a cancelled child service; the walk is not a cascade by state write, so a transition wizard whose save check holds leaves the child open for a person.

Tests: `riverflow/tests/test_execute_child_transitions.py`; radar `crewradar_cuneus_sign/tests/test_permit_case_end_closes_child_services.py` and `test_wp_v2_routing.test_case_end_closes_the_child_services_not_started`.

### Incomplete Children Warning

When a user manually transitions a service whose current state has `auto_progress_on_children_done = True`, the service wizard may warn if active child services have not reached an end state. The intent is to prevent accidentally **bypassing auto-progress** by using a circumvent transition to a normal completion state (e.g., A1 **Sent already** → Done) while child tasks are still open — not to block **Back** or **Cancel**, which do not complete the parent via the fan-in pattern.

**When the warning appears** (all must hold):

1. The service’s current state has `auto_progress_on_children_done = True`.
2. The **selected transition’s target state** (`transition_id.to_state_id`) is an end state (`is_end_state`) **and** not a cancelled end state (`not is_cancelled_state`).
3. At least one active child service is not in an end state.

**When the warning does not appear**: Transitions to a **non–end-state** (typical **Back** flows) or to a **cancelled** end state (**Cancel**) skip the warning, even if children are incomplete. Circumvent transitions that land on a normal end state (e.g., **Sent already** → Done) still show the warning when children are incomplete.

**Mechanism** (since 19.0.1.1288, 2026-10-03): the first consumer of the [save check](#save-check-confirm-warnings-before-save). `riverflow.service.wizard._save_check()` (`riverflow/wizards/riverflow_service_wizard.py`) returns one `warning` finding (kind `incomplete_children`) when the conditions above hold. OK holds the dialog on the yellow box; the user ticks "I confirm — save with these warnings" and clicks OK again. The old `incomplete_children_warning` field, the `ignore_incomplete_children` checkbox and the `UserError` are gone. `bot_claim` answers the held case `{ok: False, reason}`, as it answered the `UserError` before.

**UX rationale**: The warning is soft — users can override it by checking the confirmation box. This handles edge cases where child services are intentionally skipped (e.g., notification already sent manually outside the system).

**Tests**: Generic behavior in `riverflow/tests/test_auto_progress.py` (manual Done with incomplete children, Back, Cancel, all children done) and the hold-and-confirm round trip in `riverflow/tests/test_save_check.py`. A1 integration for Back from Send Issued A1 in `crewradar_cuneus_sign/tests/test_a1_workflow.py`.

### Radar & State Records

The Radar is a global task overview screen that queries `riverflow.state.record` -- a denormalized model mirroring key fields from workflow-enabled records. Any model that inherits `riverflow.state.record.tracker.mixin` can appear in the Radar.

State records are created automatically when a workflow-enabled record is first accessed. The fields on the state record (name, state, workflow, responsible team, etc.) are computed from the master record via overridable `_compute_*_for_record` methods. Each master model type (service, log entry, employee, ship) adds a foreign key field on the state record and overrides these methods to source the values correctly.

The generic mixin and the `_for_record` pattern are riverflow's own; see
[Radar & State Records](references/radar-state-records.md) for the full
architecture, plus a worked example of a business wiring several master
models onto the Radar (the "which FK, which override file" table).

### Check Results on a Service (19.0.1.1294)

A check result (`riverflow.check.result`: check type, severity info/warning/error,
name) is the project's mechanism for a note that tells someone working a record
something about it. It shows as a coloured Issue tag on the record's form and in the
Radar **Issues** column of its state record, and it matches the Radar "Has Issues"
filter. A result disappears when its condition stops holding; there is no per-check
"ignore".

Services can carry check results since 2026-10-04 (Ries: the mechanism and its view
belong in riverflow; an app only produces checks):

- `riverflow.check.result.service_id`, and the base `_compute_state_record_id` routes a
  service-anchored result to the service's own state record (Radar row). An app that
  adds other anchors (log entry, employee, ship) routes those itself and calls `super()`
  for the rest.
- `riverflow.service` inherits `riverflow.check.results.mixin` and has a stored
  `check_result_ids` (`models/riverflow_service_check_results.py`), shown as tags at the
  top of the service form, hidden when empty.
- An app produces checks through three hooks: `_get_services_with_checks` (the services it
  claims — the compute does nothing, and never searches, for a service no producer
  claims), `_get_service_check_types` (the types the sync owns; it never touches another
  compute's rows) and `_get_service_check_results` (the dicts, with `service_id`).
- Base trigger: `active` and `state_id.is_end_state`. Keep a producer's triggers narrow.
  In radar, a trigger on the employee's credentials broke the plan-item link of
  auto-added services, and one on the credential plan items broke the daily auto-close
  job ("Could not find all values of rivercreds.plan.item to flush"); the first producer
  therefore recomputes in its daily job instead.
- The compute skips while the registry loads; a migration calls
  `_compute_check_result_ids_impl()` directly.
- First producer: `crewradar_creds` `placement_already_covered` (One per Placement).
  Tests: `tests/test_service_check_results.py` (hooks patched).

### Tombstones: Retire Instead of Delete in a Compute (19.0.1.1299)

`riverflow.tombstone.mixin` (`models/riverflow_tombstone_mixin.py`) is the one
way a compute removes records it no longer needs (radar plan
`tombstones-instead-of-deletes-in-computes`, Ries 2026-10-08). `unlink()` inside
a compute runs every pending compute and clears the cache; that left a stale
"No open Arrange Work Permit service" warning and caused "Record does not exist"
errors. `records._retire()` flags `to_be_deleted`, archives (`active` False),
clears the stored set-null links, retires the cascade children on the mixin and
registers one pre-commit purge; `_gc_tombstones` (autovacuum) is the fallback.
The rules for readers (filter the flag with `active_test=False`, `any` instead
of `!= False`, `AND NOT to_be_deleted` in SQL, depend on `.active`) are in
`odoo-development/references/coding-patterns.md`, "Do Not Unlink Inside a
Compute That a Read Can Trigger".

- **Check results** (`riverflow.check.result`) use it: `_sync_check_results`
  skips flagged rows (a caller in an `active_test=False` env finds them) and
  retires instead of `unlink()`; a retired row is never revived. Every sync
  scope search filters `("to_be_deleted", "=", False)`.
- **Services** (`riverflow.service`) use it for the services a compute removes
  (radar: the info service of a cancelled supply order, the HR info service of
  an AB appointment). Users keep archiving services: `active` stays writable
  (`readonly=False`), the manual-archive guard in `write` skips a retire, the
  unlink guards check only unflagged rows (the purge deletes what a compute
  removed), and the purge deletes flagged rows only. `display_order` and the
  auto-add dedup leave retired services out; `is_open` follows `active`, so the
  partial unique "one open service" index never counts a retired one.
- Tests: `tests/test_tombstone_mixin.py` (hide at once, no cache clear, one
  purge step, a second round when the purge's flush retires more, a
  user-archived row survives).

### Service Deadlines

Service deadlines are computed from a `project_deadline` plus an optional `days_relative_to_project` offset, controlled by `use_project_deadline_from`:

| `use_project_deadline_from` | Behavior |
|---|---|
| `self` | Deadline is the `project_deadline` directly (manual/calendar-set date) |
| `credential_appointment` | Deadline is the appointment date of the task's newest active credential that has one (`application_appointment_date`; radar: the AT Appointment Book AB records), today when none (rivercreds extension, 19.0.3.318). The work permit's AB Booked state follows it. |
| `root` | Deadline is computed from the root (top-level) service's deadline + offset |
| `root_appointment` | Deadline is computed from the root service's `supply_actual_date` (appointment date) + offset. Falls back to root's deadline when no appointment date is set. Used by child services that must track a fixed appointment date even as the root's deadline changes during workflow progression (e.g., AB Appointment under Arrange Work Permit). |
| `creation` | Template-only: at clone time, `_create_service_member_from_template` materializes `project_deadline = today + days_relative_to_project` and stores the cloned service in `"self"` mode with a concrete date. The template itself keeps no deadline. Used by employee/ship auto-add templates (e.g., `days_relative_to_project=1` gives a next-day deadline). |
| `log_entry_start` | Deadline is computed from the log entry's start date + offset (crewradar extension) |
| `log_entry_end` | Deadline is computed from the log entry's end date + offset (crewradar extension) |
| `credential_renewal_marker` | Deadline tracks the linked credential plan slot's `renewal_action_date` — the renewal point (`expiry − renew_before_expiry_days`), left **unclamped** (not clipped to the plan window, unlike the timeline's `renewal_marker_date`), so a permit valid past the forward horizon still dates the task at its true future renewal point instead of degrading to today. Falls back to today when the slot has no renewal point (permit missing / initial; crewradar_creds then anchors that case at a future contract start). Used by the DE Work Permit auto-add so the renewal task is dated at the start of the credential's renewal window (rivercreds extension). |

When `use_project_deadline_from != "self"`, the field `is_days_relative_to_project_applicable` is True and the deadline is computed automatically. Calendar drag-and-drop triggers `_inverse_deadline` which switches to `"self"` mode, preserving the manually chosen date.

**Relative-timing badge prefix**: Each computed mode has a human-readable prefix shown in the service's relative-timing badge (e.g. `Start-30d`, `Appointment+0d`, `Renewal marker+0d`), produced by `relative_to_project_days_prefix()`. Labels: `root` → "Top-level service", `root_appointment` → "Appointment" (riverflow base); `log_entry_start` → "Start", `log_entry_end` → "End" (crewradar); `credential_renewal_marker` → "Renewal marker" (rivercreds). Each module overrides the method for the modes it adds and falls back to `super()` (MRO: crewradar → rivercreds → riverflow). **When adding a new mode, add its label here too** — an unhandled mode renders as `(unknown: use_project_deadline_from)`.

### Deadline on Entry: the State Decides (19.0.1.1316)

**Why.** The deadline used to belong to the task alone. A plain step (Back,
Restart, Resend, "Sent already") carried over whatever date the task had, and a
task added by hand on a start step kept the start screen's fixed date (the field
default `self`) for good. In radar a work permit renewal task then sat for months
on a date that no longer followed its permit (task 32423, Andres; radar pipeline
applicants plan, release R5c). Ries, 9-Oct-2026: "the rule and date is driven by
the transition screens, that should be the leading principle"; the rule sits on
the state as the default, and the steps align with it unless they explicitly
override.

**The setting.** `riverflow.state.deadline_on_entry`:

| Value | A task entering the state gets |
|---|---|
| `keep` (default) | its own date, as before |
| `rule` | `use_project_deadline_from = deadline_rule_from`, `days_relative_to_project = deadline_days` |
| `followup` | a fixed date `today + deadline_days` |
| `today` | a fixed date today |
| `clear` | no deadline |

`deadline_rule_from` offers the task's Deadline From values except `self` and
`creation` (a selection method mirrors `riverflow.service.use_project_deadline_from`,
so modes added by rivercreds or crewradar appear too).

**Who wins.** In `riverflow.transition.wizard.action_save`, after
`update_write_values`:

1. A step key (`clear_deadline`, `set_deadline_to_today`, `followup_in_days`,
   `set_deadline_relative`) wins.
2. A screen with its own date logic wins: it hides the generic deadline fields
   (`use_project_deadline_from_invisible` and `project_deadline_invisible`) and
   writes its own date (an appointment, a ticket, an e-mail follow-up).
3. On a screen that shows the generic fields, a value the user changed wins. A value
   equal to the task's own value or to the state's proposal is no choice.
4. Otherwise the state's setting applies (`_apply_state_deadline_setting`), also on
   a start step that makes the task.

The task step screen (`riverflow.service.wizard`) proposes the state's setting, so
it shows the deadline the task will get. A date typed or dragged on the task itself
still turns it to `self` (the manual override stays) and holds until the next step
into a state with a setting.

**Setting a state.** Give a state a rule only when every template that starts in
it carries that same rule; a workflow whose templates start in one state with
different rules (radar: Send Email, Task, Flight Booking, Journey Invoicing) keeps
`keep`. States are usually noupdate data, so a new setting needs the XML (fresh
install) and a post-migrate (existing databases). For open tasks already on a fixed
date, `riverflow.state._put_fixed_open_tasks_on_rule()` puts them on the state's
rule with a note that names the old date (rule settings only; a fixed-date setting
would move every task to the day of the migration). **A fixed date can be on purpose:** a
person who typed it chose it (radar decision 161, 10-Oct-2026: HR decides when a
crewman can visit the AB, so a typed work permit date stays). So a migration passes
`only=<the services whose fixed date came by accident>`; the method then changes
only those, and only while they are still open, in that state and on Self. Without
`only` it resets every such task of the state (19.0.1.1332).

Tests: `riverflow/tests/test_state_deadline_on_entry.py`.

### Weekend Deadline Rule

Dispatchers don't work weekends. The system automatically shifts deadlines that fall on Saturday or Sunday.

Each service has a **"Weekend"** field with three options:

- **Allow weekend** -- deadline stays on Saturday/Sunday (default for manually set dates)
- **Bring to Friday** -- deadline shifts to the preceding Friday
- **Delay to Monday** -- deadline shifts to the following Monday

**What gets shifted:**

- Computed deadlines from the log entry or parent service (e.g. Start -3d, End +1d)
- Example: "Book Flight" deadline falls on Sat 21 Mar -> becomes Fri 20 Mar

**What does not get shifted:**

- Manually set dates (via calendar drag or wizard) -- if you choose a date, it stays
- Appointment dates like AB, SAB, Doctor and Matroos examen -- the appointment is on that day
- Taxi, Train and Hotel -- added manually with a specific travel date

**Which templates get which rule:** this is a per-template assignment made by the
consumer module's own service templates, not a riverflow fact. A consuming
business's own services-and-workflows inventory records the current
assignment for its templates.

**Where to find it:**

- The "Weekend" field is only visible on the service form when the deadline is computed (not for manual dates)
- You can adjust the setting per service if needed

**Technical details**: The `weekend_deadline_rule` field on `riverflow.service` only applies in the `is_days_relative_to_project_applicable` branch of `_compute_deadline`. When a dispatcher sets a date via calendar drag or the service wizard, `use_project_deadline_from` switches to `"self"` and the rule has no effect. The rule is copied from template to service in `_create_service_member_from_template()`. Templates have the rule set based on a structural convention: `days_relative_to_project <= 0` -> `friday_before`, `> 0` -> `monday_after`, `use_project_deadline_from = "self"` -> `allow`.

### Bulk Deadline Update

When many services share the same deadline change -- for example, a batch of A1 applications whose response date has shifted -- the **Update Deadline** action lets you select multiple services and set (or clear) the deadline in one go, instead of editing each service individually.

**How to use it:**

1. Open the **Services** list or the **Radar** list
2. Select the services (or radar rows) you want to update
3. Open the action (cog) menu and choose **Update Deadline**
4. Enter the new deadline date, or check **Clear Deadline** to remove the deadline
5. Click **Update Deadline** to apply

When invoked from the Radar, the wizard resolves the selected state records to their underlying services automatically. Subject-only rows (log entry rows without a service) are filtered out.

Setting a deadline through this wizard switches each service to manual deadline mode (`use_project_deadline_from = "self"`), the same as a calendar drag-and-drop. Clearing the deadline sets `project_deadline` to empty.

**Technical reference**: The wizard model is `crewradar.service.deadline.wizard` (TransientModel in `crewradar/wizards/crewradar_service_deadline_wizard.py`). Server actions bind to both `riverflow.service` and `riverflow.state.record` list views.

### Radar View Shortcuts

The radar screen (list and calendar views of `riverflow.state.record`) supports a shortcut banner — a row of quick-access buttons above the view that activate saved Favorites and optionally switch between list and calendar views. This is powered by the `oteny_shortcut` module.

- No view opt-in is needed: oteny_shortcut renders the banner from Odoo's `Layout` on every multi-record view (since oteny_shortcut 19.0.1.220; the earlier `js_class="shortcut_list"` and the `ShortcutCalendarController` parent are gone, `StateRecordCalendarController` extends Odoo's `CalendarController`)
- Shortcuts are configured on `ir.filters` records by setting `shortcut_sequence > 0` (placement Views or Both)

See [oteny-shortcut](../oteny-shortcut/SKILL.md) for full details on configuring shortcuts.

### Auto-Add Rules

Rules that automatically create services when conditions are met. Auto-add rules are evaluated by `auto_add_services()` on the `riverflow.auto.add.service` model.

```xml
<record id="auto_add_check_permits" model="riverflow.auto.add.service">
    <field name="name">Check Permits</field>
    <field name="domain">[('work_status', '=', 'work')]</field>
    <field name="service_workflow_id" ref="workflow_check_permits"/>
</record>
```

**Named domains** (`riverflow.named.domain`, Configuration > Named Domains): an auto-add rule does not carry its own domain; its `domain_id` points to a named, reusable domain record (name, description, `applies_to_model_id`, `domain`). Other modules point to the same table for their own conditions — rivercreds for the applicability of a credential type group, crewradar for the extra condition of a salary auto-add component — so one domain can serve several users, and its form lists them (auto-add rules here, credential type groups added by rivercreds). Editing a shared domain changes every user. The model was `riverflow.auto.add.domain` until 19.0.1.1274 (Thijs 2026-09-28); that version's pre-migrate renamed it in place — table, sequence, constraints, indexes, every model-name reference including `ir_model_data.model` of all modules, and riverflow's own XML ids (an old-named field XML id would make `_process_end` drop the column). Odoo 19 has no model-rename helper and the upgrade-util library is not installed, so a rename is plain SQL in a pre-migrate.

**Dedup mechanism**: The dedup key is `(auto_add_rule_id, res_model, res_id, auto_add_context_ref)`. When `auto_add_services()` evaluates a rule for a record, it checks if a service already exists with that combination. If so, the service is not re-created. The `auto_add_context_ref` field (Char, default False) allows the same rule to create multiple services for different occasions (e.g., initial missing credential vs. renewal of a specific credential instance).

**Why the key carries the candidate's own `res_model`**: existing services are looked up per **candidate** `res_model`, not per trigger model. A `_filter_auto_add_candidates` hook may have re-anchored a candidate onto a different subject than the model the rule fired on (credential-subject rules anchor their service on the credential's holder — see the hook table below), so searching only the trigger model would never find the already-created service, and every re-evaluation would create another one. `_filter_single_open` groups its own lookup the same way, for the same reason.

**Single-open enforcement** (opt-in per workflow via `enforce_single_open` on `riverflow.workflow`): for an enforcing workflow, `auto_add_services()` additionally drops any candidate whose subject already has an **open** service for that workflow (and de-dups candidates within the batch) via `_filter_single_open`. "Open" is `is_open` — a stored computed boolean on `riverflow.service` (`active AND not template AND state not is_end_state`). The guard is keyed on open-ness, not the occasion key, so a renewal never stacks a second open service and a manually-created open service equally blocks auto-creation. The hard backstop is riverflow's own **unique index** `riverflow_service_single_open_uniq` on `(workflow_id, res_model, res_id) WHERE is_open AND workflow_enforce_single_open` (19.0.1.1302; `workflow_enforce_single_open` is a stored related of the workflow flag, because a partial index can only read columns of its own table). It covers every workflow with the flag and every subject model, so no consumer keeps a hand-written copy with a workflow id inside; turning the flag on while two open services share a subject fails at the database. The flag is on the workflow form. On an enforcing workflow the occasion-keyed dedup also ignores **closed** services (end state or archived): a Done task for the current occasion never blocks a fresh monitoring task, because the presence side of the invariant says an in-scope subject always holds one open service (decided 10-Sep-2026 after a Renew Passport re-upload left an employee with no task). Non-enforcing workflows keep the full dedup, so a hand-in service keyed on a specific card does not return when its lifecycle state is re-entered. **Archive heals like delete (19.0.1.1302).** A person who archives (`active=False`, not a retire) or deletes the **open** service of a single-open workflow gets it back at once: `riverflow.service.write` / `unlink` read the `(workflow, subject)` keys before the removal (`_single_open_heal_keys`) and run `auto_add_services` for those subjects afterwards (`_heal_single_open_removal`); the auto-add's own rules and filters decide whether the subject still needs it. The user then gets a sticky `simple_notification` (`_notify_single_open_heal`): "<task> comes back: <subject> needs one open task on this workflow. To stop it, use <steps>." The steps are the active transitions from the new service's state into an end state that `is_cancelled_state` (a step into a normal end state records the work as done, and the next occasion follows); without one it says "close it through its workflow". The block is unchanged and symmetric: `_single_open_removal_blocked()` refuses both an archive and a delete of a service past its initial state or with a live sub-service. `bypass_user_unlink_check` and a retire (`to_be_deleted`) skip guard and heal. Why: on 16-Sep-2026 HR archived two renewal tasks that looked like noise; a delete had always come back (an app re-ran the auto-add), an archive did not, and the two people had no renewal task for three weeks (radar pipeline applicants plan, decision 89).

**Clean context (19.0.1.1302).** `auto_add_services` creates its services from `clean_context(self.env.context)` plus the rule's own defaults (`_get_service_creation_context`). It often runs inside the save of a step screen, whose context holds the open service's values as `default_*` keys (`riverflow.transition.mixin` copies every field the wizard declares); a new service took each value its template clone does not set — the due date above all — and a value given at create never runs the compute (radar service 37725: the next work permit task copied the date of the task whose step uploaded the permit; decision 87). The start-service wizard keeps only `default_res_model`, `default_res_id`, `default_parent_id`, `default_is_this_a_template` and passes the typed date as the `deadline` argument, which `_create_service_member_from_template` writes on the started service only; before, every sub-service took the typed date too. `_create_deferred_children` already cleaned its context. Rule for new code: a service created inside a step builds its context from `clean_context` and names the defaults it means.

First consumer: the Cuneus DE Work Permit "golden rule" — exactly one open work-permit service per employee, documented as the Single-open invariant in the consuming business's own credential-planning skill.

**Trigger pattern**: Models that use auto-add services implement a stored computed field (e.g., `auto_add_trigger`) with `@api.depends("state_id")`. When the state changes, the ORM recomputes the field, and the compute method calls `auto_add_services()` as a side effect. The `flush_recordset()` call in the base transition wizard ensures this recomputation happens immediately after the state write, before `create_related_records()` runs.

**Auto-add only ever CREATES. It never removes and never retries.** Two consequences, and both have bitten:

1. **Every field a rule's domain reads must also be in the trigger's `@api.depends`.** Otherwise the rule is evaluated once and never again: a record created with the field empty raises no service, someone fills the field the next day, and no service ever appears. Nothing reports the absence. `crewradar.log.entry._compute_service_ids` gained `employee_nationality_code` for exactly this reason. The same convention is written down for credential planning in the consuming business's own skill bundle, *"when adding a new domain that uses a common stored field, add the corresponding depends path"*.
2. **Narrowing a rule is one-directional.** Widening it raises the new services immediately. Narrowing it retires nothing already raised, so the old services stay and a human must close them.

**Test the domain STRING, not only the behaviour.** A domain lives in a `noupdate="1"` data file, so it is easy to change and hard to notice: a test that only checks *behaviour* can miss a leaf silently added to the domain, because the rule still fires for every case the test happens to cover. Assert the domain string itself, not only its effect on a sample record. For a worked incident where an untested domain string cost twelve days before anyone noticed, see
a worked incident is documented in the consuming business's own bot skill. Do the same for any rule whose *absence* of a service is a business risk.

**A domain edit needs a migration, always.** `riverflow_auto_add_service_data.xml` is `<odoo noupdate="1">`, so an XML-only change is silently dropped on every database that already holds the record — the "AUV gotcha". Pair the XML edit (for fresh installs) with a post-migrate that force-writes the record (see [xml-data-noupdate](../odoo-development/references/xml-data-noupdate.md), and `crewradar/migrations/19.0.10.12/post-migrate.py` for a worked example that also re-points the rule and rewrites a pinned chatter note).

#### Credential-Subject Rules (fire on create, not on state change)

A rule whose `applies_to_model` is `rivercreds.credential` cannot use the state-change trigger above: credentials have no workflow states. The equivalent of "fire on state entry" is **fire on create** — `rivercreds.credential.auto_add_trigger` is a stored Boolean compute (`@api.depends("credential_type_id", "holder_employee_id", "expiry_date", "active")`) whose side effect calls `auto_add_services(self)`. It runs at create and re-runs when the fields that make a rule domain match land later (LLM metadata fill, holder binding); the occasion-keyed dedup makes those re-fires idempotent.

Guards in `_compute_auto_add_trigger` (`rivercreds/models/rivercreds_credential.py`):

- Assign-only while `env.registry.loaded` is False — this also makes the one-time stored-column backfill on `-u` a no-op, so existing credentials never storm services at upgrade.
- `api.NewId` records are skipped.
- The `skip_credential_subject_auto_add_trigger` context key suppresses evaluation entirely; the NAS bulk import sets it, because historical digitization must never spawn services. Deliberately **not** gated on `skip_credential_auto_add_trigger` — that key protects the in-flight service during credential-wizard saves (Upload Permit), and those uploads are exactly the ones that must spawn a service for the new card.

Since the service must hang off a subject the radar can resolve, these candidates are re-anchored onto the credential's **holder** by `_filter_auto_add_candidates` before dedup (see the hook table). When the rule also carries `credential_type_group_id`, the redirect additionally drops any credential that is not the holder's **newest** active credential of that group (`_credential_group_siblings` + `_newest_credential_of_group`, sorted expiry → issue date → id, missing dates lowest) — the firewall against late digitization of historical documents.

#### `credential_candidate_mode` (employee-subject credential rules)

`credential_candidate_mode` on `riverflow.auto.add.service` (added by rivercreds) selects how the rule's `credential_type_group_id` is interpreted for **`hr.employee`**-subject rules. It has no effect on log-entry or credential-subject rules.

| Mode | Meaning | Occasion ref (`auto_add_context_ref`) |
|------|---------|----------------------------------------|
| `covered` (default, "Skip when covered") | Renewal protocol: the candidate survives only while the employee has a holder plan item for the group that is `missing` or `issued` with a renewal horizon (`expiring_from_date`); it is suppressed by `waive_renewal` on the current permit and by the employment-end layer. Single-open workflows fall through and always emit one monitoring candidate. | The current permit's `expiry_date` in ISO form, or `"initial"` when the employee holds none |
| `latest_credential` ("Anchor to latest credential of group") | Offboarding hand-in pattern: anchors ONE service to the holder's newest active credential of the group, bypassing every renewal-protocol suppression (plan coverage, waiver, employment end) — the final card must come back precisely at the moments those would suppress. Dropped when the employee holds no active credential of the group. | `final-credential-<credential_id>` |

For completeness, the credential-subject redirect above keys its occasion on the triggering credential: `credential-<credential_id>`. Each shape is a distinct occasion, so a newer card is a new service while re-entering the triggering state never duplicates one.

#### `credential_plan_urgency_exempt`

`riverflow.workflow.credential_plan_urgency_exempt` (Boolean, added by rivercreds in `rivercreds/models/riverflow_transition.py`) is a **slot-urgency** concept only. Services of an exempt workflow that link to a credential plan slot still show on the credential planning timeline — badge plus deadline marker, in the normal state colours — but their deadlines are skipped by `rivercreds.plan.slot._compute_earliest_service_deadline`, so they never drive the row's urgency; they also sort after non-exempt services in the slot's badge strip. The flag does **not** hide or mute the marker (the badge payload still carries an `urgency_exempt` key, but the timeline renderer no longer reads it).

It lives in rivercreds rather than riverflow so the base engine stays credential-agnostic. First consumer: the Cuneus "Work Permit Handover" workflow — a handover task spawned the day a renewal succeeded must not turn a healthy renewal row red.

### Recreate-on-Done Without a Cron

A recurring service can recreate its next instance the moment it reaches its end state — no `ir.cron`. This reuses the same stored-Boolean-compute trigger pattern as auto-add, but keyed on reaching the **end state** rather than on every state change.

**Mechanism**: a stored Boolean compute on `riverflow.service` (`@api.depends("state_id", "state_id.is_end_state", "workflow_id")`) fires its side effect when the service enters an end state of the recurring workflow, creating next period's instance. Mirrors `crewradar_creds`' `credential_auto_add_trigger`.

**Worked example** — the "Weekly Work Permit Issue Request" workflow (`crewradar_cuneus_sign`): `riverflow.service.weekly_spawn_trigger` calls `_spawn_next_weekly_request()` when a weekly service reaches Done, creating next Monday's instance (`deadline = _next_monday()`).

**Guard rails** (all required, or upgrades/seed cascade-spawn or double-spawn):

- **`registry.loaded`** — no-op during module load.
- **`isinstance(self.id, models.NewId)`** — skip in-memory records.
- **A `skip_*` context flag** (e.g. `skip_weekly_spawn`) — the install seed and migrations set it so seeding a first instance does not itself cascade-spawn another.
- **Dedup** — `_spawn_next_weekly_request()` skips when another open instance already exists for the subject, OR one already targets next Monday. This makes re-open/re-close idempotent (a Back-then-Done never double-spawns).

The recurrence anchor (here a singleton on the main `res.company`) and the period helper (`_next_monday(from_date)` — the next Monday *strictly after* `from_date`, so a Monday → +7) live as plain module functions, kept separate from the trigger so the seed and migration can call them directly.

**Extension hooks on `auto_add_services()`**:

| Hook | Called when | Purpose |
|------|-----------|---------|
| `_filter_auto_add_candidates(new_services, model)` | After candidate building, before dedup | Drop candidates **and/or re-anchor them onto a different subject** — the hook may rewrite a candidate's `res_model`/`res_id` and set its `auto_add_context_ref`. `rivercreds/models/riverflow_auto_add_service.py` dispatches by subject model: `crewradar.log.entry` and `hr.employee` candidates are *filtered* on credential coverage, while `rivercreds.credential` candidates are *redirected* onto the credential's holder by `_redirect_credential_candidates_to_holder` (`_credential_candidate_holder` resolves scope `holder` → `hr.employee`; `crewradar_creds/models/riverflow_auto_add_service.py` overrides it for scope `site` → `crewradar.site`). Placement scope has no holder redirect — deliberately deferred, no consumer. |
| `_get_service_creation_context(service_vals)` | Per service, just before template cloning | Build the context dict for `_create_services_from_template()`. Base returns standard defaults (`res_id`, `res_model`, `created_by_auto_add_service_id`, `auto_add_context_ref`). Rivercreds overrides to inject `default_credential_plan_item_id`, `default_credential_plan_slot_id` and `default_source_credential_id` from whatever the filter/redirect hook set on the candidate. |

### Built-in Service Workflows

Riverflow ships several reusable service workflows:

| Workflow | XML ID | Use Case |
|----------|--------|----------|
| Task | `workflow_service_task` | General-purpose task tracking. Not started → In Progress → Done, plus a hidden **Cancelled** end state (`state_task_cancelled`, `is_cancelled_state`, added 19.0.1.1201) reached by the Cancel buttons on Not started / In Progress or by a parent's cancel cascade; **Re-start** leads back out of it |
| Send Email | `workflow_service_send_email` | Email-sending with reply tracking (Not Started → Awaiting Reply → Done) |
| Send Notification | `workflow_service_send_notification` | One-shot notification emails with fast path to Done |
| Arrange and Send | `workflow_service_arrange_and_send` | Document generation + send workflow |

**Send Notification vs. Send Email**: Both have the same 4 states (Not Started, Awaiting Reply, Done, Not Needed). The key difference is the primary "Send" transition: in Send Email, it goes to Awaiting Reply (expecting a response); in Send Notification, it goes directly to Done (one-shot send). Send Notification also has a secondary "Send and Await Reply" transition for flexibility. Designed for deferred child email services where the send-and-done fast path is the normal case (e.g., A1 certificate distribution emails).

### Action-with-Outcome = Transition, not State

A design principle established while reshaping the DE Work Permit workflows. Twice an action was first modelled as a back-office **state** and corrected to a **transition**:

- **An action with a branching outcome is a transition with a conditional target**, not a resting state. If "do X" can end in AT → Done *or* FB → loop back, that is one transition whose wizard rewrites `state_id` (see [Conditional Transition Routing](#conditional-transition-routing-wizard-rewrites-the-target-state)) — not two states the service rests in. ("Upload Permit" was a state, became a transition.)
- **"Show warnings and let the user retry/confirm" belongs in the wizard**, not in a workflow self-loop state. Use a reopen-with-results gate (see [In-Wizard Results / Warnings Gate](#in-wizard-results--warnings-gate-no-self-loop-state)). ("Bulk upload" was a state with a "warn" self-loop, became a transition with an in-wizard gate.)
- **A genuine back-office state is for a service that actually rests there** awaiting a separate, later action (e.g. "AT Applied" waiting for a future bulk pickup) — not for a momentary processing step that always resolves to a next state within the same user action.

## Technical Reference

### Key Models

| Model | Purpose |
|-------|---------|
| `riverflow.workflow` | Workflow definitions |
| `riverflow.state` | State definitions. Credential planning flags on the form since 19.0.1.1274: `counts_as_active_employment` (an employee in the state counts as (possibly) employed for holder credentials, e.g. Agreement Signed / Employed / Offboarding) and `requires_onboarding_credentials` |
| `riverflow.transition` | Transition definitions |
| `riverflow.transition.action` | Transition action handlers |
| `riverflow.service` | Service instances. `_get_template_clone_vals(template_service)` hook for adding fields during template cloning. Template placement fields: `can_be_root_service`, `can_be_child_service` (Booleans, default True), `allowed_subject_model_ids` (Many2many to `ir.model`); `_template_placement_allowed()` shared helper; `_check_template_placement()` validation in `_create_service_member_from_template`. `daily_prio` (Integer, labeled "Priority") controls display order of services with the same deadline in list views and journey timelines — lower numbers first. `supply_time_of_day` (Float, 0.0–23.99, `float_time` widget) records the time of day for services — appointment times (AB), taxi pickup times, train departure times. Visibility on the Supply Order tab controlled by `has_supply_time` workflow flag; shown as "Appointment" (date + time on one row) in the left column for appointment services — a journey parent (`is_journey_parent`) or any non-supply-order service with an appointment date set (`supply_actual_date`), so a proposed slot shows before a journey is armed; supply orders excluded. When set and `daily_prio` is at default (1), auto-syncs to HHMM `daily_prio`. Extended by `crewradar_sign` with `credential_type_id`, `credential_id`, `sign_direct_generate`, and `_prepare_sent_dto()` hook for credential-producing document workflows. Extended by `rivercreds` with `credential_type_group_id` (which credential group the service arranges) and `email_credential_type_id` (default credential type for email-wizard auto-attach; overridable per transition via `action_context` key `email_credential_type_code` — see Transition Action Context). Extended by `crewradar` with `is_service_with_journey` (journey anchor, configured on Template Settings tab only). Document metadata (`sent_dto`, `application_date`) is stored on `rivercreds.credential`, not on the service |
| `riverflow.named.domain` | Named, reusable domain on one model (was `riverflow.auto.add.domain` until 19.0.1.1274). Used by auto-add rules (`domain_id`) and by other modules; the form lists where it is used. |
| `riverflow.auto.add.service` | Auto-add rules. Hooks: `_filter_auto_add_candidates()` (pre-dedup filtering), `_get_service_creation_context()` (context injection at creation). Extended by rivercreds with `credential_type_group_id` and plan-item-aware filtering/linking. |
| `riverflow.state.record` | State tracking per record. Underpins the Radar app view (list + calendar with shortcut banner), used by users to query across different models |
| `riverflow.mail.thread.review.mixin` | Chatter mixin with message classification, internal notes summary, external messages summary |

### Transition Wizard Hierarchy

Transition wizards follow a three-level inheritance chain:

```text
riverflow.transition.wizard (AbstractModel)
  └── riverflow.service.wizard (TransientModel)
        ├── riverflow.service.email.sender.wizard  (+ mail.render.mixin)
        ├── riverflow.enter.train.ticket.wizard
        ├── riverflow.enter.airline.ticket.wizard
        ├── riverflow.service.register.wizard
        ├── riverflow.enter.distance.wizard
        ├── riverflow.verify.trip.locations.wizard
        └── riverflow.set.deadline.tomorrow.wizard
```

**Key extension points** (override in derived wizards):

| Method | Purpose |
|--------|---------|
| `default_get_using_records(defaultValues, records)` | Pre-populate wizard fields from the service being transitioned |
| `update_write_values(record, vals)` | Add wizard field values to the dict that will be written to the service |
| `create_related_records(record)` | Create side-effect records after the service is written (e.g., internal notes) |
| `get_visibility_defaults(transition_id)` | Control field visibility based on the transition |
| `_save_check()` | Return error and warning findings; OK holds until errors are fixed and warnings confirmed. See [Save Check](#save-check-confirm-warnings-before-save) |
| `_after_ticket_attachment_posted(service)` | Hook called after ticket attachments are posted (train/airline wizards). Overridden by crewradar_wilma to trigger AI PDF parsing |

### Dynamic Selection Fields on Wizards

When a wizard mirrors a Selection field from its parent model (e.g., `riverflow.service`), both the base module and inheriting modules can add options via `selection_add`. Hardcoding the selection list on the wizard creates a maintenance hazard: if a new option is added to the service model but not to the wizard, Odoo raises `ValueError` when `_prepare_transition_action` copies the service value as a `default_*` context key and Odoo validates it against the wizard's selection.

**Pattern**: Use a dynamic selection function (`selection="_selection_method_name"`) on the wizard field that reads from the parent model's field at runtime:

```python
use_project_deadline_from = fields.Selection(
    selection="_selection_use_project_deadline_from",
    ...
)

@api.model
def _selection_use_project_deadline_from(self):
    """Mirror the selection from riverflow.service so the wizard always
    accepts every value the service model can store."""
    service_field = self.env["riverflow.service"]._fields["use_project_deadline_from"]
    return service_field._description_selection(self.env)
```

This way the wizard automatically accepts any value the service can store, including options added by `selection_add` in inheriting modules. Inheriting modules no longer need their own `selection_add` on the wizard field.

**UI filtering**: To restrict which options the user _sees_ on the wizard form (while still accepting all values), use the `filterable_selection` widget with a JSON whitelist field (`use_project_deadline_from_options`). The whitelist is computed per service/context and passed to the widget via `options="{'whitelist_fname': 'use_project_deadline_from_options'}"`. The Json field needs `default=list` to prevent JS errors when the widget calls `.includes()` on null.

**Structural test**: `riverflow/tests/test_wizard_selection_sync.py` contains a guard test (`test_wizard_selection_is_superset_of_service`) that asserts the wizard selection keys are always a superset of the service selection keys.

**Appointment-style service wizards**: When the wizard has a dedicated appointment date field and should hide the generic **Project deadline** / **Deadline From** row on the transition form, set `use_project_deadline_from_invisible` (and optionally `project_deadline_invisible`) in `default_get_using_records`, and still set `project_deadline` / `use_project_deadline_from` on the service in `update_write_values` (base `update_write_values` skips copying hidden deadline fields from the wizard).

The `action_save` method orchestrates the transition: validates state hasn't changed (optimistic concurrency), calls `update_write_values` to collect vals, writes them to the record, calls `flush_recordset()` to force recomputation of stored computed fields (e.g., `auto_add_trigger` that creates side-effect services), then calls `create_related_records`. For new records (no `records_to_transition_ids`), it creates the service instead and also flushes before `create_related_records`.

**Why `flush_recordset()`**: Odoo lazily flushes stored computed fields. Without explicit flush, side-effect computes (like auto-add service creation triggered by `state_id` change) may not run until a later operation triggers a flush. The explicit flush after write/create ensures all state-dependent computes run immediately, so `create_related_records` sees the complete state.

### In-Wizard Results / Warnings Gate (no self-loop state)

**Superseded for new work by the [save check](#save-check-confirm-warnings-before-save)** (2026-10-03): return the findings from `_save_check()` instead of a reopen with a custom confirm field. The weekly bulk upload below moves in phase 3 of `radar/plans/save-check.md`.

When a transition's action processes a batch (e.g. a bulk document upload) and some items may fail, show the warnings **in the wizard** and let the user retry or confirm — do **not** model this as a back-office "warn" self-loop state.

**Mechanism**: the transition wizard runs its action, renders a per-item results table into a computed Html field (e.g. `result_html`), then:

- If there are warnings and the user has **not** ticked a confirm checkbox (e.g. `confirm_warnings`), the wizard **re-opens** itself — it returns `_reopen_wizard()` (flipping its own state back, e.g. `'done'` → `'upload'`) **instead of** calling `super().action_save()`. The service stays on the from-state; **no transition fires**.
- On a clean batch, or once the user ticks the confirm box, it calls `super().action_save()` to fire the transition normally.

This mirrors the document-import wizard's results view and the credential wizard's warning gate.

**Worked example** — the "Weekly Work Permit Issue Request" bulk-upload transition (`cuneus.weekly.bulk.upload.wizard`, Permits Picked up → Done): drag-drop the scanned permit stack, classify AT vs FB, bind each to its holder; on unreadable/unmatched scans the wizard re-opens with `result_html` populated and does not advance — the weekly service reaches Done only when the batch is clean or HR confirms.

**Design rule** (see [Action-with-Outcome = Transition, not State](#action-with-outcome--transition-not-state)): "show warnings and let the user retry/confirm" belongs **in** the wizard (a reopen-with-results gate), not in a workflow self-loop state.

### Save Check: Confirm Warnings Before Save

Built 2026-10-03 (19.0.1.1288), plan `radar/plans/save-check.md` (phases 1
and 2; phase 3 moves three more radar wizards later). Why: at least eight
wizards showed a warning with a confirm checkbox, each in its own way (five
field names, four save behaviours, error versus warning mostly an icon, a
stale tick that let a different warning through).

**Terms.** A *save check* judges a record before a *guarded method* runs
(default `action_save`, the OK button). It returns *findings*: an *error*
holds the save (no checkbox, the message says how to fix it); a *warning*
holds it until the user ticks the *confirmation* "I confirm — save with
these warnings". No `info` level: an info needs no confirmation but would
still stop the first save, so it stays a toast after the dialog closes.

**Server.** `riverflow.save.check.mixin`
(`riverflow/models/riverflow_save_check_mixin.py`); every
`riverflow.transition.wizard` inherits it, and any other model can. A
consumer overrides `_save_check()`, calls `super()` and appends dicts
`{level, message, kind?, subject?, ref?}`. Fields: `save_check_state`
(`warning` / `error`), `save_check_findings` (Json), `save_check_confirmed`.

- **The gate wraps the guarded method on the final registry class.**
  `_register_hook` sets a wrapper on `self.env.registry[model]` for every
  name in `_save_check_methods` (precedent: `base_automation` patches
  `create`); `_unregister_hook` removes it, so a module install in a running
  registry re-wraps the current method. The wrapper is the outermost layer,
  so the check runs before every override, in any load order: an override
  that imports or uploads before its `super()` has not run when the gate
  holds.
- **Once per call, also for a child with its own `_name`.** Odoo puts the
  parent's registry class in the bases of a child class (`add_to_registry`),
  so an override's `super()` reaches the parent's wrapper too. A wrapper
  gates only when `type(self)` is its own class. Test:
  `test_save_check_runs_once_per_call` (also on
  `riverflow.set.deadline.tomorrow.wizard`).
- **The gate, on every call:** no findings → go on (and clear what a hold
  stored); an error → hold; warnings only → go on when they equal the stored
  findings (level, message, subject) and the confirmation is ticked, else
  hold. A hold stores the findings, clears the confirmation, and returns
  `{"type": "ir.actions.client", "tag": "riverflow_save_check_hold",
  "params": {res_model, res_id, state, findings}}`. It returns normally, so
  the transaction commits the findings and a consumer's cache; a `UserError`
  would roll both back.
- **No fingerprint** (Ries): every guarded call checks again, so findings
  are never stale. A consumer with an expensive step caches that step
  itself, keyed by what it reads.
- `action_save_check()` is a "Check now" button: check, store, hold.

**Browser.** `static/src/components/save_check/`: the field widget
`save_check` (errors in a red box, warnings in a yellow box, `subject:
message`) and the function client action `riverflow_save_check_hold`. It
returns nothing, so the action service neither closes the dialog nor opens
anything; it triggers `RIVERFLOW:SAVE_CHECK_HOLD` on `env.bus`.
`static/src/patch/save_check_form_controller_patch.js` reloads the
`FormController` whose `resModel` / `resId` match, so the box and the
checkbox show.

`save_check_field.scss` (19.0.1.1328, radar pipeline applicants plan, decision
154) draws the checkbox row as the **footer of the yellow box**: checkbox
first, label across both columns, in the `--warning-*` colours that
`.alert-warning` reads (dark mode follows without a `.dark.scss`). Before it,
Odoo drew the row as an ordinary boolean, the label squeezed into the 150px
label column and the checkbox apart from it, and Fred read the first OK on
Cancel Offboarding as "the OK button does not work" (Q138 (a)). The rules
select by field name (`[name="save_check_confirmed"]`, `:has()`), so a form
that carries the three view lines below gets them without a change; the field
stays a real field node, so a bot still ticks it through `oteny.form.session`.

**Views.** Every transition wizard form carries three lines in a group
`name="save_check"`, the **last element of the sheet**, so the findings box
and the confirmation sit right above the OK button (Ries, 2026-10-03: the
user reads the box where they click). The root view
`riverflow.view_transition_wizard_form` has the group; a form that does not
inherit it adds the group itself:

```xml
<group name="save_check">
    <field name="save_check_state" invisible="1"/>
    <field name="save_check_findings" widget="save_check" nolabel="1" colspan="2"
           invisible="not save_check_findings"/>
    <field name="save_check_confirmed" invisible="save_check_state != 'warning'"/>
</group>
```

An inheriting view that adds content to the end of the sheet puts it
`before` `//group[@name='save_check']`, so the box stays last.

`test_every_transition_wizard_form_shows_the_save_check_box` runs on the
full install and names every primary form of a transition wizard without
them. The confirmation is a real field node because a bot can only change a
visible, editable field (`oteny.form.session._assert_amendable`).

**The bot follows the person's path** (Ries: "the user manual for the UI is
the manual for the bot"). `save`, `action_save` → a hold carries the
findings in `params` → `open` the form again with `res_id` (the photo now
shows `save_check_confirmed`) → `set` it, `save`, `action_save`.
`bot_claim` (no one to tick) turns a hold into `{ok: False, reason: <the
messages>}` in `_bot_claim_run_wizard`.

**Consumers** (2026-10-03): the incomplete-children warning
(`riverflow.service.wizard`, phase 1); in radar (phase 2) the credential
upload wizard (rivercreds: the missing document is an error finding;
crewradar_wilma: the Wilma scan, cached per file) and the airline and
train ticket wizards (one batch scan per file set). Phase 3 (later, one at
a time): the weekly bulk upload, the sign document wizard, MFNL Ask Barney.
Out of scope: the billing and salary month wizards and DATEV.

**Tests.** `riverflow/tests/test_save_check.py` (Python, with
`odoo.tests.Form`): hold then confirm, same warnings keep the confirmation,
different findings clear it, an error ignores a tick, one check per call,
the wrapper on the registry class, a clean wizard writes nothing,
`bot_claim`, the bot path, every form has the box. One hoot test,
`riverflow/static/tests/save_check.test.js`: the reload after a hold
(`mountView` needs `defineMailModels()` and an `onRpc` answer for
`ir.config_parameter.get_param`, which oteny_shortcut's wide-form toggle
reads).

**Layout.** The box shows each level (errors red, warnings yellow) in its own alert. Two or more findings of one level are a numbered list, one finding is a plain line (19.0.1.1324, Ries 10-Oct-2026); each item keeps the class `o_riverflow_save_check_error` or `o_riverflow_save_check_warning`.

### Chatter Mixin (`riverflow.mail.thread.review.mixin`)

The mixin also copies a message from an outside sender into the Responsible team's Discuss channel. A staff user's message to a colleague of another team is copied by a separate `mail.thread` hook (`models/mail_thread.py`) into the colleague's home team channel, on every record with a chatter. Which messages reach which team channel, and why, is in [Team Channels](references/team-channels.md).

The mixin extends `mail.thread` and adds message classification used by `riverflow.service` and `crewradar.log.entry`. Key fields:

| Field | Source | Purpose |
|-------|--------|---------|
| `internal_note_ids` | `message_type` in `[comment, email, email_outgoing]` + `subtype_id.internal=True` | Internal dispatcher notes |
| `external_message_ids` | Same message types + `subtype_id.internal=False` | Customer/supplier emails |
| `internal_notes_summary` | Top 3 most recent `internal_note_ids`, plain-text formatted | Shown in list/calendar/form views |
| `external_messages_summary` | Top 3 most recent `external_message_ids` | Shown in list/calendar/form views |
| `most_recent_attachment_id` | Latest attachment across all chatter messages | Used by ticket wizards to detect existing uploads |

**Message classification rules** (`_get_filtered_messages`):

- Only `message_type` in `["email", "comment", "email_outgoing"]` is considered
- `notification` type messages are deliberately excluded from both summaries
- The `subtype_id.internal` flag determines internal vs. external classification
- `mail.mt_note` (subtype id 2) has `internal=True`; `mail.mt_comment` (subtype id 1) has `internal=False`

This classification has a direct impact on how wizard-posted messages should be typed — see "Wizard Attachment Posting" below.

### Wizard Attachment Posting

When ticket wizards (train, airline) post uploaded attachments to the service chatter, two things must happen after `message_post`:

1. **Re-link attachments** from the transient wizard to the service record — Odoo's `_process_attachments_for_post` only re-links attachments from `mail.compose.message`, not from custom wizards. Without re-linking, attachments stay with `res_model='wizard' / res_id=0` and the server-side delete access check fails.

2. **Include file names as body text** — Odoo's JS `showDelete` getter requires `hasTextContent` or 2+ attachments. Without body text, single-attachment messages have no delete button.

```python
att_names = ", ".join(self.attachment_ids.mapped("name"))
service.message_post(
    body=att_names,
    attachment_ids=self.attachment_ids.ids,
)
self.attachment_ids.write(
    {"res_model": service._name, "res_id": service.id}
)
```

**Do not use `message_type='comment'` with `subtype_xmlid='mail.mt_note'`** for these messages. Because of the mixin's classification rules (see above), `comment` + `internal=True` messages are picked up by `internal_notes_summary`, so file-name listings would pollute the top-3 notes. The default `notification` type is deliberately excluded from both summaries. Tradeoff: individual attachment delete buttons work, but the message itself cannot be deleted (notification messages are not editable in Odoo's JS).

Applied in: `riverflow_enter_train_ticket.py`, `riverflow_enter_airline_ticket.py`. See also [Coding Patterns: Wizard Attachments Posted to Chatter](../odoo-development/references/coding-patterns.md) for the general Odoo pattern.

### Email Sender Wizard (`riverflow.service.email.sender.wizard`)

The email sender wizard is the transition action for `transition_action_email_sender`. It opens a full email composer with template rendering, editable content, recipient management, and file attachments. Used by workflows that need to send outgoing emails as part of a state transition (e.g., taxi supply orders, crew change confirmations, A1 child email services).

**Two-layer content model**: The wizard maintains raw template fields (`subject`, `body`) and rendered/editable fields (`subject_updatable`, `body_updatable`). When a `mail_template_id` is selected, the template is rendered via `mail.render.mixin` with QWeb (body) and inline_template (subject) engines. The rendered output populates the editable fields, which the user can then modify before sending. This allows template-based defaults with full manual override.

**Recipients**: Default recipients come from `service._get_default_recipients()` (chatter followers). When a template is selected, `partner_to` recipients from the template are merged in. The user can add or remove recipients in the wizard.

**Supply order mode**: When `is_supply_order=True`, additional fields appear (supplier, supply date, instructions) and the supplier is automatically added as a recipient. Used by taxi order workflows.

**Attachments**: Template attachments (`mail_template_id.attachment_ids`) are auto-loaded. Users can add/remove files via the `many2many_binary` widget.

**Attachment warning**: A footer alert "Attachments not yet added" appears when the email body mentions attachment-related keywords but no attachments are present. The keywords are matched via a case-insensitive regex from system parameter `riverflow.email_attachment_warning_regex`. Covers English, Dutch, and German terms (e.g., attach, bijlage, Anhang). The warning reacts live to body edits and attachment changes.

**Email domain restriction**: System parameter `riverflow.restrict_email_recipients_to` (comma-separated domains) restricts outgoing emails to specific domains. If set, recipients with email addresses outside the allowed domains are rejected with an error.

**System parameters**:

| Parameter | Purpose | Default |
|-----------|---------|---------|
| `riverflow.email_attachment_warning_regex` | Regex for body keywords that trigger the attachment warning | `attach\|enclos\|herewith\|bijlage\|bijge\|ingesloten\|meegezonden\|anhang\|anbei\|beigefüg\|beilieg\|anliegend` |
| `riverflow.restrict_email_recipients_to` | Comma-separated email domains to restrict recipients to | (empty -- no restriction) |

**Sending**: On confirm, the wizard calls `service.message_post()` with `message_type="email"` and `subtype_id=mail.mt_comment` (external). Recipients are auto-followed on the service chatter. The `email_layout_xmlid` from the template is used as the email layout.

### Review email bounce

A bounced outgoing email leaves a failed `mail.notification` on the
record that sent it. The team that owns that record gets no task
unless one is opened. The template
`riverflow.template_service_review_email_bounce` (name Review email
bounce, Task workflow, state Not started) is that task. There is no
new workflow. Version `19.0.1.1281`.

The live hook is `mail.thread._routing_handle_bounce`, after the write
that sets `notification_status` to `bounce` and `failure_type` to
`mail_bounce`. The temporary exception that `mail.mail._send` writes
does not open a task.

One open child per bounced `mail.message`
(`bounced_mail_message_id`). A later notification on that message
replaces the same internal note. An open child for that message gets
the new address appended to the sentence. The sentence is
`Bounced email to` and then each recipient as `name <email>`, joined
by a comma. The mail.message id is not in that sentence.
`failure_reason` follows in its own paragraph. The same internal note
is posted on the parent and on the child. The mail body cleaner
drops an unknown data attribute, so the wrapper id is
`riverflow-bounced-mail-message-<id>`. A later bounce finds the note
by that id. A later message gets its own child. A closed child means a
new one. The parent is the record that owns the message. A
`riverflow.service` gets `parent_id`.
Any other owner that can own a service gets `res_model` and `res_id`
and no `parent_id`. The clone copies the template team. The parent's
`responsible_team_id` and today's `project_deadline` are set after the
clone. The notes are `message_type` `comment`, subtype `mail.mt_note`,
with no `partner_ids`.

The note is posted as OdooBot. OdooBot is not a staff user, so the
external message copy would treat the note as outside mail and post it
in the Responsible team channel as `message_type` `email`. That post
can email the channel members. The copy skips `subtype_id.internal`,
so this note does not take that path. See
[Team Channels](references/team-channels.md).

Archiving the template, or removing its xmlid, turns the feature off.
An open child stays. Nothing new is created and no note is posted.

`riverflow/migrations/19.0.1.1281/post-migrate.py` uses the same
create path. It opens a service only when the bounced message is the
latest `email` or `email_outgoing` on the record and
`mail.message.date` is within 72 hours of the run. A later successful
send, a note, or a tracking message does not qualify. It does not
send email.

The code lives in `~/oteny/otenydoo` and lands on `main`.

Tests: `riverflow/tests/test_review_email_bounce.py`, tag
`test_review_email_bounce`.

### Reset Workflow to XML (`reset_workflow_to_xml`)

Developer tool on `riverflow.workflow` that resets a workflow's states and transitions back to their XML data file definitions. Useful when manual UI changes have drifted from the source XML.

**Button**: "Reset to XML" in the workflow form header (with confirmation dialog).

**Behavior**:

1. Finds all `ir.model.data` entries for the workflow, its states (`riverflow.state`), and transitions (`riverflow.transition`)
2. Scans owning modules + installed dependent modules' XML data files for matching record XML IDs
3. Re-imports matching records via `odoo.tools.convert.xml_import._tag_record` (with `mode='init'`, `noupdate=False`) — selectively re-imports only matching `<record>` elements, avoiding side effects on other records in the same file
4. Re-activates XML-defined states/transitions that were manually archived, unless the XML `active` field's element text is `false` or `0`. The check reads that text only. `eval="False"` has empty text, so the reset turns the button back on. Write `<field name="active">0</field>`. A migration that must keep the button off also writes `active = False` after the reset. Records already flagged as XML-orphans (step 5) are skipped
5. **Archives XML-orphan records** — states/transitions whose `ir.model.data` entry still points at this workflow but whose XML id no longer appears in any of the scanned data files. This is the noupdate-safe orphan cleanup: a workflow file loaded with root-level `noupdate="1"` (e.g. `work_permit_workflow.xml`) is invisible to Odoo's standard `_process_end` orphan deletion, so removing a `<record>` from such a file leaves the database row behind. `reset_workflow_to_xml` closes that gap by archiving the orphans explicitly
6. Archives manually-added states/transitions (those without XML IDs)
7. Logs a summary of changes — counts for "XML records re-imported", "orphan archived", and "manual archived"

**Caller's job — rehoming live services**: archiving a state does not touch `riverflow.service` rows that point at it. When a migration removes a state from XML, the per-workflow post-migrate must re-home any in-flight services to a surviving state *before* calling `reset_workflow_to_xml()` (the helper cannot know the correct target — it's workflow-specific). The full reshape recipe: (1) remove the dropped state/transition `<record>`s from the XML, (2) in post-migrate re-home live services off the dying states to a survivor, (3) **then** call `workflow.reset_workflow_to_xml()` (re-imports field values with `noupdate=False` AND archives the now-orphan records). Always re-home **before** reset, so no service still points at an about-to-be-archived state. A consuming business's own migration history documents this pattern's worked examples in full.

**Cross-workflow re-home (splitting one service off a shared workflow into its own):** when a service type moves onto a **new parallel** workflow while the old states stay alive for other consumers, the migration maps each in-flight instance's `state_id` from the old workflow's states to the equivalent new-workflow states. Writing **`state_id` alone is enough — `riverflow.state.mixin.write` auto-syncs `workflow_id` from `new_state.workflow_id`** (`_sync_workflow_with_state`), so a cross-workflow re-home needs no explicit `workflow_id` write. Discriminate the instances to move by a stable marker (a service tag), not shared-workflow membership, so siblings that legitimately stay on the shared workflow are left untouched. The template that seeds new instances is itself `noupdate="1"`, so the same migration must also force-write its `state_id` + `days_relative_to_project` (the XML change is skipped on `-u`). A consuming business's own migration history documents a worked example of this split in full.

**Tests**: `riverflow/tests/test_reset_workflow.py` — 6 tests covering field restore, transition restore, manual state archive, manual transition archive, re-activate archived state, and error on non-XML workflow.

### Shared Timeline Frontend Components

`riverflow/static/src/components/timeline/` holds the pure timeline building blocks shared by the crew planning timeline (`crewradar`) and the credential planning timeline (`rivercreds`/`crewradar_creds`). Both consumers import from `@riverflow/components/timeline/...`:

| File | Contents |
|------|----------|
| `range.js` | Window/column maths: `getRangeWindow`, `getIntervalUnit`, `snapWindowToColumns`, `computeSnappedGeometry`, `calculateColumns` — the rendered grid is snapped to whole interval boundaries (ISO Monday for weeks, the 1st for months) so week columns match their ISO-week header labels, while the user-chosen dates stay exact |
| `projection.js` | Date-to-percentage bar positioning: `getItemPctPosition`, `getItemPositionStyle` |
| `tracks.js` | Vertical track assignment for overlapping bars: `assignVerticalTracks`, `calculateTrackCount` |
| `row_heights.js` | Row height constants (CRED/CREW) + height helpers for both timelines |
| `timeline_scale_selector.js/.xml/.scss/.dark.scss` | `TimelineScaleSelector` — scale dropdown extending web's `ViewScaleSelector` with a "custom" scale (From/to date pickers, Apply button), optional `minDate`/`maxDate` picker clamping and a 10-year span cap |
| `layout_range.js` | `layoutFromRange` / `rangeParamsFromLayout` — a planning window ⇄ an oteny_shortcut layout: a scale id means "its default window around today", a custom period is fixed `start_date`/`stop_date` or `relative_period {start_month_offset, months}` in whole months from the start of today's month; the view injects its scale map and horizon clamp. (`range_persistence.js`, the localStorage format of the chosen range, was removed on 09-Sep-2026: the planning views no longer remember their range across visits.) |

Hoot unit tests in `riverflow/static/tests/` (`timeline_range.test.js`, `timeline_range_state.test.js`, `timeline_layout_range.test.js`, `timeline_projection.test.js`, `timeline_tracks.test.js`, `timeline_row_heights.test.js`) cover the pure timeline maths. They are registered via the `web.assets_unit_tests` bundle in `riverflow/__manifest__.py` and run in the browser Hoot runner at `/web/tests` — see [testing-guidelines § JS Unit Tests (Hoot)](../odoo-development/references/testing-guidelines.md#js-unit-tests-hoot).

Full behaviour documentation (scales, data-horizon clamping, focus-date retention, sticky-left pinning) lives with the main consumer, in its own credential-planning skill.

### Key Files

```text
riverflow/
├── models/
│   ├── riverflow_workflow.py                  # Workflow model + reset_workflow_to_xml()
│   ├── riverflow_state.py                     # State model (`bot_login_hold`)
│   ├── riverflow_state_bot_mixin.py           # Bot queue/claim/reaper + wizard `bot_claim` + `_bot_one_live_slot_defers` + `_bot_drain_peers` + `_bot_assert_human_transition_allowed` (the human fence; the timeout exit is its one exemption)
│   ├── riverflow_transition.py                # Transition model
│   ├── riverflow_service.py                   # Service model
│   └── riverflow_mail_thread_review_mixin.py  # Chatter mixin: message classification, notes summary
├── data/
│   ├── transition_action_data.xml # Built-in actions
│   ├── service_send_notification_workflow.xml  # Send Notification workflow (one-shot email fast path)
│   ├── riverflow_*_workflow.xml   # Workflow definitions
│   └── riverflow_teams_data.xml   # Team definitions
├── static/src/components/
│   └── state_record_calendar/     # Custom calendar for radar screen
│       ├── state_record_calendar_controller.js  # Extends Odoo's CalendarController (open master record, create a service)
│       ├── state_record_calendar_renderer.js    # Custom sorting, popover
│       └── state_record_calendar_view.js        # Registers "state_record_calendar" js_class
├── static/src/patch/
│   ├── many2many_binary_upload_patch.js  # Every many2many_binary upload (email sender, ticket wizards, credential uploads) blocks the UI until the file is linked; a late upload into a closed dialog warns instead of crashing with "Component is destroyed". Stock Odoo bug, upstream fix odoo/odoo#286831
│   ├── date_field_format_patch.js        # Date display format patch
│   ├── kanban_record_patch.js            # A card a user just changed gets the row-bg tint of a changed list row; see "Highlight of a Recently Changed Record"
│   ├── kanban_renderer_patch.js          # A drop on a board grouped by state opens the step (action_kanban_drop), never saves state_id, and after OK puts the moved cards first in their column until a page refresh; see "A Kanban Drop Is a Step"
│   └── list_renderer_patch.js            # A row a user just changed gets the row-bg tint (highlight_row)

│   ├── state_record_calendar/     # Custom calendar for radar screen
│   │   ├── state_record_calendar_controller.js  # Extends Odoo's CalendarController
│   │   ├── state_record_calendar_renderer.js    # Custom sorting, popover
│   │   └── state_record_calendar_view.js        # Registers "state_record_calendar" js_class
│   └── timeline/                  # Shared timeline building blocks (see Shared Timeline Frontend Components)
│       ├── range.js               # Window/column maths incl. snapWindowToColumns/computeSnappedGeometry
│       ├── projection.js          # Date→percentage bar positioning
│       ├── tracks.js              # Vertical track assignment for overlapping bars
│       ├── row_heights.js         # Row height constants + helpers
│       ├── timeline_scale_selector.js/.xml/.scss/.dark.scss  # TimelineScaleSelector (custom range pickers)
│       └── layout_range.js        # shortcut layout ⇄ timeline window (fixed / relative period)
├── static/tests/                  # Hoot JS unit tests (web.assets_unit_tests bundle, run at /web/tests)
│   ├── timeline_range.test.js
│   └── timeline_layout_range.test.js  # + range_state, projection, tracks, row_heights tests
├── views/
│   ├── riverflow_state_record_views.xml  # Radar list, calendar, search (the shortcut banner needs no js_class)
│   └── riverflow_check_result_views.xml  # Base form/list/search views for check results (inherited by crewradar)
└── wizards/
    ├── riverflow_transition_wizard.py         # Base wizard (AbstractModel). Deadline elif includes `set_deadline_relative`.
    ├── riverflow_service_wizard.py            # Service wizard base (TransientModel)
    ├── riverflow_service_email_sender.py      # Email composer wizard (template rendering, recipients, send)
    ├── riverflow_service_email_sender_view.xml # Email sender form (supply order fields, attachment warning footer)
    ├── riverflow_enter_train_ticket.py        # Train ticket entry + attachment posting
    ├── riverflow_enter_airline_ticket.py       # Airline ticket entry + attachment posting
    ├── riverflow_service_register_wizard.py   # Register service for billing
    ├── riverflow_enter_distance_wizard.py     # Enter distance for legs
    ├── riverflow_verify_trip_locations_wizard.py  # Verify/edit leg locations
    └── riverflow_start_service_wizard.py      # Start service wizard: template picker with placement filtering in _add_template_start_transitions
├── tests/
│   ├── test_wizard_selection_sync.py          # Guards wizard Selection ⊇ service Selection
│   ├── test_auto_progress.py                  # auto_progress_on_children_done, incomplete_children_warning scoping
│   ├── test_reset_workflow.py                 # reset_workflow_to_xml(): field restore, transition restore, archive manual records, re-activate, error handling
│   ├── test_service_template_placement.py     # Template placement guard rails: root/child/subject restrictions, wizard filtering, bypass context (10 tests)
│   ├── test_bot_one_live_slot.py              # Generic one-live-slot gate: exclude-self, occupant-of-workflow, strict dispatch, relaxed consume, drain
│   └── test_bot_execute.py                    # Generic execute: same strip, prepare then form session, bot_claim wizard, human fence
├── talents/
│   └── riverflow-execute-talent/              # Host Talent the bot reads (git path). Not Odoo data.

│   ├── test_workflow_icons.py                 # Guards every workflow/transition/action icon against the FA 4.7 CSS shipped with Odoo (post_install — also covers downstream modules)
│   └── test_service_template_placement.py     # Template placement guard rails: root/child/subject restrictions, wizard filtering, bypass context (10 tests)
```

## References

- [Workflow XML Guide](references/workflow-xml-guide.md) - Detailed XML writing patterns
- [Coding Patterns](../odoo-development/references/coding-patterns.md) - Odoo 19 development patterns including wizard attachment handling
- [View Shortcuts (oteny_shortcut)](../oteny-shortcut/SKILL.md) - Shortcut banner module used by the radar screen
- [Oteny Bots operator UI](../oteny-bot/SKILL.md) - derived Slot badge on `/odoo/oteny-bots` (not in this engine)
- [Host module Talents](../oteny-bot/SKILL.md#host-talent-delivery) - git path delivery for both host bundles
- [riverflow-execute-talent](../../../riverflow/talents/riverflow-execute-talent/SKILL.md) - the recipe the bot reads
- [Talent author workflow](references/talent-author-workflow.md) - operator pointer to that Talent
- [Team Channels](references/team-channels.md) - which chatter messages riverflow copies into a team's Discuss channel, and why: the external message copy (Responsible team) and the colleague message copy (recipient's home team, `res.users.home_team_id`, team form **Members** list; 19.0.1.1275)
- [Roadmap](references/roadmap.md) - Development roadmap
