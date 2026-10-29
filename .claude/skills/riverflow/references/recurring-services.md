# Recurring services

A recurring service is one service that goes around once per period, with no end state; this file
is the manual section of the concept: what it is for, where users find it, how its deadline moves,
and how it is built (riverflow 19.0.1.1381 and 19.0.1.1382; the decisions are in the plan
[recurring-services.md](../plans/recurring-services.md)).

## What a recurring service is

Some work comes back every week, month or year and belongs to no employee, ship or log entry. A
**Recurring Service** is a service of a workflow marked **Recurring**. It is one service that
stays open for good: its workflow has no end state. When the user finishes a period, the
transition leads back to an earlier state and moves the deadline to the next period. The deadline
carries the period, not the name.

Why one service and not a new service per period: a chain of services, where each closed service
makes the next one, stopped without a message on production (radar, 23-Oct-2026: two closes of
the same week before its deadline made a second service for that week and then none). One service
that loops has no chain that can stop and no second service for the same period.

**The history of a period is in the chatter of the service**: the e-mails that its transitions
send and the summaries that its screens post.

## Where to find it

- **Services → Recurring → Recurring Services**: every recurring service, with its state and
  deadline. Templates are not in the list.
- **Services → Recurring → ➕ New Recurring Service**: the start screen of New Service, with only
  the start transitions and the templates of recurring workflows. Every user who may start a
  service (the group Writer, `riverflow.group_service_writer`) sees it.
- The team's Radar shows the service as any open service, and the row turns red when the deadline
  is past.

The service templates moved to their own menu at the same time: **Services → Templates → Service
Templates** and **➕ New Template** (formerly under Configuration).

## The deadline of each transition

Each transition of a recurring workflow says how it sets the deadline. Its screen shows the
deadline field with the proposed date, and the user can change the date before Save (a public
holiday, a closed office). On Save the service gets the date on the screen. The user can also
change the deadline on the service at any time.

- **The next given weekday.** The screen proposes the first such weekday strictly after the
  service's current deadline, never counted from today. A week finished early or late still moves
  one period on. A period finished weeks late gets a proposal in the past; the user changes it by
  hand, because the user keeps the service up to date.
- **Ask.** The screen shows the deadline field empty and required, with no proposal. Use it for a
  Back after a mis-click: no rule knows the right date then.

## When the service is gone

A user may delete a recurring service; no rule stops it. The repair is **➕ New Recurring
Service**, which makes a new one. A fresh database has no recurring service until a user (or a
migration) makes it.

## Worked example

Radar's **Collect Work Permits at AB** (`crewradar_cuneus_sign`), the weekly round of Cuneus HR at
the Ausländerbehörde: Send Pickup Request proposes the Friday, Upload Permits and Nothing to
Upload propose the next Monday, Back asks. HR manual: radar
`crewradar-cuneus/references/collect-work-permits-at-ab.md`; technical reference: radar
`crewradar-cuneus/references/weekly-work-permit-request.md`.

## Technical Reference

### The flag

`riverflow.workflow.is_recurring` (Boolean, label Recurring, default False), on the workflow form
(Services → Configuration → Workflows) under Enforce Single Open Service. It only places the
services in the menu Recurring and the start screen; nothing checks that the workflow has no end
state.

**No end state, and what that means.** `is_open` of the service is always True, so filters on
"not in an end state" (the Radar shortcuts) always show it, and its Radar row
(`riverflow.state.record`) stays. The transition screen shows the Responsible field on every
transition (`get_visibility_defaults` hides it only on a transition into an end state). The rules
that need an end state on a child or a subject do not touch a service without a subject, a parent
or child services: the cancel cascade (`_cascade_cancel_to_subtree`, `_cancel_end_state`), the
parent's auto-progress (`auto_progress_on_children_done`), `refuse_with_open_services`, and
`auto_complete_state_id`. A later recurring service with a subject or a parent needs them first
(roadmap).

**No `enforce_single_open`.** That field refuses the delete of a service past its first state
(`_single_open_removal_blocked`), and the delete must stay allowed (the repair is New Recurring
Service).

**No subject.** A recurring service needs no `res_model`/`res_id`. Its company is then the
company the user has selected ([service-company.md](service-company.md), rule 4).

### The menus and the start screen

| Menu | xmlid | Action |
|---|---|---|
| Recurring (sequence 40) | `riverflow.service_menu_recurring_root` | — |
| Recurring Services | `riverflow.service_menu_recurring_services` | `riverflow.action_recurring_services`: `riverflow.service`, path `recurring-services`, list, form, calendar; domain `[('workflow_id.is_recurring', '=', True), ('is_root_a_template', '=', False)]` |
| ➕ New Recurring Service | `riverflow.service_menu_start_recurring_service` (groups `riverflow.group_service_writer`) | `riverflow.action_riverflow_start_recurring_service`: `riverflow.start.service`, path `start-recurring-service`, target new, context `{'recurring_only': True}` |
| Templates (sequence 50) | `riverflow.service_menu_templates_root` | — |
| Service Templates | `riverflow.services_menu_configuration_templates` (id kept) | `riverflow.action_service_templates` |
| ➕ New Template | `riverflow.service_menu_start_template` (id kept) | `riverflow.action_riverflow_start_template` |

