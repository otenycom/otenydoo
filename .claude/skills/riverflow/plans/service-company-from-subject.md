# Plan: the company of a service comes from its subject

Status: built. Design agreed with Thijs on 26-Oct-2026 and 27-Oct-2026; built on the branches
`feat/service-company-from-subject` and merged on 27-Oct-2026 (otenydoo PR #5 into `main`, radar
PR #8 into `dev`), not deployed yet. riverflow 19.0.1.1374, crewradar 19.0.11.82. See the
outcome at the end; the rule as it works now is in
[service-company.md](../references/service-company.md).

## Why

A service on a log entry of a Cuneus Duisburg GmbH employee can land on Cuneus Services Pte. Ltd.
Example on production: services 26477/26478 (log entry 8987) and 38358/38359 (log entry 10793),
date-change review services that Dolf created on 03-Jun-2026 and 15-Oct-2026 while Cuneus
Services was the active company in his company switcher. The audit log shows the company was set
at creation and never changed.

The company of a service matters: the billing wizard selects services by `service.company_id`
(`crewradar_billing_wizard_services.py`), the AUV, MFNL, AVB and other letters and the service
mails use it as the sender company, and the multi-company rule shows a service only to users who
have its company active.

The cause: the log entry computes its company from the employee
(`crewradar_log_entry.py` `_compute_company_id`, depends on `employee_id.company_id`), and a
credential from its holder. The service has no such compute.
`riverflow_service.py` `_compute_company_id` follows only the supply order of an info service; every
other service keeps the company it has, or takes `self.env.company`. So the company of a new
service comes from one of these sources today:

1. An info service of a supply order (`crewradar_service_leg_pax.py`, one per passenger on a
   leg) gets the company of its supply order.
2. A service from a template gets the template's company (`riverflow_service.py`, the create
   from a template). All 59 templates on production are on Cuneus Duisburg GmbH.
3. Add Service on a parent service passes the parent's company (`default_company_id`).
4. Add Service on a log entry, an employee or a ship passes the user's active company
   (`crewradar_log_entry.py`, `hr_employee.py`, `crewradar_site.py`, `default_company_id`).
5. Every other create (auto-add services without a template, the date-change review services)
   gets the user's active company from the compute.

On production, 3,810 services on Cuneus Services log entries are on Cuneus Duisburg GmbH (1,645
made by hand, 2,157 by auto-add services or templates). No child service has a different company
from its parent.

## Cross-check with the service data on crmain (restore of 25-Oct-2026)

The company each service has today, against the company the rule below would give it (templates
left out):

| Source | Services | Differ under the rule |
| --- | --- | --- |
| Root service on a log entry | 25,587 | 3,040 (418 open) |
| Child service | 6,764 | 2 |
| Info service | 3,210 | 0 |
| Root service on an employee | 1,552 | 1 |
| Standalone | 55 | 0 |
| On a ship | 32 | 0 |
| On a company (`res.company`) | 10 | 0 |

What the data shows:

- **Services on Cuneus Services log entries are on Cuneus Duisburg GmbH.** 3,014 of 3,016 root
  services: Operations NL 2,957, Human Resources 45, Finance Team 10, Sales Team 2. Of these, 2,156
  came from auto-add services and 235 are supply orders; none is included in an invoice. This is
  the user's active company at work, not a practice: Operations NL, Sales and Planning handle both
  companies (Thijs, 26-Oct-2026), and the users who created these services had Duisburg as the
  first selected company at that moment (and Cuneus Services selected too, see Q8).
  Cuneus Services has no accounting configuration (crewradar-salary `employee-eligibility.md`).
- **The company of a team says nothing about the company of its services** (Thijs, 26-Oct-2026).
  Operations NL, Sales and Planning handle both companies; Human Resources and Finance handle only
  Cuneus Duisburg GmbH. The team's company is no source for the rule.
- **HR and Finance services on Cuneus Services entries are anomalies.** The 45 HR services are all
  closed and date from Sep to Dec 2025: Duisburg auto-add services (Send MFNL Posted Workers
  Contract, Send AUV + WA, Arrange A1, Check Permits) and HR date-change review services, which the
  code creates only for Duisburg entries. So the entries were probably on Duisburg then; the
  production audit log starts on 28-Apr-2026 and cannot show it. The 10 Finance services are
  closed supply orders and a Sign-Off Journey Invoicing, made by hand.
