# Plan: recurring services

Status: design agreed with Thijs on 28-Oct-2026: one service that loops. Built on 29-Oct-2026 on
`feat/recurring-services` in otenydoo and radar (riverflow 19.0.1.1382, crewradar 19.0.11.89,
crewradar_cuneus_sign 19.0.6.292); merged on 29-Oct-2026 into otenydoo `main` and radar `dev`,
and deployed to test1 the same day. See [Outcome](#outcome-29-oct-2026).
The manual section is [recurring-services.md](../references/recurring-services.md).
Sources: Ries's brief of 28-Oct-2026 (`Call-with-Ries-2026-10-28 — summary.md`), the talk of Ries and Thijs on
27-Oct-2026, part 3 (`Call-with-Ries-2026-10-27 (3) — summary.md`), a read-only check of
production and a replay on crmain, both on 28-Oct-2026. This plan replaces the old draft "Phase
5: Recurring Services & Scheduling Mechanism" in radar
`crewradar-hr/references/implementation_plan.md` (a cron over a domain of records, model
`riverflow.recurring.service.rule`), which was never built.

## Why

Some work comes back every week, month or year and belongs to no employee, ship or log entry.
Today one such service exists: the Weekly Work Permit Issue Request of Cuneus HR, the weekly
round at the Ausländerbehörde (AB), on the company Cuneus Duisburg GmbH (radar
`crewradar-cuneus/references/weekly-work-permit-request.md`). Each week is a new service, so
that the old weeks stay out of the way and HR can look back at what it asked the AB for in a
given week. When a service reaches Done, a stored compute makes the service of the next week.

That design has three faults, and the check of 28-Oct-2026 showed all three:

1. **The chain stops without a message.** Two closes before the service's Monday were enough
   (see [The finding](#the-finding-on-production-28-oct-2026)).
2. **Nobody can see the recurring work.** No screen lists it. The service hangs on the company,
   so it has no New button either.
3. **Only the dev team can repair it.** HR cannot make a new service.

Ries asks for a name for the concept in the system, a section in the manual, and a home: in the
Services app a top-level menu Recurring Services, with New Recurring Service and a list of all
recurring services. "Then the concept has a peg and a home" (Ries, 28-Oct-2026). An audit log
filter on that screen is a later idea.

## The finding on production (28-Oct-2026)

Nobody deleted a weekly service. The audit log (`oteny_audit_log`) has no delete of a service
with "Weekly" in its snapshot, and all 10 services since 7-Jul-2026 exist, all in Done, all on
Cuneus Duisburg GmbH (`res.company` 1), all with the Permits tag (id 5) and the Human Resources
team (id 9).

| Service | Monday (deadline) | Made | Closed to Done (by Kerstin) |
|---|---|---|---|
| 29491 | 24-Jul | 7-Jul, by the install seed | 29-Jul, after the pickup |
| 31484 | 5-Aug | 29-Jul, on close of 29491 | 5-Aug, after the pickup |
| 32478 | 10-Aug | 5-Aug, on close of 31484 | 10-Aug, Nothing this week |
| 32800 | 26-Aug | 10-Aug, on close of 32478 | 27-Aug, from List Submitted |
| 33810 | 31-Aug | 27-Aug, on close of 32800 | 14-Sep, from List Submitted |
| 35060 | 22-Sep | 14-Sep, on close of 33810 | 23-Sep, after the pickup |
| 35935 | 28-Sep | 23-Sep, on close of 35060 | 15-Oct, Nothing this week |
| 38370 | 20-Oct | 15-Oct, on close of 35935 | 21-Oct, after the pickup |
| 38857 | 26-Oct | 21-Oct, on close of 38370 | Fri 23-Oct 09:29, Nothing this week |
| 39011 | 26-Oct | 23-Oct 09:29, on close of 38857 | Fri 23-Oct 10:10, Nothing this week |

Why the chain stopped after 39011. The fault is in the system, not in the use: the workflow
offers "Nothing this week" from Not Started at any time, and closing a week with nothing to
collect before its Monday is a normal use of it.

- **38857 made 39011 for the same week.** `_spawn_next_weekly_request()` dates the next service
  with `_next_monday()`, which counts from today, not from the deadline of the closed service. A
  service for Monday 26-Oct that is closed on Friday 23-Oct therefore makes a second service for
  Monday 26-Oct, not one for 2-Nov. To the user, that second service looks like a left-over of
  the week just closed, so closing it too is the natural next click.
- **39011 made nothing.** The duplicate check skips the create when any weekly service
  (`active_test=False`, end states included) already has `project_deadline` = next Monday.
  When 39011 closed, 38857 had 26-Oct, so no service was made and no message was shown.
- **Replayed on crmain, 28-Oct-2026** (rolled back): the two closes of 23-Oct through the
  "Nothing this week" wizard, with the clock at 23-Oct, end with no open service. The same fault
  shows by hand today: on 39011 click Back and Nothing to upload (a service for Monday 2-Nov
  appears), click Nothing this week on it (a second service for Monday 2-Nov appears), and click
  Nothing this week on that one (no service appears). A single close on a day after the
  service's Monday, as on 27-Oct, works, which is why the fault does not show in a simple test.
- **The click of 27-Oct was not on production.** The production audit log has no change of 38857
  or 39011 after 23-Oct 10:10 and no weekly service for 2-Nov. On 28-Oct production has no open
  weekly service.
- **The Radar shortcut was not the gap.** HR's own Radar shortcut Permits (`ir.filters` 116,
  made by a user, not shipped: Human Resources team, not in an end state, tags AB or Permits)
  shows an open weekly service. On 27-Oct there was none to show.

## What exists today

- **Recreate-on-Done** ([SKILL.md, "Recreate-on-Done Without a Cron"](../SKILL.md)): the stored
  compute `weekly_spawn_trigger`, `_spawn_next_weekly_request()`, `_weekly_service_vals()`,
  `_next_monday()` and the context flag `skip_weekly_spawn` in `crewradar_cuneus_sign`, and the
  seed `hooks._seed_weekly_permit_request`.
- **The transition wizard** (`riverflow.transition.wizard` → `riverflow.service.wizard`): every
  transition screen, the default action included, inherits from it. The weekly bulk upload
  wizard (`cuneus.weekly.bulk.upload.wizard`) inherits `riverflow.service.wizard`.
- **Single-open heal** (`enforce_single_open` on the workflow): a deleted or archived open
  service of such a workflow comes back through the auto-add. It is off for the weekly workflow
  (`riverflow_workflow` 39 on production).
- **No read-only rule.** riverflow has no rule today that makes a service read-only in a state.

## Design (draft)

Thijs's redesign of 28-Oct-2026: **one service that loops, not a new service per period.** The
recurring service never reaches an end state. When HR finishes a week, the closing transition
leads back to the first state and moves the deadline to the next week. This revises the reason
for one service per week (the old weeks out of the way, each week its own record): the history
of a week is now in the chatter of the one service. The Monday e-mail with the pickup list is
posted there by the e-mail sender (`message_post`), and the bulk upload posts its per-file
summary there (`_post_bulk_chatter`). With no new services, there is no chain that can stop and
no duplicate week.

### Part A, generic, in riverflow

**A1. A flag on the workflow, no mixin (Thijs, 28-Oct-2026).** The workflow gets the Boolean
**Recurring** (`is_recurring`). A service of such a workflow is a **Recurring Service**; its
workflow has no end state, so the service stays open and goes around once per period. The rule
for the next deadline is code in the transition screen, and its settings are keys in the
transition's `action_context` (A2).

**A2. The deadline goes to the next given weekday after the current deadline.** The deadline
moves twice a week: Monday (send the request) → Friday (the pickup) → next Monday (Thijs,
28-Oct-2026).

*How a transition sets the deadline, in plain terms.* Each transition of the service carries a
deadline key in its `action_context`, in the style of `followup_in_days`. When HR clicks the
transition, its screen shows the deadline field. HR can change the date before Save (a public
holiday, a closed AB). On Save the service gets the date on the screen. HR can also change the
deadline of the service at any time after the transition. There are two keys:

- `next_deadline_weekday`, with a weekday name: the screen proposes the first such weekday
  strictly after the service's current deadline, never counted from today;
- `ask_deadline`: the screen shows the deadline field empty and required, with no proposal, and
  HR enters the date (Thijs, 28-Oct-2026).

| Transition | Key | Current deadline | Proposed |
|---|---|---|---|
| Send Pickup Request | `{'next_deadline_weekday': 'friday'}` | Mon 26-Oct | Fri 30-Oct |
| Upload Permits, Nothing to Upload (from Awaiting Pickup) | `{'next_deadline_weekday': 'monday'}` | Fri 30-Oct | Mon 2-Nov |
| Upload Permits, Nothing to Upload (from Awaiting Pickup Request) | `{'next_deadline_weekday': 'monday'}` | Mon 26-Oct | Mon 2-Nov |
| Back | `{'ask_deadline': True}` | Fri 30-Oct | None: HR enters the date |

Because "after" is strictly after, a Monday deadline with `'monday'` gives the Monday a week
later, so a week without an e-mail moves on. A deadline that HR has moved by hand within the week
still leads to the right days (Tue 27-Oct gives Fri 30-Oct, and then Mon 2-Nov). A week that HR
finishes early goes to the next Monday all the same (Mon 26-Oct, clicked on Fri 23-Oct, gives
Mon 2-Nov). Back follows a mis-click, so no rule knows the right date: HR enters it. A week that HR finishes weeks late gets a proposal in the past, and HR changes
it by hand; HR keeps the service up to date (Thijs, 28-Oct-2026).

*Where it lives.* Both keys are in `STEP_DEADLINE_KEYS` (`riverflow/models/riverflow_state.py`),
so the target state's Deadline on Entry setting does not overwrite them, and `action_save()` of
`riverflow.transition.wizard` writes the date as a fixed date, as for `followup_in_days`.
`riverflow.service.wizard` computes the proposal in its deadline field, or leaves the field
empty and required for `ask_deadline`. The three screens of the
service inherit it: the default action screen, the e-mail sender
(`riverflow.service.email.sender.wizard`, field Follow-up deadline) and the bulk upload wizard
(`cuneus.weekly.bulk.upload.wizard`). With one of the keys on the transition, the e-mail sender
shows its Follow-up deadline field and writes the date on the screen.

**A3. People can delete a recurring service.** No delete rule. When the service is gone, a user
starts a new one with New Recurring Service (A4); that is the repair that needed the dev team on
27-Oct. The workflow does not get `enforce_single_open`, because that field refuses the delete
of a service past its first state (`_single_open_removal_blocked`).

**A4. The Services app menu.** Two new top-level menus, the same structure for both:

| Top-level menu | Item | Action |
|---|---|---|
| Services | (as today) | `riverflow.service_action` |
| ➕ New Service | (as today) | `riverflow.action_riverflow_start_service` |
| **Recurring** | **Recurring Services** | New action on `riverflow.service`, domain `workflow_id.is_recurring` = True and not a template; shows state and deadline. |
| | **➕ New Recurring Service** | New action on `riverflow.start.service`, like `action_riverflow_start_template`, with the context key `recurring_only`. |
| **Templates** | **Service Templates** | `riverflow.action_service_templates`, moved out of Configuration. |
| | **➕ New Template** | `riverflow.action_riverflow_start_template`, moved out of Configuration. |
| Configuration | Tags, Workflows, States, Transitions, … | (as today, without the two template items) |

The menu ids `riverflow.services_menu_configuration_templates` and
`riverflow.service_menu_start_template` keep their ids and get the new parent. The audit log
filter on the Recurring Services list is a later idea (Ries, 28-Oct-2026). Every user who may start
a service (`group_service_writer`) may use New Recurring Service, so HR can repair a missing
service (Thijs, 28-Oct-2026).

**The New Recurring Service screen.** The same start transition screen as New Service and New
Template (`riverflow.start.service`). With `recurring_only` in the context,
`_default_start_transition_ids()` keeps only the start transitions of workflows with
`is_recurring`, and `_add_template_start_transitions()` keeps only the templates of those
workflows, the same as New Service does for all workflows (Thijs, 28-Oct-2026).

**A recurring service has no subject (Thijs, 28-Oct-2026).** A service may have no
`res_model`/`res_id`, and a recurring service does not use one. Its company follows the rule for
a service without a subject: the company the user has selected (`self.env.company`,
[service-company.md](../references/service-company.md), rule 4).

**A5. The name in the manual.** "Recurring Service" goes in the glossary (radar
`crewradar-intro/references/glossary.md`). After the build, `riverflow/references/recurring-services.md`
is the manual section, and the riverflow `SKILL.md` section "Recreate-on-Done Without a Cron" is
replaced by a one-line pointer to it.

### Part B, the weekly AB service, in crewradar_cuneus_sign

**B1. A new name, without "Weekly" (Thijs, 28-Oct-2026).** The period is in the deadline, not in
the name. The name is **Collect Work Permits at AB** (Thijs, 28-Oct-2026). It follows the
verb-and-object names of the other workflows (Collect Rijnvaartverklaring, Arrange Work Permit),
names the result of the loop (the cards in Odoo), uses the glossary term Work Permit, and the
plural marks the batch, apart from each man's Arrange Work Permit. The name changes on the
workflow, the service and the skills. The e-mail template keeps its name and subject "Wöchentliche
Abholliste Aufenthaltstitel": the AB reads it, the e-mail still goes once a week, and
"Abholliste" already says collection list. No user-facing name of the
workflow, its states or its transitions has "Weekly" in it. The technical ids keep it
(`workflow_weekly_permit_request`, `trans_weekly_*`, `email_template_wp_weekly_request`,
`weekly_pickup_list_html`), because a rename of an xmlid costs a migration and the user does not
see it.

**B2. Two states, no end state (names: Thijs, 28-Oct-2026).**

| State | Was | Meaning |
|---|---|---|
| Awaiting Pickup Request | Not Started | The week's Monday e-mail to the AB is not sent yet. |
| Awaiting Pickup | Send list, List Submitted, Permits Picked up | The e-mail went to the AB; HR waits for the pickup. |

Done goes. "Went Friday" was one step too many (Ries, 27-Oct-2026).

**B3. The transitions.**

| From | Transition | To | Screen | Proposed deadline (A2) |
|---|---|---|---|---|
| Awaiting Pickup Request | Send Pickup Request | Awaiting Pickup | The e-mail sender with the pickup list, as today. | Friday of this week |
| Awaiting Pickup Request | Upload Permits | Awaiting Pickup Request | The bulk upload wizard, for a week without an e-mail. | Monday of next week |
| Awaiting Pickup Request | Nothing to Upload | Awaiting Pickup Request | Default action, for a week without an e-mail. | Monday of next week |
| Awaiting Pickup | Upload Permits | Awaiting Pickup Request | The bulk upload wizard. | Monday of next week |
| Awaiting Pickup | Nothing to Upload | Awaiting Pickup Request | Default action. | Monday of next week |
| Awaiting Pickup | Back | Awaiting Pickup Request | Default action, for a mis-click on Send Pickup Request. | None: HR enters the date (`ask_deadline`) |

(Thijs, 28-Oct-2026.)

**B4. The weekly period.** The transitions carry the keys of B3; the workflow gets
`is_recurring`. The pickup day Friday is the same day as `_WP_FRIDAY`. Removed: `weekly_spawn_trigger`, `_spawn_next_weekly_request()`,
`_weekly_service_vals()`, `_next_monday()`, `skip_weekly_spawn` and the seed hook (the start
transition and New Recurring Service make the first service). The pickup list
(`weekly_pickup_list_html`), the bulk upload wizard and the cadence constants
`_WP_PICKUP_LEAD_DAYS` and `_WP_FRIDAY` (also used by the per-employee follow-up deadline) stay.

**B5. Migration.** `reset_workflow_to_xml()`. The migration deletes the 10 services in Done
(29491 to 39011; Thijs, 28-Oct-2026: they are mostly empty). On crmain (28-Oct-2026) nothing
else points at them: no child services, no credential, no attachment; only their tag rows and
Radar rows, which go with them. Seven have no message. Three hold the history of a week: the
Monday e-mail to the AB and the bulk upload summary (31484, 5-Aug: 3 permits; 35060, 23-Sep: 7
permits; 38370, 21-Oct: 5 permits), and 29491 has the note "sent 15.07.2026". The credentials
that those uploads made are not linked to the services and stay. Before the delete, the
migration moves these seven messages to the chatter of the new service (Thijs, 28-Oct-2026): the
e-mail is HR's legal request for the prolongation (Kirsten, 22-Oct-2026), and the history of each
week belongs in the chatter of the one service. The migration ends with exactly one open
service, with no subject and the company Cuneus Duisburg GmbH (`res.company` 1), in Awaiting
Pickup Request, deadline the next Monday.

**B6. The Services tab of the company and the Company button of the service go (Thijs,
28-Oct-2026).** It was built for the weekly
service only (commit `67a2d61a2`, 21-Oct-2026, BC 10426833280, on `main`). With no subject, no
service hangs on a company: on production the 10 weekly services are the only services with
`res_model` = `res.company`, and the migration deletes them. Removed from `crewradar`: the page
`services` in `views/res_company_view.xml`, and `service_ids`, `service_list_button_caption`,
`_compute_service_ids()`, `_compute_service_list_button_caption()` and
`action_view_company_services()` in `models/res_company.py`, with the tests
`test_company_lists_only_services_on_it` and `test_services_tab_is_first_on_the_company_form`.
The Company button at the top of the service form, from the same commit, goes too: it shows only
for a service on a company, and no such service is left. Removed: `subject_company_id`,
`_compute_subject_company_id()` and `action_view_subject_company()` in
`crewradar/models/riverflow_service.py`, the button in `crewradar/views/riverflow_service_views.xml`,
and with the tests above the whole file `crewradar/tests/test_service_company_button.py`.

**B7. A manual for HR (Thijs, 28-Oct-2026).** A user-facing document for HR on how the service
works, written after the build from what is built: radar
`crewradar-cuneus/references/collect-work-permits-at-ab.md`, audience Key Users, with a line in
the reference list of the crewradar-cuneus `SKILL.md`. It follows the HR runbooks there (for
example `mfnl-runbook.md`): short, in HR's words, every button by its label. It covers:

- what the service is for: one service, Collect Work Permits at AB, that comes back every week;
- where HR finds it: the Radar shortcut Permits, and Services → Recurring → Recurring Services;
- the week: Monday Send Pickup Request (the e-mail with the pickup list), Friday the pickup, then
  Upload Permits or Nothing to Upload; a week without an e-mail; Back after a mis-click;
- the deadline that each button proposes, and that HR can change it on the screen (a public
  holiday, a closed AB, a week finished late);
- where the history of a week is: the e-mails and the upload summaries in the chatter;
- what to do when the service is gone: Services → Recurring → ➕ New Recurring Service.

The technical reference `crewradar-cuneus/references/weekly-work-permit-request.md` stays for
the Radar dev team and the agents; it is rewritten for the built design in the same step and
points to the manual, as `mfnl-workflow.md` and `mfnl-runbook.md` point to each other.

## Build steps and checks

No interim fix of the old spawn code: the Radar dev team builds the whole redesign and tests it
before it goes to production (Thijs, 28-Oct-2026).

1. Part A in riverflow. Tests: `next_deadline_weekday` proposes the first such weekday strictly
   after the current deadline, for a deadline on every day of the week and for a click days
   before or after the deadline; two finished weeks in a row move the deadline two weeks, never
   twice to the same week; `ask_deadline` shows an empty, required deadline field and writes the
   date that HR enters; a date that HR types on the screen wins; the target state's deadline
   setting (release R5c) does not overwrite the key; `followup_in_days` still counts from today;
   the e-mail sender writes the proposed Friday, not tomorrow, and still writes tomorrow for a
   transition without the key. New
   Recurring Service shows only the start transitions and templates of recurring workflows; New
   Service and New Template show what they show today. In the browser: the menus Recurring and
   Templates, and Configuration without the template items. Check what in riverflow and
   crewradar expects a service to reach an end state at some time (the Radar, the parent and
   child rules, the reports), because this is the first workflow without one.
2. Part B in crewradar_cuneus_sign, with the migration. Tests: the two states and the
   transitions; Send Pickup Request moves Monday to Friday; Upload Permits and Nothing to Upload
   from both states move to the next Monday; Back asks for the deadline; the company form has no Services tab, and the service form has
   no Company button.
   On a copy of crmain: after the migration exactly one open service exists, it carries the
   seven messages of 29491, 31484, 35060 and 38370, the 10 old services are gone, and the replay of
   23-Oct (two weeks finished early) moves it to 2-Nov and then 9-Nov.
3. Skills: the HR manual (B7), the technical reference rewritten for the built design, the
   riverflow manual section (A5), the glossary, a release note and Basecamp. Check: HR can do a
   full week with the manual alone, on crmain.

## Outcome (29-Oct-2026)

Built on `feat/recurring-services` in both repositories, not merged and not deployed. otenydoo:
riverflow 19.0.1.1381 (`495bd49`, A1 and A4) and 19.0.1.1382 (`28d0b06`, `cdebc7e`, A2), tests
`65f284d`. radar: crewradar_cuneus_sign 19.0.6.292 (`16ef786f8`, `ae53ad428`, part B), crewradar
19.0.11.89 (`e72d9312c`, B6), tests `50ca858ff`. The radar pin of otenydoo still points at
`1abd937`; it moves with the merge (`commit-with-otenydoo`).

### What was built, per part

- **A1.** `riverflow.workflow.is_recurring` (label Recurring), on the workflow form under
  Enforce Single Open Service. Nothing checks that a recurring workflow has no end state.
- **A2.** Both keys are in `STEP_DEADLINE_KEYS`. `riverflow.transition.wizard` has the helper
  `_next_weekday_deadline(record)` (constant `WEEKDAYS`, lower-case English names) and a branch in
  the deadline chain of `action_save`, after `followup_in_days`, that writes the screen date as a
  fixed date (`self`, 0 relative days). `riverflow.service.wizard.default_get_using_records`
  proposes the date, or for `ask_deadline` shows the field empty with the new wizard Boolean
  `project_deadline_required`, which drives `required` in the views of the service wizard and the
  e-mail sender. Decided in the build: a service without a deadline (a start transition) counts
  from today; an unknown weekday name raises a UserError that names the key; a cleared date with
  `next_deadline_weekday` gets the proposal; an empty date with `ask_deadline` is refused with
  "Enter the deadline for this transition."; the proposal reads the stored `project_deadline`,
  not the effective `deadline`.
- **A3.** No code: no delete rule and no `enforce_single_open` on the workflow.
- **A4.** Actions `riverflow.action_recurring_services` (path `recurring-services`, list, form
  and calendar) and `riverflow.action_riverflow_start_recurring_service` (path
  `start-recurring-service`, context `{'recurring_only': True}`). Menus under Services: Services
  (10), ➕ New Service (30), Recurring (40: Recurring Services, ➕ New Recurring Service), Templates
  (50: Service Templates, ➕ New Template), Configuration (moved from 20 to 90, so the order is the
  order of the A4 table). The New Recurring Service menu item has `group_service_writer`; its
  action has no group, as New Template.
- **A5.** [recurring-services.md](../references/recurring-services.md); the SKILL.md section
  "Recreate-on-Done Without a Cron" is a pointer to it.
- **B1–B4.** As designed. Decided in the build: the start transition is named after its target
  state, Awaiting Pickup Request, as the other workflows name theirs, so the New Recurring Service
  button reads "Collect Work Permits at AB - Awaiting Pickup Request". It carries
  `next_deadline_weekday: 'monday'` (counted from today), `default_name` and the Permits tag as
  `default_tag_ids`; riverflow resolves an xmlid in `action_context` only for a Many2one
  (`_id_ref`), so the XML writes the tag's database id with `ref()` when the file loads. The new
  state is `state_weekly_awaiting_pickup`; Awaiting Pickup Request keeps `state_weekly_not_started`;
  Upload Permits from Awaiting Pickup Request is the new `trans_weekly_bulk_upload_from_not_started`.
  The bulk upload wizard shows the deadline when the transition has `next_deadline_weekday`, and
  its texts say Upload Permits. The label of `weekly_pickup_list_html` is Pickup List. A fresh
  install has no service: the seed is gone, and New Recurring Service makes it.
- **B5.** `crewradar_cuneus_sign/migrations/19.0.6.292/post-migrate.py`: `reset_workflow_to_xml()`,
  then always a new service (no subject, Awaiting Pickup Request, Human Resources, Permits,
  `base.main_company`, the first Monday after the day of the upgrade); the messages of every
  existing service of the workflow, with their attachments, move to it; the existing services are
  deleted. The old migration 19.0.5.19 no longer calls the removed seed, so it still imports.
- **B6.** As designed, plus `crewradar/migrations/19.0.11.89/pre-migrate.py` (finding 4 below).
- **End states.** This is the first workflow without an end state. Every use of `is_end_state` and
  `is_open` in riverflow, crewradar and crewradar_cuneus_sign was read: nothing breaks a service
  without a subject, a parent or child services. `is_open` stays True, the Radar row stays and turns
  red when the deadline is past, and every transition screen shows the Responsible field (it is
  hidden only on a transition into an end state).

### What the reviews found, and the fixes

1. **The start transition crashed on a cleared date.** The fallback of `next_deadline_weekday` in
   `action_save` read the first record of the batch (`recordsToTransition[:1]`). On a start
   transition that is a Python list with an in-memory record, so the save raised an
   AttributeError; on a bulk transition records 2 to n got the proposal of record 1. Fix
   `cdebc7e`: the record of the loop. Tests
   `test_a_start_transition_with_a_cleared_date_counts_from_today`,
   `test_each_record_of_a_bulk_transition_gets_its_own_proposal`.
2. **The migration kept an open weekly service as it was.** A service that HR makes by hand before
   the deploy, with the old start transition, has the old name and can have a subject and any
   deadline, so keeping it broke B1 and B5. Fix `ae53ad428`: the migration always makes the new
   service and treats every existing service as old.
3. **A test assumed one recurring workflow.**
   `test_start_screen_recurring_only_lists_recurring_workflows` asserted that New Recurring Service
   lists one workflow; it failed on a database with crewradar_cuneus_sign, where Collect Work
   Permits at AB is recurring too. Fix `65f284d`: `assertIn` and `assertNotIn`.
4. **A stale inherit view stopped the upgrade of crewradar** with "action_view_subject_company is
   not a valid action on riverflow.service". The stored view `crewradar.view_service_form_log`
   still held the removed button, and Odoo checks the combined form when the first view of the file
   loads, before it rewrites that view. Fix: the pre-migrate deletes the view
   (`_force_unlink=True`) and the upgrade creates it again from the XML. On production nothing
   inherits from that view or points to it.

### Proof

- **Tests on cr-test.** riverflow with `test_weekly_email_list`: 0 failed of 292.
  crewradar_cuneus_sign: 0 failed of 1718. crewradar: 3 failures of 4233, all in
  `test_employee_site_lifecycle`, caused by a stale state row `crewradar.state_site_archived` on
  cr-test; the class (tag `test_site_lifecycle`) passes, 21 tests, on a production restore with
  this branch.
- **Upgrade of a production restore.** A copy of `crmain_company_test` (production of 26-Oct-2026,
  below the branch), `-u riverflow,crewradar,crewradar_cuneus_sign`: no error or warning; exactly
  one service of the workflow (no subject, Awaiting Pickup Request, Monday 2-Nov-2026, company 1,
  `is_recurring` set), the 7 messages moved to it, the 10 old services gone, the log view created
  again.
- **Replay on a copy of crmain.** The migration made the service and deleted 12 old services: the
  10 of production plus 39083 and 39084, the replay by hand of 28-Oct on crmain. With 39084 set
  open first, as a stand-in for a service made by hand before the deploy, it was deleted too. Two
  Nothing to Upload in a row moved the deadline from 2-Nov to 9-Nov and 16-Nov. The test
  `test_two_weeks_finished_early_move_two_weeks` replays 23-Oct (2-Nov, then 9-Nov).
- **Browser check on crmain.** Services menu order Services, New Service, Recurring, Templates,
  Configuration; Recurring holds Recurring Services and New Recurring Service; New Recurring
  Service lists only "Collect Work Permits at AB - Awaiting Pickup Request"; the list shows the one
  service (Awaiting Pickup Request, 02-Nov-26, Permits, Human Resources); the Upload Permits screen
  proposes Project deadline 09-Nov-26 and the Send Pickup Request screen Follow-up deadline
  06-Nov-26. Both screens were closed without Save.

### Follow-ups

The open riverflow items are in the [roadmap](../references/roadmap.md): a key for a list of
xmlids in `action_context`, the end-state rules for a later recurring service with a subject or a
parent, and the date of a bulk transition. Also open:

- The check of build step 3: HR does a full week with the manual alone, on crmain.
- The merge of both branches, the radar pin, the Basecamp to-do and the release.
- The start transition's `default_tag_ids` and `default_name` are Odoo context defaults, so they
  apply to every record with a `tag_ids` or `name` field that the same save creates. Safe today;
  watch it when an auto-add is added to this workflow.
- The help text of `weekly_pickup_list_html` still says "the weekly AB request email".
- The audit log filter on the Recurring Services list stays a later idea (Ries, 28-Oct-2026).
