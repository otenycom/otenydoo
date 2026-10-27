# The company of a service

A service belongs to the company of its parent, or else of its subject, and keeps following it
(riverflow 19.0.1.1374, crewradar 19.0.11.82; plan
[service-company-from-subject.md](../plans/service-company-from-subject.md)). The company decides
who sees the service (the multi-company rule shows a service only when its company is selected in
the company switcher), which billing wizard finds it, and the sender company of its letters and
mails.

## The order

`riverflow_service.py` `_compute_company_id` takes the first source that has a company:

1. An info service of a supply order (`supply_order_service_id`): the supply order's company. One
   taxi can carry passengers of more than one company; the info services of one ride stay on one
   company.
2. A child service: the company of its parent. The parent wins over the subject, so one service
   tree has one company.
3. The subject's company: `_get_subject_company()` reads `company_id` of the `res_model`/`res_id`
   record when that model has the field, with `sudo()`, because the subject decides also when the
   user has not selected its company.
4. None of these: a new service takes the company the user has selected
   (`self.env.company`); an existing service keeps its company. A ship has no company field, and a
   log entry without an employee has no company.

The template's company and the responsible team play no part: a template can be used on subjects
of every company, and a team can handle more than one company (Operations NL, Sales and Planning
work for Cuneus Duisburg GmbH and Cuneus Services Pte. Ltd.).

## The company is live

The service follows a change of its parent's or its subject's company, open or closed, invoiced or
not, as a log entry follows its employee. The compute always wins, so a company chosen by hand on
the form does not stay.

Riverflow depends on `parent_id.company_id`, `res_model` and `res_id`. A compute cannot depend on a
company through the `res_model`/`res_id` pair, so a module that knows the subject's model adds the
dependency in an override that calls `super()`; Odoo merges the `@api.depends` of every override.
crewradar adds `log_entry_id.company_id` and `employee_id.company_id`
(`crewradar/models/riverflow_service.py`).

Do not pass a company when you create a service: no `company_id` in the values and no
`default_company_id` in the context, because a value given at creation wins over the order until
the next change of the parent or the subject. The exceptions agree with the order: the info
service passes its supply order's company, Add Service on a parent service passes the parent's
company, and the salary timeline passes its own company.

**The old company comes from the database.** When the compute must keep the company, Odoo fetches
the old value with `_read()`. A service created in the same transaction and not yet written has
no old company there, so it gets the selected company. The tests write the service first
(`flush_recordset()`), as a later request does.

Tests: riverflow `test_service_company`, crewradar `test_service_company`.