- **The wrong cases on Duisburg entries are user mistakes.** 18 root services are on Cuneus
  Netherlands B.V. and 5 on Cuneus Services, made by hand or by the date-change review while the
  user had another company active (Dolf, Pepijn, Kasin). The open ones include 26478 and 38359.
- **Log entries rarely change company after creation.** In the production audit log (since
  28-Apr-2026), 1,017 of the 1,098 company changes of log entries are the first value, written in
  the same transaction as the creation. The other 81: 74 times an entry got a company when an employee
  was assigned, 7 times an entry lost it when the employee was removed. No entry went from one company directly to
  another, and no employee changed company. This matters for Q5.
- **Child services:** the 2 children that differ from their parent (27101, 27102, A1 for Moreno)
  have a parent without a company, 9724 (Arrange A1 Application, auto-added on 12-Nov-2025); it is
  the only service on crmain without a company. 762 children on Cuneus Services entries are on
  Duisburg, like their parent (332 info services, 324 supply orders, 105 other children on the
  parent's entry); all are closed. The rule gives them the parent's company, so they follow the
  root.
- **Auto-added services take the template's company.** All 2,156 auto-added services on Cuneus
  Services entries come from a template on Cuneus Duisburg GmbH; 414 are open, the latest from
  23-Oct-2026 (Send Sign OFF Aftercare, Arrange Sign OFF transport, Book Flight OFF).
- **Employee services:** service 5060 (Arrange Work Permit at AB, Human Resources) of a Cuneus
  Services employee is on Cuneus Duisburg GmbH.
- **The access rule is the only company filter.** The Radar has no own company domain; a service is
  visible when its company is selected in the user's company switcher. The selection lives in
  the browser (cookie `cids`), so the data cannot show who has which companies selected. HR
  reported on 22-Oct-2026 that Cuneus Services crew is invisible with only Duisburg selected
  (crewradar-cuneus review meeting of 22-Oct-2026). `hr.otenybot` (Barney) has only Cuneus
  Duisburg GmbH.

## The rule (proposal of Thijs, 26-Oct-2026)

- A standalone service (no subject, no parent) gets the user's active company.
- A service with a parent or a subject gets the company of the parent, or else of the subject. A
  service on a log entry is handled in the context of the log entry's company, which is the
  employer of the employee.
- The service keeps following: when the parent's or the subject's company changes, the service
  changes too, as a log entry follows its employee. One build does it all (Thijs, 26-Oct-2026); an
  earlier draft had a first phase for new services only and a second phase for the rest.

## Decisions

**The order, first match wins** (Thijs, 26-Oct-2026):

1. An info service: the company of its supply order (Q7).
2. A child service: the company of its parent (Q4).
3. A service with a subject: the subject's company.
4. A new service without any of these: the company that the user has selected (Q3). An existing
   service without any of these keeps the company it has.

**Q1. Supply orders follow the same order as every other service** (Thijs, 26-Oct-2026). Odoo does
no bookkeeping of supply orders today: the costs of flights, taxis, trains and buses are booked
in Cuneus Netherlands B.V., outside Odoo. So the company of a supply order does not decide who
pays the supplier. The code comment in `_compute_company_id` (the user's company pays the supplier
and invoices the employee's company) does not describe the practice and goes.

**Q2. The template's company no longer decides** (follows from the order): it is neither the parent
nor the subject. A template's company only says for which company the template is visible.

**Q3. A subject without a company** (Thijs, 26-Oct-2026 and 27-Oct-2026). A new service gets the
company that the user has selected, as a standalone service does; an existing service whose
subject loses its company keeps the company it has. On crmain, 409 active log entries have no
company, all because they have no employee: Gap 248, Not Needed 119, Plan Employee 32, Standby
Away 3, Project Planned 2, Project Completed 5. Only the project entries have services (114, 7 of
them open); the Gap, Not Needed and Plan Employee entries have none. A ship (`crewradar.site`)
has no company field; 32 services are on ships. Every employee has a company. When planning
assigns an employee to an entry, its services follow the employee's company.

**Q4. The parent wins over the subject** (follows from the order), so one service tree has one
company. A child can have another subject than its parent (the AB Appointment marker sits on a
log entry under a work permit service on the employee).

**Q5. The company is live, for every service** (Thijs, 26-Oct-2026 and 27-Oct-2026). Open and
closed services follow a change of the parent's or the subject's company, also invoiced services
and services paid on a salary. A guard that kept invoiced and paid services on their company was
dropped (27-Oct-2026): the log entry itself already moves with its employee, invoiced or not, so
the service follows the same behaviour as its log entry, and the case has not happened. On crmain,
1,200 services are invoiced and 2,278 paid on a salary; none of the services that the rule moves
is invoiced or paid. The production audit log (since 28-Apr-2026) shows no employee that changed
company and no log entry that went from one company directly to another; 74 times an entry got a
company when an employee was assigned, and 7 times an entry lost it. The compute always wins, so a
company chosen by hand does not stay; every company on crmain that differs from the rule is a
mistake.

**Q6. Existing data is corrected by the upgrade.** The migration recomputes the company of every
service once (about 37,000 on crmain); 3,836 services move, 418 of them open: 3,812 from Cuneus
Duisburg GmbH to Cuneus Services, the rest from Cuneus Netherlands or Cuneus Services back to
Duisburg (services 26478 and 38359 among them). The state records follow their service. The
upgrade is a deploy, so the Radar dev team approves it as every release; no other write to
production. `hr.otenybot` (Barney) keeps only Cuneus Duisburg GmbH: the 3 MFNL services on Cuneus
Services entries are closed, so Barney has no work there.

**Q7. An info service keeps the company of its supply order** (Thijs, 26-Oct-2026), because this
works without faults today. One taxi can carry passengers of more than one company; the info
services of one ride stay on one company.

**Q8. Services on Cuneus Services entries move to Cuneus Services** (Thijs, 27-Oct-2026), new and
existing. A user who has only Duisburg selected in the company switcher then no longer sees them
on the Radar. Nobody can tell from the data who has which companies selected: the web client
keeps the selection in the browser cookie `cids` (Odoo `web/static/src/core/user.js`), and the
database and the audit log do not store it. The users who created the services on Cuneus Services
entries had Duisburg as the first selected company and Cuneus Services selected too, because the
access rule hides a Cuneus Services log entry otherwise. So a user who can see the log entry also
sees its services. HR handles only Duisburg; its report of 22-Oct-2026 (Cuneus Services crew
invisible on a ship) has its own fix, the DU / CS tag on the ship (BC 10431836007). The Radar dev team
tells Operations NL of the move before the release.

**The responsible team plays no part** (Thijs, 26-Oct-2026): Operations NL, Sales and Planning
handle both companies.

## Design

Riverflow (otenydoo) owns the order, in `riverflow_service.py` `_compute_company_id`:

- an info service (`supply_order_service_id`): the supply order's company, as today;
- else `parent_id.company_id`;
- else the subject's company from a new hook `_get_subject_company()`, which reads `company_id` of
  the `res_model`/`res_id` record when that model has the field (log entry, employee); a ship or a
  subject without a company returns nothing;
- else, for a new record, `self.env.company`; for an existing record, the company it has.

Dependencies: riverflow adds `parent_id.company_id` (the field is already `recursive=True`).
crewradar adds `log_entry_id.company_id` and `employee_id.company_id`, because a compute cannot
depend on the `res_model`/`res_id` pair. A service moved to another log entry follows too, because
`log_entry_id` is a stored compute of `res_model` and `res_id` (`crewradar/models/riverflow_service.py`). The inverse stays a flag method; the compute wins at the next
change.

The company passed at creation must not beat the order, so the build removes these sources:

- the create from a template no longer passes `template_service.company_id`
  (`riverflow_service.py`, the create from a template);
- Add Service on a log entry, an employee and a ship no longer passes
  `default_company_id: self.env.company.id` (`crewradar_log_entry.py`, `hr_employee.py`,
  `crewradar_site.py`). Such a context default also reaches every other service created in that
  context, for example by auto-add services.

Add Service on a parent service keeps passing the parent's company, and the salary timeline keeps
passing its own company: both agree with the order. The info service keeps passing its supply
order's company. The date-change review fix in `crewradar_log_entry_review.py` (commit 966e5eaa0)
becomes redundant; the build removes it and keeps its test.

Migration: a post-migrate in crewradar (the module that adds the subject dependencies) recomputes
`company_id` of every service, archived ones and templates too; the ORM computes a parent before
the child that reads it.

## Checks

- riverflow tests, each with another company selected than the expected one: a standalone service
  gets the user's selected company; a child gets its parent's company and follows when the parent
  changes company; a root service on a subject with a `company_id` gets it; a subject without one
  falls back to the user's company at creation; a child whose subject has another company than its
  parent gets the parent's company; a service from a template gets the subject's company, not the
  template's.
- crewradar tests: an auto-added service and a date-change review service on a log entry get the
  log entry's company; Add Service on a log entry, an employee and a ship gives the subject's
  company; an info service keeps its supply order's company when its passenger's entry is of
  another company; a service follows when the employee of its log entry changes company, and when
  an employee is assigned to an entry without one; a service keeps its company when the employee
  is removed from its entry; a closed and an invoiced service follow too.
- Flush in the environment with the other company selected: services created in a stored compute
  are created at flush (see the test `test_review_service_takes_company_of_log_entry`).
- The migration on a copy of crmain: 3,836 services move, the counts per company match the
  cross-check, and the state records follow.
- Run the riverflow and crewradar suites on cr-test.

## Outcome (27-Oct-2026)

Built as designed:

- riverflow `riverflow_service.py`: `_compute_company_id` takes the supply order of an info service,
  else `parent_id.company_id`, else `_get_subject_company()`, else keeps the company, or takes
  `self.env.company` for a service without one; it depends on `parent_id.company_id`, `res_model`
  and `res_id`. `_get_subject_company()` reads `company_id` of the subject with `sudo()`, because
  the subject decides also when the user has not selected its company. The create from a template
  no longer passes the template's company; the old comment on supply orders is gone.
- crewradar `riverflow_service.py`: an override adds `log_entry_id.company_id` and
  `employee_id.company_id` to the dependencies (Odoo merges the `@api.depends` of every override).
  Add Service on a log entry, an employee and a ship passes no `default_company_id`; the
  date-change review service passes no company (the fix of commit 966e5eaa0 is gone, its test
  stays).
- crewradar migration `19.0.11.82/post-migrate.py`: `env.add_to_compute()` on every service, then
  `modified(["company_id"])` on the moved ones, because `add_to_compute()` does not pass the change
  on: in the first run on the crmain copy all 3,841 moved services kept a state record on the old
  company.

What was learned:

- **The compute reads the old company from the database.** Odoo marks the field as computed and
  fetches the old value with `_read()` when the compute reads it. A service whose company is not
  yet written to the database (created in the same transaction) has no old company to keep, so a
  service created and moved to a subject without a company in one transaction gets the selected
  company. The tests write the service first, as a later request does.

Tests:

- riverflow `test_service_company` (7) and crewradar `test_service_company` (4) and
  `test_review_service_on_change` (9): 0 failed of 20 on cr-test.
- All 20 first-party modules on cr-test: 1 failed and 7 errors of 5,495, the same faults as before
  this work, none in code that it changed: `TestFormSessionPartner` (5 errors, oteny_bot,
  `KeyError: 'total_all_due'`, a field of an Enterprise module the addons path no longer loads)
  and `TestSiteLifecycle` (1 failure, 2 errors, the old ship state 93 in the cr-test data).
- The migration on `crmain_company_test`, a copy of crmain (restore of 25-Oct-2026): 3,841 of
  37,269 services moved in about 10 s, 416 of them open: 3,817 from Cuneus Duisburg GmbH to Cuneus
  Services (371 of them info services), 18 from Cuneus Netherlands and 5 from Cuneus Services to
  Duisburg (26478 and 38359 among them), and service 9724 from no company to Duisburg. The
  simulation of the cross-check counted 3,836; it gave a root info service the subject's company,
  the build gives it the supply order's company. Afterwards no child differs from its parent, no
  state record differs from its service, and 31 root services differ from their log entry: all are
  info services that keep the company of their supply order (Q7). Services 6479 and 6480 on log
  entry 7044 (no employee, no company) keep Cuneus Netherlands (Q3).
