# Workflow XML Guide

Detailed patterns for writing riverflow workflow XML files.

## File Structure

```xml
<?xml version="1.0"?>
<odoo noupdate="1">
    <!-- 1. Workflow definition -->
    
    <!-- 2. All states (define before transitions to prevent forward references) -->
    
    <!-- 3. All transitions (grouped by from_state with comments) -->
</odoo>
```

## Workflow Record

The `icon` must depict the ACTION that completes the task (go to the counter, post a letter, collect a signature) and must be a FontAwesome **4.7** class — see [SKILL.md § Choosing the Workflow Icon](../SKILL.md#choosing-the-workflow-icon) for the principle, examples, and the FA4-only pitfall.

```xml
<record id="workflow_service_taxi_order" model="riverflow.workflow">
    <field name="model_id" ref="riverflow.model_riverflow_service"/>
    <field name="name">Taxi Supply Order</field>
    <field name="description">Workflow for managing taxi orders</field>
    <field name="icon">fa-taxi</field>
    <!-- Service-specific fields -->
    <field name="is_supply_order">True</field>
    <field name="has_supplier">True</field>
    <field name="has_legs">True</field>
</record>
```

For a workflow attached to a non-service model owned by a consumer module
(e.g. a business's own log-entry model):

```xml
<record id="log_entry_workflow_crew_change" model="riverflow.workflow">
    <field name="model_id" ref="example_biz.model_example_biz_log_entry"/>
    <field name="name">Work</field>
    <field name="description">Placement of an employee on a ship</field>
    <field name="icon">fa-ship</field>
    <field name="work_status">work</field>
    <field name="billing_sequence">10</field>
</record>
```

### Icon Guidelines

- **FontAwesome 4.7 only.** Odoo 19 ships FA 4.7; a newer FA5/6 class name (e.g. `fa-calendar-plus`, `fa-file-invoice-dollar`) fails silently and renders as an **empty box**. Verify a candidate exists with `rg '^\.fa-NAME:before' <odoo>/addons/web/static/src/libs/fontawesome/css/font-awesome.css`. The guard test `riverflow/tests/test_workflow_icons.py` (post_install) validates every workflow, transition, and transition-action icon on the database against that CSS, so an invalid class fails the suite — including records loaded by downstream modules.
- **Disambiguate sibling workflows.** Workflows on the same model appear side by side in the same picker and service lists; do not let siblings share a glyph (the 2026-08-10 audit found three workflows on `fa-file-text` and three on `fa-envelope`). The icon should show the ACTION the user must perform, not the artifact produced — see [SKILL.md § Choosing the Workflow Icon](../SKILL.md#choosing-the-workflow-icon) for the principle and worked examples.
- **Changing a shipped icon needs a guarded post-migrate.** Workflow files — or at least their `ir_model_data` rows — are `noupdate`, so the XML edit alone never reaches existing databases (check the ROW, not the file: see [xml-data-noupdate.md](../../odoo-development/references/xml-data-noupdate.md#row-noupdate-is-stamped-at-creation--check-the-row-not-the-file)). Guard the update on the originally shipped value (`if workflow.icon == old: workflow.icon = new`) so a manual override survives. Canonical migration (riverflow's own): `riverflow/migrations/19.0.1.1198/post-migrate.py`. A consumer module doing the same guarded rewrite for its own workflows follows the identical shape in its own `migrations/` folder.

## State Records

### Sequence Convention

States have sequences that increment by **10**:

```xml
<!-- State sequence: 10, 20, 30, 40... -->
<record id="state_not_started" model="riverflow.state">
    <field name="sequence">10</field>
    ...
</record>

<record id="state_ordered" model="riverflow.state">
    <field name="sequence">20</field>
    ...
</record>

<record id="state_confirmed" model="riverflow.state">
    <field name="sequence">30</field>
    ...
</record>
```

### Unique names inside one workflow

`name` must be unique among states of the same workflow. The list groups by `"<state> | <workflow>"`. The bot harness verifies with the display name, not the xmlid. Two states that share a label look like one group and break name-keyed expect/refusal text. Keep the xmlid stable; change only `name`. A truthful qualifier is enough (`Barney is filling (after login)`).

### Common State Patterns

**Initial state:**

```xml
<record id="state_not_started" model="riverflow.state">
    <field name="workflow_id" ref="workflow_example"/>
    <field name="name">Not Started</field>
    <field name="sequence">10</field>
    <field name="color_int">7</field>  <!-- Teal -->
</record>
```

**End state (completed):**

```xml
<record id="state_completed" model="riverflow.state">
    <field name="workflow_id" ref="workflow_example"/>
    <field name="name">Completed</field>
    <field name="sequence">50</field>
    <field name="is_end_state">True</field>
    <field name="is_back_office_state">True</field>
    <field name="color_int">10</field>  <!-- Green -->
</record>
```

**Cancelled state:**

```xml
<record id="state_cancelled" model="riverflow.state">
    <field name="workflow_id" ref="workflow_example"/>
    <field name="name">Cancelled</field>
    <field name="sequence">60</field>
    <field name="is_end_state">True</field>
    <field name="is_cancelled_state">True</field>
    <field name="hide_in_statusbar">True</field>
    <field name="color_int">1</field>  <!-- Red -->
</record>
```

**Bot login hold** (generic — set this on login-park and login-resume states so fresh
work waits; the parked record's own resume is admitted):

```xml
<field name="bot_login_hold">True</field>
```

Which states set the flag stays in the owning app's workflow XML. There is no
occupancy record and no extra clock. A SLA-less login park does not occupy
the slot. When a record **leaves** `in_progress` or `bot_login_hold`,
`_bot_drain_peers` dispatches the oldest queued peer in that same `write`
transaction. If no queue peer waits, it resumes one fresh SLA-less login
park (`_bot_resume_login_park`). A leftover park older than
`_BOT_LOGIN_PARK_FRESH_HOURS` stays human-owned. That is the primary
release. The 3-min `bot_dispatch_queue` cron is the catch-up belt for
queue. It does not promote parks. Do not treat the cron as the normal
path. A mid-turn browser row (`linger_until == 0`) is not adoptable.

**State with review service:**

```xml
<record id="state_in_progress" model="riverflow.state">
    <field name="workflow_id" ref="workflow_example"/>
    <field name="name">In Progress</field>
    <field name="sequence">20</field>
    <field name="add_review_service_on_change">True</field>
    <field name="review_on_change_max_days_in_future">60</field>
    <field name="color_int">8</field>  <!-- Blue -->
</record>
```

### State Color Reference

| Color Int | Name | Typical Use |
|-----------|------|-------------|
| 1 | Red | Cancelled, Error |
| 2 | Orange | Warning, Registered |
| 4 | Cyan | Arranging, Attention |
| 7 | Teal | Initial, Not Started |
| 8 | Blue | Working, In Progress |
| 10 | Green | Completed, Success |
| 11 | Violet | Planning, Draft |

## Transition Records

### Transition / button naming

`transition.name` is the **form-header button caption** (Riverflow renders it as `caption`;
the lowest-`sequence` button gets `btn-primary`). Name the **action the clicker takes**, never
the destination state or a completed result.

| Kind | Rule | Examples |
|------|------|----------|
| **State `name`** | May describe the situation | `Needs Login`, `Paused for Approval`, `Barney is posting` |
| **Human button** | Imperative verb phrase | `Continue after login`, `Approve & submit`, `Hand to Barney`, `Send back for rework` |
| **Bot button** (`bot_role` set, or only the bot takes it) | `<BotDisplayName>: <verb phrase>` | `Barney: ask HR to log in`, `Barney: mark filed`, `Barney: start filing` |

Do not render `bot_role` `claim` / `work` to an HR user. Those exits stay on the
transition for `bot_claim`. The form header hides them. A live claim
(`_bot_claim_is_live`) shows a working note plus **one** button: that state's
`is_bot_timeout` exit. Why only that one: Hand back or Cancel on a mid-fill can
land between *Indienen* and the credential write, so a person must not move the
record out from under a live run — except through the exit the reaper itself
would take, which is the same judgement made sooner and lands in the same state.
A bot-owned `queue` / `in_progress` strip sets `no_primary`, so no blue primary
sits on a wait or a fill, and the abort is never primary either.

**So give every bot `in_progress` state an `is_bot_timeout` exit, and point it at
"unfinished, a person should look" — never at "cancelled".** Without the exit the
state has no reaper *and* no abort, so a dead run strands the record with no door
at all. And the landing state is what makes the early abort safe: "unfinished"
stays true whether or not an irreversible act landed, where "cancelled" becomes a
lie the record carries. Put the warning a person needs on the transition's own
`description` — the wizard renders it, and it is the only place they are told
what the abort cannot undo.

Anti-patterns (do not use as button labels):

- Destination/result as the button: `Needs login`, `Login complete — continue`, bare `Confirmed`
- A rare override before the owner's intended next action (e.g. `File` / `Mark as filed` before
  `Hand back to HR` on a bot queue) — that steals the purple primary

### Sequence Convention

Transitions restart sequence at **10** for each `from_state`, incrementing by **10**.
**Lowest sequence = left-most = `btn-primary`.** Put the state's **owner's intended next
action** first; supporting / rare actions next; **Cancel last**. On bot-owned states, put the
human escape (`Hand back to HR`) before bot `claim`/`work` exits so a bot work-exit does not
steal primary on a human-facing strip.

```xml
<!-- Transitions from Not Started -->
<record id="trans_not_started_to_ordered" model="riverflow.transition">
    <field name="from_state_id" ref="state_not_started"/>
    <field name="sequence">10</field>  <!-- First transition from this state -->
    ...
</record>

<record id="trans_not_started_to_confirmed" model="riverflow.transition">
    <field name="from_state_id" ref="state_not_started"/>
    <field name="sequence">20</field>  <!-- Second transition from this state -->
    ...
</record>

<!-- Transitions from Ordered (sequence restarts) -->
<record id="trans_ordered_to_confirmed" model="riverflow.transition">
    <field name="from_state_id" ref="state_ordered"/>
    <field name="sequence">10</field>  <!-- First transition from Ordered -->
    ...
</record>
```

### Grouping with Comments

Always add comments to group transitions by from_state:

```xml
<!-- Transitions from Not Started -->
<record id="trans_not_started_to_ordered" model="riverflow.transition">
    ...
</record>

<!-- Transitions from Ordered -->
<record id="trans_ordered_to_confirmed" model="riverflow.transition">
    ...
</record>

<!-- Transitions from Confirmed -->
<record id="trans_confirmed_to_completed" model="riverflow.transition">
    ...
</record>
```

### Initial Transition (No from_state)

The initial transition has an empty `from_state_id`:

```xml
<record id="trans_initial_to_not_started" model="riverflow.transition">
    <field name="name">Create</field>
    <field name="from_state_id" ref=""/>  <!-- Empty = initial transition -->
    <field name="to_state_id" ref="state_not_started"/>
    <field name="action_id" ref="riverflow.transition_action_default"/>
    <field name="sequence">10</field>
</record>
```

### Common Transition Actions

| Action ID | Purpose |
|-----------|---------|
| `riverflow.transition_action_default` | Simple state change, no wizard |
| `riverflow.transition_action_email_sender` | Opens email composer wizard |
| `riverflow.transition_action_register_service` | Marks for billing/registration |
| `riverflow.transition_action_supplier_confirmed` | Records supplier confirmation |

A consumer module adds its own transition actions the same way, under its own
module namespace, for example:

| Action ID | Purpose |
|-----------|---------|
| `example_biz.log_entry_transition_action_new_prospect` | New planning entry |
| `example_biz.log_entry_transition_action_validate_employee_sign_on` | Validate sign-on |
| `example_biz.log_entry_transition_action_set_actual_start_date` | Set actual start |

### Transition Examples

**Simple transition:**

```xml
<record id="trans_not_started_to_ordered" model="riverflow.transition">
    <field name="name">Order Now</field>
    <field name="from_state_id" ref="state_not_started"/>
    <field name="to_state_id" ref="state_ordered"/>
    <field name="action_id" ref="riverflow.transition_action_email_sender"/>
    <field name="icon">fa-taxi</field>
    <field name="sequence">10</field>
    <field name="to_responsible_team_id" ref="riverflow.partner_team_logistics"/>
</record>
```

**Cancel transition:**

```xml
<record id="trans_ordered_to_cancelled" model="riverflow.transition">
    <field name="name">Cancel</field>
    <field name="from_state_id" ref="state_ordered"/>
    <field name="to_state_id" ref="state_cancelled"/>
    <field name="action_id" ref="riverflow.transition_action_default"/>
    <field name="icon">fa-times</field>
    <field name="sequence">40</field>
</record>
```

**Self-transition (stay in same state):**

```xml
<record id="trans_ordered_to_ordered" model="riverflow.transition">
    <field name="name">Re-order</field>
    <field name="from_state_id" ref="state_ordered"/>
    <field name="to_state_id" ref="state_ordered"/>  <!-- Same state -->
    <field name="action_id" ref="riverflow.transition_action_email_sender"/>
    <field name="icon">fa-refresh</field>
    <field name="sequence">30</field>
</record>
```

**Return/undo transition:**

```xml
<record id="trans_completed_to_in_progress" model="riverflow.transition">
    <field name="name">Return to In Progress</field>
    <field name="from_state_id" ref="state_completed"/>
    <field name="to_state_id" ref="state_in_progress"/>
    <field name="action_id" ref="riverflow.transition_action_default"/>
    <field name="icon">fa-undo</field>
    <field name="sequence">10</field>
</record>
```

### State vs. Transition: Action-with-Outcome is a Transition

A recurring modelling decision: an action with a branching outcome, or a "process this and show me any problems" step, is a **transition** — not a back-office state the service rests in.

- **Branching outcome → conditional-target transition.** If "do X" can end in two different next states (e.g. AT → Done vs FB → loop back), model it as **one** transition with a nominal `to_state_id`; the wizard rewrites the real target at runtime by setting `vals["state_id"]`. Do not create a state per branch. See [SKILL — Conditional Transition Routing](../SKILL.md#conditional-transition-routing-wizard-rewrites-the-target-state).
- **Warnings/retry → in-wizard gate, not a self-loop state.** A "review results and confirm" step belongs in the transition wizard (render a results table, re-open the wizard on warnings, fire the transition only when clean/confirmed) — not a state with a "warn" self-loop transition. See [SKILL — In-Wizard Results / Warnings Gate](../SKILL.md#in-wizard-results--warnings-gate-no-self-loop-state).
- **A back-office state is for genuine rest** — a service that actually waits there for a separate, later action — not for a momentary processing step that always resolves within the same user action.

Both "Upload Permit" and "Bulk upload" in the DE Work Permit workflows were first written as states and corrected to transitions on exactly this principle.

## Cross-Module Template Enrichment (`noupdate=0` Section)

When a workflow XML needs to set fields on records from another module (e.g. a service template defined in one consumer module that needs `state_id` from a workflow defined in another), use a `noupdate=0` `<data>` section at the end of the workflow XML file.

```xml
<odoo noupdate="1">
    <!-- Workflow, states, transitions (noupdate=1 — don't overwrite manual changes) -->
    ...
</odoo>

<odoo noupdate="0">
    <!-- Template enrichment: always re-applied on -u -->
    <record id="example_biz.template_service_send_example" model="riverflow.service">
        <field name="state_id" ref="state_auv_not_started"/>
        <field name="sign_template_id" ref="example_biz_sign.example_template"/>
        <field name="sign_direct_generate" eval="True"/>
    </record>
</odoo>
```

**Why this pattern**: The template's original module uses `noupdate=1`, so fields set via this enrichment section always win on `-u` module upgrade. For existing databases that already have the records, combine with a migration script (`post-migrate.py`) for belt-and-suspenders reliability.

**XML load order**: When the enrichment references records defined earlier in the same module (e.g. `state_auv_not_started`), this naturally works. When it references records from sign template XML files in the same module, ensure the workflow XML is listed AFTER those files in `__manifest__.py`.

## Updating Existing Workflows

When modifying workflows:

1. **Re-number sequences** if states/transitions are inserted or deleted
2. **Keep existing flags** (`is_end_state`, `is_back_office_state`) unless there's a reason to change
3. **Test transitions** ensure all paths work correctly
4. **Removing a state or transition from a `noupdate="1"` file**: Odoo's standard module-update orphan cleanup does NOT delete records whose XML id was loaded with `noupdate="1"`, so the database rows for the removed `<record>` survive the upgrade. To clean them up, call `workflow.reset_workflow_to_xml()` in the post-migrate — it now archives XML-orphan records (records whose XML id is no longer in any data file). Before calling the reset, **re-home any live `riverflow.service` rows** pointing at the dying states to a surviving state (the helper cannot know the workflow-specific target). A consumer module's own `migrations/` folder is where this re-home-then-reset pattern belongs — it is workflow-specific and cannot live in `riverflow` itself.

### Retiring an Entire Workflow

When a workflow is removed altogether — canonical example: the never-live "To Be Invoiced" stub retired in [riverflow/migrations/19.0.1.1198/post-migrate.py](../../../riverflow/migrations/19.0.1.1198/post-migrate.py):

1. **Delete the XML file** and remove its entry from `__manifest__.py`.
2. **A post-migrate must delete the surviving rows.** The `noupdate` rows are invisible to Odoo's orphan cleanup and survive the `-u`. Delete the **states first, then the workflow** — ORM `unlink()` also removes the `ir_model_data` rows, so no separate cleanup is needed.
3. **Guard the deletion on zero usage.** Search `riverflow.service` for the workflow via `workflow_id` **OR** `front_office_workflow_id`, with `active_test=False`. If any service references it after all, skip the retirement and log a warning instead of breaking the upgrade.
4. **Hand-made workflows are archived, not deleted.** A workflow created via the UI (no XML id) is hand-entered data the module does not own — match it by exact name + absence of an `ir.model.data` row + zero service usage, then set `active = False`.

Keep every step search-then-act so the migration stays idempotent when re-run.

### Adding a State

Insert with appropriate sequence, renumber subsequent states:

```xml
<!-- Before: 10, 20, 30 -->
<!-- After: 10, 20, 25 (new), 30 -->
<!-- Or: 10, 20, 30 (new), 40 (renumbered) -->
```

### Adding a Transition

Add with appropriate sequence for its from_state group:

```xml
<!-- Existing transitions from state_ordered: 10, 20 -->
<!-- Add new: sequence 30 -->
```

**`noupdate="1"` + `-u` behaviour (important):** a *brand-new* `<record>` (an xmlid not yet in the DB) **is** created on plain `-u` — `noupdate="1"` only suppresses *updates* to records that already exist, never creation of new ones. So pure additions load without a migration. But the moment you **renumber an existing** transition (e.g. inserting a new transition mid-group and bumping the old `Back` from 20 → 30 to keep it last), that existing row's sequence change will **not** apply on `-u` — `noupdate="1"` blocks it. Call `workflow.reset_workflow_to_xml()` in a post-migrate to force-re-import the XML rows (it bypasses the `noupdate` guard), which lands both the new transitions and the renumbered existing ones. No states removed → nothing to re-home first.

## Complete Example

See `/riverflow/data/riverflow_taxi_order_workflow.xml` for a complete workflow example with:

- Workflow definition with service-specific fields
- Five states (Not Started → Ordered → Confirmed → Registered → Cancelled)
- Multiple transitions per state
- Team assignments
- Action contexts

## Prompt Notation

When describing workflows in prompts, use arrow notation:

```
a -> b -> c
```

This indicates:

- 3 states: a, b, c
- 2 transitions: a→b, b→c

More complex:

```
Plan -> Arrange -> Working -> Completed
           ↓
       Cancelled
```