Configuration moved to sequence 90, so the order is Services, ➕ New Service, Recurring,
Templates, Configuration.

**`recurring_only`.** With this context key, `riverflow.start.wizard._default_start_transition_ids()`
keeps only the start transitions with `workflow_id.is_recurring`, and
`riverflow.start.service._add_template_start_transitions()` keeps only the templates whose own
workflow is recurring (not the workflow of their root). Without the key both list what they did
before.

### The deadline keys

Two bare keys in the transition's `action_context`, both in `STEP_DEADLINE_KEYS`
(`models/riverflow_state.py`), so the target state's Deadline on Entry does not overwrite them:

| Key | Screen (`riverflow.service.wizard.default_get_using_records`) | Save (`riverflow.transition.wizard.action_save`) |
|---|---|---|
| `next_deadline_weekday` (a lower-case English weekday name, `'monday'` … `'sunday'`) | Proposes `_next_weekday_deadline()` of the first record, shows the field (`use_project_deadline_from = 'self'`). | Writes the screen date, as `use_project_deadline_from = 'self'`, `days_relative_to_project = 0`. An empty date (a cleared field, or a wizard without a deadline field) gets the proposal for the record of the loop. |
| `ask_deadline` (`True`) | Shows the field empty, with `project_deadline_required = True`. | Writes the screen date as above; an empty date raises "Enter the deadline for this transition." |

`_next_weekday_deadline(record)` (`wizards/riverflow_transition_wizard.py`):
`current + ((WEEKDAYS.index(name) - current.weekday() - 1) % 7 + 1)` days, where `current` is the
stored `project_deadline` of the record (not the effective `deadline`), or today when the record
has none or there is no record (a start transition). The result is 1 to 7 days after `current`.
An unknown name raises a UserError that names the key.

| Current deadline | Key | Proposed |
|---|---|---|
| Mon 26-Oct-2026 | `'friday'` | Fri 30-Oct |
| Tue 27-Oct (moved by hand) | `'friday'` | Fri 30-Oct |
| Fri 30-Oct | `'monday'` | Mon 2-Nov |
| Mon 26-Oct (clicked on Fri 23-Oct, early) | `'monday'` | Mon 2-Nov |
| Fri 30-Oct | `'friday'` | Fri 6-Nov |
| none (start transition on Wed 28-Oct) | `'monday'` | Mon 2-Nov |
| any | `ask_deadline` | none: the user enters it |

**Order in `action_save`.** The deadline chain runs after `update_write_values`: `clear_deadline`,
`set_deadline_to_today`, `followup_in_days`, then `next_deadline_weekday` / `ask_deadline`, then
`set_deadline_relative`, else the target state's setting. On the e-mail sender
(`riverflow.service.email.sender.wizard`) `update_write_values` writes tomorrow first; with one of
the two keys the chain overwrites it with the date of the field Follow-up deadline, which the
screen shows.

**A screen that inherits `riverflow.service.wizard`** gets the proposal through
`super().default_get_using_records()`. It must not hide the deadline field when the key is set,
and must not write `project_deadline` after `super().action_save()` (radar's
`cuneus.weekly.bulk.upload.wizard` is the example).

### Key files

- `riverflow/models/riverflow_workflow.py` — `is_recurring`.
- `riverflow/models/riverflow_state.py` — `STEP_DEADLINE_KEYS`.
- `riverflow/wizards/riverflow_transition_wizard.py` — `WEEKDAYS`, `_next_weekday_deadline()`, the
  branch in `action_save`.
- `riverflow/wizards/riverflow_service_wizard.py` — the proposal, `project_deadline_required`.
- `riverflow/wizards/riverflow_service_wizard_view.xml`, `riverflow_service_email_sender_view.xml`
  — `required="project_deadline_required"`.
- `riverflow/wizards/riverflow_start_wizard.py`, `riverflow_start_service_wizard.py` —
  `recurring_only`.
- `riverflow/views/riverflow_service_views.xml` — the two actions and the menus;
  `riverflow/views/riverflow_workflow_views.xml` — the flag on the form.

### Tests

- `riverflow/tests/test_recurring_services.py` (tag `test_recurring_services`, 6 tests): the flag's
  default, the start screen with and without `recurring_only` (transitions and templates), the
  domain of Recurring Services, the context of New Recurring Service, the menu structure.
- `riverflow/tests/test_step_deadline_weekday.py` (tag `test_step_deadline_weekday`, 17 tests):
  every weekday of the deadline with `'friday'` and `'monday'`, clicked before and after it; no
  deadline counts from today; two weeks in a row; a typed date wins; the target state does not
  overwrite; a cleared date, on a start transition and per record of a bulk transition;
  `followup_in_days` unchanged; `ask_deadline` empty, required, refused when empty; the e-mail
  sender with each key and without.
- The worked example: radar `crewradar_cuneus_sign/tests/test_weekly_permit_workflow.py` and
  `test_weekly_bulk_upload_driver.py`.
