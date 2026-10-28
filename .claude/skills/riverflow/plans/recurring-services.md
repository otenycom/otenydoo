# Plan: recurring services

Status: design agreed with Thijs on 28-Oct-2026, not built: one service that loops.
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

**A2. The next deadline counts from the current deadline.** `riverflow.service.wizard`, the
screen of every default action, already reads the `action_context` key `followup_in_days` and
proposes the deadline today + N days in its Project deadline field. Beside it comes a second
key, `next_deadline_in_days`: the screen proposes the service's current deadline + N days. A
weekly transition has `{'next_deadline_in_days': 7}`, so a service with deadline Monday 26-Oct
gets Monday 2-Nov, whatever the day of the click. HR sees the date and can change it before
Save (a public holiday, a closed AB). The bulk upload wizard (`cuneus.weekly.bulk.upload.wizard`)
inherits `riverflow.service.wizard`, so it gets the same proposal without its own code. No roll
forward: a week finished weeks late proposes a deadline in the past, and HR changes it by hand.
HR keeps the service up to date, as it has done in the last weeks (Thijs, 28-Oct-2026).

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

**B2. Two states, no end state.**

| State | Was | Meaning |
|---|---|---|
| Await Pickup Request | Not Started | The week's Monday e-mail to the AB is not sent yet. |
| Awaiting Permits | Send list, List Submitted, Permits Picked up | The e-mail went to the AB; HR waits for the pickup. |

Done goes. "Went Friday" was one step too many (Ries, 27-Oct-2026).

**B3. The transitions.**

| Transition | From → to | Screen | `next_deadline_in_days` |
|---|---|---|---|
| Send Pickup Request | Await Pickup Request → Awaiting Permits | The e-mail sender with the pickup list, as today. | — |
| Upload Permits | Awaiting Permits → Await Pickup Request | The bulk upload wizard, with the proposed next deadline. | 7 |
| Nothing to Upload | Awaiting Permits → Await Pickup Request | Default action, with the proposed next deadline. | 7 |
| Upload Permits, Nothing to Upload | Await Pickup Request → Await Pickup Request | The same two screens, for a week without an e-mail. | 7 |
| Back | Awaiting Permits → Await Pickup Request | Default action, for a mis-click on Send Pickup Request. The deadline stays. | — |

(Thijs, 28-Oct-2026.)

**B4. The weekly period.** Upload Permits and Nothing to Upload carry
`{'next_deadline_in_days': 7}`; the workflow gets `is_recurring`. Removed: `weekly_spawn_trigger`, `_spawn_next_weekly_request()`,
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
service, with no subject and the company Cuneus Duisburg GmbH (`res.company` 1), in Await
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

## Build steps and checks

No interim fix of the old spawn code: the Radar dev team builds the whole redesign and tests it
before it goes to production (Thijs, 28-Oct-2026).

1. Part A in riverflow. Tests: `next_deadline_in_days` proposes the current deadline + N days,
   also when the click is days before or after that deadline; two finished weeks in a row move
   the deadline two weeks, never twice to the same week; `followup_in_days` still counts from
   today. The screen also proposes the target state's deadline setting (release R5c) unless the
   transition has its own deadline key; `next_deadline_in_days` must count as such a key. New
   Recurring Service shows only the start transitions and templates of recurring workflows; New
   Service and New Template show what they show today. In the browser: the menus Recurring and
   Templates, and Configuration without the template items. Check what in riverflow and
   crewradar expects a service to reach an end state at some time (the Radar, the parent and
   child rules, the reports), because this is the first workflow without one.
2. Part B in crewradar_cuneus_sign, with the migration. Tests: the two states and the
   transitions; Upload Permits and Nothing to Upload from both states move the deadline one week;
   Back keeps the deadline; the company form has no Services tab, and the service form has no
   Company button.
   On a copy of crmain: after the migration exactly one open service exists, it carries the
   seven messages of 29491, 31484, 35060 and 38370, the 10 old services are gone, and the replay of
   23-Oct (two weeks finished early) moves it to 2-Nov and then 9-Nov.
3. Skills, glossary, release note and Basecamp.
