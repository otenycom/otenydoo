# Plan: fewer audit rows, the same clarity

Status: analysis done on 7-Nov-2026, on `crmain` restored from `~/Downloads/cr.zip` (production
backup of 27-Oct-2026, retention window 29-Apr to 27-Oct-2026). The purge that removes the
stored rows outside the new coverage is designed and measured on a copy (§ 11). Decided by Ries
on 7-Nov-2026: all cuts A to I in one change, and the purge includes the old rows of ignored and
removed models (§ 9, questions 1 to 3); the purge runs as a one-shot night cron that the
migration switches on (question 5); fields that code writes but users cannot edit are
audited too, as cut I (question 4); the noise rules N1 to N4 are part of the build and of the
purge (question 6). Built on 7-Nov-2026 on the branches `feat/audit-storage-reduction`
(otenydoo, radar), oteny_audit 19.0.1.712; outcome in § 14. Not user-facing, so no Basecamp to-do.

## 1. Why

The audit log is the largest part of the database. On the backup of 27-Oct-2026 the database is
1,743 MB, and the two audit tables use 1,061 MB of it:

| Table | Total | Heap | Indexes | Rows |
|---|---|---|---|---|
| `oteny_audit_log` | 573 MB | 343 MB | 167 MB | 1,116,317 |
| `oteny_audit_log_ref` | 488 MB | 242 MB | 246 MB | 1,495,558 |

Every audit row is also a write: one insert into `oteny_audit_log` (six indexes, one of them
GIN), one or more inserts into `oteny_audit_log_ref` (three indexes), and a read of the old value
before the business write. A row that no person will ever read costs storage, write time on
every transaction and backup size.

The goal: remove the rows that hold no information that an investigator cannot get from other
audit rows. A cut that removes the only record of who changed a business fact is not in this plan.

## 2. How the data was measured

All numbers come from SQL on `oteny_audit_log` and `oteny_audit_log_ref`, and from the Odoo
registry (`odoo-bin shell`) for the field definitions. "Bytes" is `pg_column_size` of the row,
so it excludes index space. The queries are throwaway; they are not in the repository.

Who wrote a row: `create_uid` of the audit row is the user of the transaction. User 1
(`__system__`) means a cron or an upgrade.

Share of the last 30 days of the backup (rows): `rivercreds.plan.item` 41.7 %,
`riverflow.service` 31.1 %, `ir.attachment` 3.9 %, `crewradar.salary.timeline` 3.3 %,
`account.move.line` 3.1 %, everything else below 3 % each.

## 3. The cuts

Each cut says what it removes, why no information is lost, the lever, and where it lives.
Numbers are for the full retention window and have no overlap between the cuts (each row is
counted once, in the first cut that takes it).

| Cut | Log rows | Log bytes | Direct refs | Parent refs |
|---|---|---|---|---|
| A. `rivercreds.plan.item` | 518,896 | 107 MB | 518,896 | 0 |
| B. `riverflow.service.end_date_for_calendar` | 34,639 | 8.1 MB | 34,639 | 33,213 |
| C. Generated image attachments | 10,482 | 6.6 MB | 10,482 | 21,556 |
| D. `ir.cron.trigger` | 2,769 | 1.0 MB | 2,769 | 0 |
| E. Updates in the insert transaction | 183,827 | 37 MB | 183,827 | 93,718 |
| G. `oteny.oops.log` | 0 on this backup | | | |
| H. Reconciliation salary timelines | 8,090 | 6.8 MB | 8,090 | 0 |
| **A to E, G, H** | **758,703 (68 %)** | **167 MB (43 %)** | **758,703** | **148,487** |
| F. Direct refs of the rows that stay | | | 357,634 | |

### A. `rivercreds.plan.item`: ignore the model

- **What:** 99 % of its rows are `start_date` and `end_date` updates by `__system__`, about
  4,000 per day. The daily cron moves the planning window, and the slot compute
  (`rivercreds.plan.slot._compute_item_ids`, `_prepare_item_sync`) moves the dates of the
  synthetic items with it.
- **Why it is safe:** no person writes a plan item. The list view has `create="0" edit="0"
  delete="0"`, and the readers (`riverflow.group_service_reader`) have read access only. The
  slot compute rebuilds every item from its inputs, and those inputs stay audited: the
  credentials, the log entries and the requirement windows. The link from a service to its plan
  item (`credential_plan_item_id`) is on the service and stays audited. This meets both tests
  for "ignore a model" in the oteny-audit skill ("Bot and agent activity logs": no person writes
  it, and the system rebuilds it on a clock), the same as the salary timeline segments.
- **Lever:** `_oteny_audit_ignore = True` on `rivercreds.plan.item`
  (`rivercreds/models/rivercreds_plan_item.py`).
- **Not included:** `rivercreds.plan.slot` (1,719 rows). Its name, requirement window and status
  are computed too, but it is small, and a slot that appears or goes away is a fact an
  investigator can want to see. Keep it.

### B. `riverflow.service.end_date_for_calendar`: ignore the field

- **What:** 34,639 update rows.
- **Why it is safe:** the compute is `end_date or deadline`
  (`riverflow/models/riverflow_service.py`, `_compute_end_date_for_calendar`). Both inputs are
  audited. The inverse writes `end_date` (or clears it when the value equals the deadline), and
  that write is audited too. So every value of this field follows from two other audited fields.
- **Lever:** new. Today no lever ignores one field. Add a class attribute
  `_oteny_audit_ignore_fields`, a set of field names, built like
  `_oteny_audit_html_strip_fields`. The patch skips a field in the set at the four places that
  write audit rows (`patched_create`, the relational branch of `patched_write`,
  `patched_flush`, `patched_unlink`). Cost: one set lookup per field. Declare it on
  `riverflow.service` with `{"end_date_for_calendar"}`.

### C. Generated image attachments: skip per record

- **What:** inserts and deletes of `ir.attachment` whose `res_field` is `thumbnail` (8,805 on
  `ir.attachment`, 932 on `documents.document`, 147 deletes) or `image_128`, `image_256`,
  `image_512`, `image_1024` (134 each, on `res.partner`).
- **Why it is safe:** the browser generates the `thumbnail` of a PDF (`mail` adds
  `ir.attachment.thumbnail = fields.Image()`), and Odoo makes the four smaller images by resizing
  `image_1920`. The source file and `image_1920` stay audited. `res_field` does not change
  after create, so the per-record skip gives the same answer at create and at delete, which
  the skill requires for `_oteny_audit_ignore_record`.
- **Lever:** `_oteny_audit_ignore_record` on `ir.attachment` in `oteny_audit`, returning True
  when `res_field` is in a fixed set of generated image fields.
- **Not included:** attachments of transient models (wizards), about 7,000 inserts. A wizard
  often moves its attachment to a business record later (`res_model` and `res_id` change, 4,512
  update rows), so a skip at create would leave updates without their insert.

### D. `ir.cron.trigger`: add to the default ignore list

- **What:** 2,769 inserts (580 by `__system__`, the rest by users whose action schedules a cron).
- **Why it is safe:** a trigger is a row in the scheduler queue. It holds a cron and a time, and
  Odoo deletes it after the run. It says nothing about a business fact.
- **Lever:** add `"ir.cron.trigger"` to `_DEFAULT_IGNORED_MODEL_NAMES` on `oteny.audit.log`,
  next to `ir.cron.progress`.

### E. Updates in the insert transaction: fold them into the insert snapshot

- **What:** 197,478 update rows (183,827 after A to D) are written in the same database
  transaction as the insert of their record. The largest group is `riverflow.service`: 93,473
  rows. Almost every new service gets six or seven of them: `has_been_paid_on_salary`,
  `charge_unit_price`, `deadline`, `end_date_for_calendar`, `charge_salary_month`,
  `charge_invoice_month` (about 13,400 each) and `project_deadline` (7,539). Other models:
  `account.move.line` 27,319, `crewradar.salary.timeline` 14,365,
  `rivercreds.document.import.job` 14,326, `account.move` 7,928, `ir.attachment` 6,719,
  `rivercreds.credential` 5,061, `crewradar.log.entry` 4,055.
- **Cause:** `patched_create` reads the new row with SQL right after the create. Stored
  computes and code that writes right after the create (for example the salary timeline
  builder, which sets the reconciliation links) arrive later in the same transaction. The patch
  writes those as update rows, with the value of the snapshot as the old value.
- **Why it is safe:** a value that changes inside one transaction before the commit was never
  visible to another user or process. The insert snapshot with the value at the end of the
  transaction says everything that a person or another process could ever have seen. A change
  in a later transaction stays an update row, so the history of the record after its creation
  does not change.
- **Lever:** in `patched_flush` and in the relational branch of `patched_write`, when the
  record is in `newly_created_records` of this transaction, update the insert snapshot row
  instead of writing an update row: one `UPDATE ... SET snapshot = snapshot || %s` per insert
  row and flush, with the same HTML strip and redaction as the create. When the new value is
  the default or empty, remove the key, the same rule as at create. Keep a map from record to
  its insert row id in `env.cr.precommit.data["oteny_audit"]`.
- **Watch out:** when the folded field is the parent field (`_oteny_audit_parent_field`, for
  example `log_entry_id` set after the create), resolve the parent refs of the insert row again
  and add the missing ones. If you do not, the creation of a service no longer shows under its
  log entry.
- **Cost:** one update of a jsonb row per insert and flush, in place of one to seven inserts of
  a log row and its refs.
- **Change of a documented rule:** the oteny-audit skill says that late fields on a new record
  are logged as `'u'` rows ("How Changes Are Captured", step 3, and the Completed item "Insert
  ordering"). This cut replaces that rule. The skill text changes with the build.

### F. Direct refs: stop writing them

- **What:** every log row gets one ref row with `is_direct = True`
  (`_create_audit_log_references`). It repeats the model, the record id, the record name, the
  date and the transaction of its own log row. On the backup these are 1,116,317 of the
  1,495,558 ref rows. After A to E, 365,724 remain.
- **Why it is safe:** the direct ref holds no value that is not in the log row. The only readers
  of the ref table are the view `oteny.audit.log.aggregated` and the orphan cleanup in
  `cleanup_old_audit_logs`. Every other reader (the Audit Log action, `oteny.audit.mixin`,
  `crewradar_log_entry.py`, the Wilma data analyst) reads the view, so they see no change when
  the view keeps its columns.
- **Lever:** the view takes its direct rows from `oteny_audit_log` itself and its child rows
  from the ref table (four branches: direct and child, each with and without a snapshot). The
  purge of § 11 deletes the direct refs in batches.
- **Risks to check:**
  - The view id is `ref.id * 1000 + ordinal` today. Direct rows have no ref id, so the id needs
    a new encoding that cannot collide: `(log.id * 1000 + ordinal) * 2` for direct rows and
    `(ref.id * 1000 + ordinal) * 2 + 1` for child rows (decided by Ries on 7-Nov-2026). It must
    stay below 2^53.
  - The view was tuned twice for speed (the date filter from 4.8 s to 113 ms, the value search
    from 36 s to about 100 ms; see the comments in `oteny_audit_log_aggregated.py`). The direct
    branches must use the log indexes: (`model_name`, `record_id`, `create_date DESC`, `id
    DESC`), `create_date`, `transaction_id`. Measure both queries again on `crmain`.
- **Cost:** the largest build of this plan: a view rewrite, a purge of a large table and a
  new speed check. Built in the same change as the other cuts (decided by Ries on 7-Nov-2026).

### G. `oteny.oops.log`: ignore the model

- **What:** the error log of otenydoo (since 25-Oct-2026, so the backup has no rows yet). All
  its fields are `readonly=True`, so each error gives an insert row with an empty snapshot, and
  each record that the autovacuum deletes (`last_date` older than the retention) a delete row.
- **Why it is safe:** the record is itself the log of an event, written by the system. "A log of
  a log has no audit value" (oteny-audit skill).
- **Lever:** `_oteny_audit_ignore = True` on `oteny.oops.log`.

### H. Reconciliation salary timelines: skip per record

- **What:** the rows of `crewradar.salary.timeline` records with `type = "reconciliation"`:
  4,840 inserts and 3,250 deletes. Their 4,840 updates (`owner_timeline_id`) fall in the insert
  transaction and are already counted under cut E.
- **Why it is safe:** only code makes, changes and deletes a reconciliation timeline. See § 6,
  "Salary timeline", for the walk through the code. The estimate that owns it stays audited,
  and its `reconciliation_timeline_id` row names the reconciliation, also after a regenerate
  replaced it.
- **Lever:** `_oteny_audit_ignore_record` on `crewradar.salary.timeline`, returning True when
  `type == "reconciliation"`. `type` is `readonly=True` and only the builder sets it, at the
  create, so the answer is the same at create and at delete.

### I. Audit the fields that code writes but users cannot edit

Added by Ries on 7-Nov-2026 (§ 9, question 4). This cut adds rows instead of removing them; it
closes the gap of § 7.

- **What changes:** today the patch logs a stored field only when it is not `readonly`. In
  Odoo, `readonly=True` on a field without a compute means "read-only on screen", not "never
  written", so code writes such fields and the log never shows them. From now on the patch logs
  every stored field with a column **except a computed or related field that is read-only**
  (as today, the log explains such a value through its inputs). A computed field with an
  inverse stays logged, as today. One helper decides this for all four places that write
  audit rows (`patched_create`, the relational branch of `patched_write`, `patched_flush`,
  `patched_unlink`), together with `_oteny_audit_ignore_fields` of cut B.
- **What this adds to the log** (measured on the backup, window 29-Apr to 27-Oct-2026, after the
  exclusions below): 33 fields; 16,091 values in insert snapshots (no new rows, only keys), and
  at most 3,599 update rows on records made before the window (the count of such records that
  hold a value and changed in the window; updates of records made in the window are mostly
  folded by cut E). The fields that matter most:
  - `account.move.state`, `move_type`, `is_move_sent`, `reversed_entry_id`: who posted, sent,
    cancelled or reversed an invoice; today the log does not show the posting of an invoice;
  - `account.move.line.move_id`, `full_reconcile_id`: the invoice of a line, and its payment
    match;
  - `riverflow.service.invoice_line_id`, `salary_timeline_id`, `follow_up_of_service_id`,
    `credential_closed_by_plan`;
  - `crewradar.timesheet.billed_on_move_line_id`, `salary_timeline_id`;
    `crewradar.leg.pax.invoice_line_id`; `crewradar.vacation.payout.salary_timeline_id`: which
    invoice or salary month included the item, and the release when a salary is cancelled or
    deleted (`_unlink_included_items`);
  - `crewradar.salary.timeline.type`: whether a timeline is an estimate, a sandbox or a
    reconciliation;
  - `crewradar.contract.created_with_agency_id`; the fields of the salary and accounting export
    files (`crewradar.salary.export.file`, `crewradar.accounting.export`).
- **Excluded, because they are technical or would be noise** (each in the module that owns the
  field, with `_oteny_audit_ignore_fields`):
  - riverflow, `riverflow.tombstone.mixin`: `to_be_deleted`. A read-only `active` is not logged
    either (rule in `oteny_audit`): only `_retire()` writes it, right before the delete in the
    same transaction, and a model with a user archive (services, log entries) redefines
    `active` as writable, so it stays logged there;
  - riverflow: `user_write_date` (`riverflow.state.record`, `riverflow.highlight.row.mixin`; it
    changes with every user write and repeats the time of the audit row itself) and
    `bot_released_token` (`riverflow.state.bot.mixin`);
  - `oteny_audit`, `ir.attachment`: `index_content` (the full text of a document, 11.8 MB in the
    window; the file itself is the source), `checksum` (`store_fname` already holds it),
    `mimetype`, `file_size`;
  - `oteny_cloud_filestore`, `ir.attachment`: `cloud_local_only`;
  - `oteny_audit`, `mail.message`: `message_id` (the e-mail header id; it joins rule N3);
  - `oteny_audit`, `res.users`: `oauth_access_token` (it changes at every OAuth login),
    `odoobot_state`, `odoobot_failed`;
  - `oteny_article`, `oteny.article`: `html_field_history` (the article's own revision history);
  - `oteny_audit`: `ir.model.constraint` joins `_DEFAULT_IGNORED_MODEL_NAMES` (the reflection
    of database constraints, rewritten by every upgrade). `ir.module.module` stays audited: its
    `state` and `latest_version` say who installed or upgraded which module, and when.
- **Old rows:** the rows written before the change hold none of these fields, and nothing can
  add them afterwards. The purge of § 11 does not touch this cut.

## 4. Effect

| | Log rows | Log bytes | Ref rows |
|---|---|---|---|
| Today | 1,116,337 | 387 MB | 1,495,578 |
| After A to E, G, H | 357,634 | 220 MB | 588,388 |
| After A to H | 357,634 | 220 MB | 230,754 |
| After the purge of § 11 (A to H and the old rows of ignored and removed models) | 271,421 | | 233,805 |

Measured on a copy of `crmain` (§ 11): after the purge and `VACUUM FULL`, the audit tables go
from 1,062 MB to 302 MB, and the database from 1,974 MB to 1,214 MB. Cut I adds at most about
3,600 update rows and 16,091 snapshot values per 180 days, which does not change these numbers. Writes: cut A alone removes about 4,000
log rows and 4,000 ref rows per day; cut E removes six to seven log rows and their refs per new
service.

## 5. What stays audited, and why

- **The computed fields with an inverse on the service**: `deadline`, `project_deadline`,
  `charge_invoice_month`, `charge_salary_month`, `has_been_paid_on_salary`,
  `charge_unit_price`. About 135,000 rows (30 MB); cut E removes the part that is written at
  create. 86 % to 93 % of the rest come from real users, mostly when a user moves the dates of a
  log entry. A rule that logs them only on an explicit write would save most of these rows, but
  HR would no longer see under a log entry which deadlines moved, and a formula change in a
  deploy would leave no trace.
- **Accounting** (`account.move`, `account.move.line`): financial records. Their computed fields
  (`no_followup`, `balance`, `invoice_date_due`, and more) are about 5 % of the rows.
- **`mail.message.body`**: the audit log is the only copy after a cascade delete (oteny-audit
  skill, "Default-Ignored Infrastructure Models").
- **`crewradar.plan.item`** (the crew planning, not the credential planning): users make these
  items (3,005 inserts, none by `__system__`).

## 6. Analysed

### Salary timeline (`crewradar.salary.timeline`): computed content, human workflow

The question: to what extent is the salary timeline a read-only computed model, like the
credential plan item? Answer: its content is computed, and so is one of its three types; the
existence and the state of an estimate are acts of a person.

**What a user can do.** `ir.model.access.csv` gives `base.group_user` read and delete, and no
write and no create (`1,0,0,1`). The form and the list have `create="false" edit="false"`.
A user changes a timeline only through these paths:

- the monthly salary wizard (`action_create_estimates`) builds the estimates of a month for the
  selected employees; the sandbox wizard (`crewradar_salary_wizard.py`) builds a sandbox;
- the buttons `action_confirm`, `action_payroll_processed`, `action_set_confirmed`,
  `action_cancel`, `action_draft` and their batch versions change `state`;
- `action_regenerate` rebuilds a draft timeline in place;
- delete, from the list or the form.

Each path runs the builder or the write as `sudo()` ("timelines cant be created or changed
directly by users"). `sudo()` keeps the user, so the audit rows name the person who pressed the
button.

**Field by field:**

| Field | Who sets it | In the audit log today |
|---|---|---|
| `month_start_date`, `month_end_date`, `employee_id`, `company_id` | the builder, from the wizard input, at create | insert snapshot |
| `manning_agency_id` | the builder, a frozen copy of the employee's agency, again at regenerate | insert snapshot, update at regenerate |
| `type` | the builder, at create | never (`readonly=True`, § 7) |
| `state` | the buttons (estimates only) | updates: 2,962 draft to confirmed, 1,323 confirmed to payroll processed, 49 back to draft |
| `reconciliation_timeline_id`, `reconciliation_estimate_timeline_id`, `owner_timeline_id` | the builder, right after the create and at regenerate | updates, 14,365 of them in the insert transaction (cut E) |
| `display_name`, `is_passive`, `is_exported`, `has_contract`, `contract_status` | stored computes | never (read-only computes) |
| segments, adjustments, salary lines, check results, export fields | the builder | never (models ignored) |
| `crewradar.salary` | the builder; a user edits `remarks` and the net payable | user edits only (builder runs with `oteny_audit_ignore=True`) |

**The three types:**

- **Reconciliation** (4,840 inserts, 3,250 deletes): fully computed. The builder makes one when
  it builds an estimate and the previous month has a confirmed or payroll-processed estimate
  (`_create_reconciliation_timeline`). It has no state, every button refuses it, regenerate
  skips it, and `unlink()` refuses to delete it directly. It goes away only by the cascade from
  its estimate or when a regenerate replaces it. In the audit data, 76 transactions delete one
  reconciliation and change `reconciliation_timeline_id` on its estimate: a regenerate. Every
  other reconciliation delete comes with the delete of its estimate. This is the same shape as
  the credential plan item, so cut H skips it.
- **Estimate** (about 4,786 inserts, 3,189 deletes after the reconciliations are taken out):
  computed content, but a person decides that it exists, that it is confirmed and paid, and
  that it is deleted. The data shows that the user who builds the salaries (35,008 of the 35,019
  rows) deletes the whole set of a month (about 266
  estimates, in pages of 80) and builds it again: 1,870 draft estimates on 12 days, and 1,319
  confirmed estimates on 18-May, 14 and 15-Jul, 10-Aug and 20-Aug-2026. Only the audit log can
  say who deleted confirmed salaries and when. Keep.
- **Sandbox** (2 records): a person builds it to test. Keep; the volume is nothing.

**Result:** after cuts E and H, the timeline writes the insert and the delete of each estimate,
its state changes, and about 255 link updates at regenerate: 12,564 rows in place of 35,019.

**Gaps that stay** (not part of this plan): the insert snapshot does not say the type (§ 7).
The link from a service, a timesheet or a vacation payout to the timeline
(`salary_timeline_id`, `readonly=True`) is not audited either, so the audit log cannot show
which items an estimate included, or that `_unlink_included_items` released them at a cancel or
a delete (§ 7, question 4).

## 6b. Analysed, no change needed

### Document import job (`rivercreds.document.import.job`)

1,657 inserts and 14,407 updates. The form has `edit="0"`: the job is written by the import,
never by a person. 14,326 of the updates fall in the insert transaction, so cut E removes 99 %
of them. The rest is 81 rows. The insert row stays: it records who imported which file. No
separate cut is needed.

### Old rows of models that are now ignored or removed

104,431 log rows (79 MB) and as many refs belong to models that no longer write audit rows:

| Model | Rows | Bytes | Last row | Why |
|---|---|---|---|---|
| `wilma.transport.review` | 56,502 | 46 MB | 26-Aug-2026 | ignored since then |
| `documents.document` | 39,519 | 10 MB | 24-Oct-2026 | removed by the switch to Community |
| `ai.embedding` | 528 | 11 MB | 21-Oct-2026 | removed by the switch to Community |
| `wilma.api.cache` | 2,761 | 9.7 MB | 26-Aug-2026 | ignored since then |
| `documents.access` | 4,643 | 2.2 MB | 24-Oct-2026 | removed by the switch to Community |
| 14 other removed models (sign, knowledge, spreadsheet) | 478 | 0.4 MB | | removed by the switch to Community |

The retention cleanup deletes all of them by about 22-Apr-2027. A one-off delete would only make
that earlier. Recommended: no action.

The oteny-audit roadmap item "Purge the legacy residue of the already-ignored models" (851,370
rows on `test1`) is already done by the retention cleanup: the backup holds no row of a model on
`_DEFAULT_IGNORED_MODEL_NAMES`, and its oldest row is from 29-Apr-2026. The roadmap text said
the cleanup "never will" remove these rows; that was wrong. The item moved to Completed on
7-Nov-2026.

## 7. A clarity gap found on the way, closed by cut I

The patch logged only fields that are stored and not `readonly`. In Odoo, `readonly=True` on a
field without a compute means "read-only on screen", not "never written". Code writes such
fields, so the audit log never recorded them, not even in the insert snapshot: among them the
invoice and salary links of services and timesheets, the type of a salary timeline, and the
state of an invoice. Cut I (§ 3) closes this gap; Ries added it to this plan on 7-Nov-2026.

## 8. Build order and checks

All cuts, A to I, go in one change (decided by Ries on 7-Nov-2026, § 9).

1. **Cuts A to I** (otenydoo: `oteny_audit`, `riverflow`, `oteny_oops`, `oteny_cloud_filestore`, `oteny_article`; radar: `rivercreds`,
   `crewradar`). Tests in `oteny_audit/tests` unless noted:
   - E: create a record and let a stored compute change it in the same transaction: one insert
     row with the final values, no update row. The same write in a later transaction: one
     update row. A parent field set after the create in the same transaction: the insert row
     has the parent ref. A redacted field and an HTML-stripped field keep their rules when
     folded. An x2many write after the create folds too.
   - B: write a field in `_oteny_audit_ignore_fields`: no row for it, the other fields of the
     same write keep their rows; the field is also absent from the insert and delete snapshots.
   - C: create and delete an attachment with `res_field = "thumbnail"`: no rows; the same with
     `image_1920`: rows.
   - D and G: create an `ir.cron.trigger` and an `oteny.oops.log`: no rows.
   - N1: an insert of a record with an empty value for a field without a default leaves that
     key out; an empty value for a field with a default (`mail.message.email_add_signature`
     False) stays. N2: no `parent_path` key in the snapshot of a `riverflow.service` child.
     N3: a posted message has none of the keys of § 12 N3, nor `message_id`. N4: an asset
     bundle attachment (`url` `/web/assets/…`) writes no row.
   - A (in `rivercreds/tests`): the plan item cron writes no audit row; a change of a credential
     still writes its rows.
   - I: a write by code on a read-only field without a compute writes an update row, and its
     value is in the insert and delete snapshots; a read-only computed or related field still
     writes none; `to_be_deleted`, a read-only `active` and the excluded fields of § 3 I write
     none; `account.move` posted from draft writes a `state` row.
   - H (in `crewradar/tests`): building an estimate whose previous month is confirmed writes
     rows for the estimate and none for its reconciliation; regenerate and delete of the
     estimate write no rows for the reconciliation; a state button on the estimate still writes
     its row.
   - Run the full module list (`.claude/skills/crewradar-development/references/running-tests.md`).
   - Measure on `crmain`: one service create before and after (rows and refs written), and one
     run of the plan item cron.
   - The purge cron of § 11 and the migration that switches it on, in the same change, so that
     no new row outside the coverage arrives after the purge. Tests: each step on a small data
     set (a row of each cut goes, a covered row stays, a folded value lands in the snapshot, a
     parent ref moves); a second run changes nothing; a batch that raises rolls back alone; the
     cron deactivates itself when nothing remains.
   - F: the view rewrite. Check on `crmain` that the view returns the same rows before and
     after the purge for a sample of records (count and content per model and record, with
     the direct rows now read from the log), and repeat the two speed measurements of § 3 F (the
     date filter, 113 ms today; the value search, about 100 ms today). Test the new id encoding:
     no two view rows share an id, and every id stays below 2^53.

## 9. Questions and decisions (all decided by Ries on 7-Nov-2026)

1. **Cuts A to E, G and H.** Situation: 68 % of the audit rows hold no information that is not
   in another audit row or that any person could ever have seen; the largest part is the daily
   cron that moves the dates of credential plan items. Question: do we build cuts A, B, C, D, E,
   G and H? Recommended: yes, as one change, because each cut is small and they share the tests.
   **Decided by Ries on 7-Nov-2026: yes.**
2. **Cut F.** **Decided by Ries on 7-Nov-2026: build F in the same change and delete the direct
   refs in the same purge** (in place of the recommended second change). § 8 and § 11
   follow this decision.
3. **Old rows of removed models** (changed on 7-Nov-2026: Ries asked for a migration that makes
   the stored rows match the new coverage, § 11; the old rows of ignored models such as
   `wilma.*` are part of it). Situation: 26,950 rows remain of models that the switch to
   Community removed (`documents.*`, `sign.*`, `knowledge.*`, `spreadsheet.*`, `ai.*`). Those
   records no longer exist, so these rows are their last history in the database; the retention
   cleanup deletes them by about 22-Apr-2027 anyway. Question: does the purge delete them
   too? **Decided by Ries on 7-Nov-2026: yes.** "The audit log holds what the current coverage
   writes" is one rule without exceptions, and the cost is small (§ 11).
4. **Read-only fields that code writes.** Situation: invoice links, salary timeline links and the
   salary timeline type are never in the audit log, because those fields are read-only on
   screen. Question: do we audit stored fields that are read-only on screen but have no compute?
   **Decided by Ries on 7-Nov-2026: yes, in this plan, as cut I (§ 3).**
5. **Where the purge runs.** Situation: Ries asked on 7-Nov-2026 whether a dedicated cron after
   the upgrade is wiser than a migration. A migration adds 40 to 60 seconds (laptop) or an
   estimated 2 to 5 minutes (odoo.sh) to the upgrade, rolls back the whole upgrade on a failure,
   and cannot run `VACUUM FULL`, so the space would come back only by a manual step. Question:
   do we run the purge as the one-shot night cron of § 11, switched on by the migration?
   Recommended: yes, because the upgrade stays short, `VACUUM FULL` runs on its own at a quiet
   hour, and each batch rolls back alone. **Decided by Ries on 7-Nov-2026: yes, the cron.**
6. **Noise inside the rows (§ 12).** Situation: about one line in five under an insert in
   **Audit > Log** is an empty value, a materialized id path, or mail plumbing, and the web
   asset bundles show as inserts by OdooBot. Question: do we add rules N1 to N4 to this change,
   and strip them from the stored rows in the purge? Recommended: yes, because each rule is a
   few lines in `oteny_audit`, loses no information, and the purge already rewrites 23,617 of
   the 56,745 rows concerned. **Decided by Ries on 7-Nov-2026: yes.**

## 10. Skill updates after the build

- `oteny-audit/SKILL.md`: the lever `_oteny_audit_ignore_fields`; the fold of updates in the
  insert transaction (replaces step 3 of "How Changes Are Captured" and the Completed item
  "Insert ordering"); `ir.cron.trigger` on the default list; the per-record skip on
  `ir.attachment`; the storage numbers of § 4. (The roadmap item about the legacy residue
  is already marked done, 7-Nov-2026.)
- rivercreds skill: `rivercreds.plan.item` is audit-ignored, and why.
- `oteny-audit/SKILL.md`, "Salary sub-models": reconciliation timelines are skipped per record;
  estimates and sandboxes stay audited for their creation, state and deletion.
- otenydoo `oteny-oops` skill: `oteny.oops.log` is audit-ignored.
- `oteny-audit/SKILL.md`, "How Changes Are Captured": an empty value of a field without a
  default is left out of the insert snapshot (N1); `parent_path` is never logged (N2); the
  `mail.message` delivery fields are ignored (N3); asset bundles join the per-record rule of
  `ir.attachment` (N4).
- `oteny-audit/SKILL.md`, "Automatic ORM Patching": a read-only field without a compute is
  logged (cut I); the list of excluded fields and where each is declared.

## 11. Purge: remove the stored rows that the new coverage would not write

Asked by Ries on 7-Nov-2026: the stored audit rows must match the new coverage, not decay over
the 180 days of retention. The scope is every cut, A to H, plus the old rows of models that are
ignored or removed (§ 9, questions 1 to 3, decided by Ries on 7-Nov-2026).

### What it does

The purge applies the cuts to the rows that already exist, in this order:

1. **Rows of ignored models.** Every `model_name` for which `oteny.audit.log._is_audit_ignored`
   answers True on the full registry (without the `oteny_audit_ignore` context). This covers
   A (`rivercreds.plan.item`), D (`ir.cron.trigger`), G (`oteny.oops.log`) and the old rows of
   models that were ignored earlier (`wilma.transport.review`, `wilma.api.cache`: 59,263 rows).
   A model that is not in the registry any more also answers True, so its rows go too
   (26,950 rows of the models that the switch to Community removed; question 3).
2. **Rows of ignored fields** (cut B): the update rows of each field in
   `_oteny_audit_ignore_fields`, and the key of that field inside the insert and delete
   snapshots of the model (`snapshot - '<field>'`).
3. **Rows of ignored records**, by the SQL shape of each per-record rule:
   - C: `ir.attachment` snapshot rows whose `res_field` is a generated image field;
   - the existing `mail.message` rule: messages posted into a `discuss.channel`
     (`snapshot->'model'->>'raw' = 'discuss.channel'`), 2,358 rows from before 25-Aug-2026;
   - H: `crewradar.salary.timeline` rows of a reconciliation. A live record is found by
     `type = 'reconciliation'`. A deleted one is found by the shape of its snapshot: the
     insert holds `reconciliation_estimate_timeline_id`, or the delete holds
     `owner_timeline_id` (5,623 records in all);
   - N4: `ir.attachment` snapshot rows whose `url` starts with `/web/assets/` (§ 12).
4. **The fold** (cut E): each update row in the same transaction as the insert of its record
   (same `model_name`, `record_id`, `transaction_id`) goes into the insert snapshot: one
   `UPDATE` per insert row with `snapshot || jsonb_object_agg(field_name, {raw, display, label}
   ORDER BY id)`, so the last value of the transaction wins. The values in the update rows are
   already stripped and redacted. A parent ref of a folded row that the insert row lacks moves
   to the insert row (3,051 refs on the backup). Then the folded rows are deleted. The same
   `UPDATE` strips the noise of § 12 (N1 to N3) from the insert snapshot; an insert snapshot
   that carries such a key but is no fold target gets its own `UPDATE` (33,128 rows on the
   backup, estimated 260 MB of WAL and 8 seconds on the laptop, not measured).
5. **The delete.** The refs of every row of steps 1 to 4, and every direct ref (cut F), go
   first; then the log rows. Deleting the refs first keeps the foreign key
   `oteny_audit_log_ref_audit_log_id_fkey` (`ON DELETE CASCADE`) from looking up each ref one
   by one.
6. **Give the space back:** `VACUUM (FULL, ANALYZE) oteny_audit_log, oteny_audit_log_ref`. See
   "The space comes back only with `VACUUM FULL`" below.

### Measured cost

Measured on 7-Nov-2026 on a copy of `crmain` (production backup of 27-Oct-2026), PostgreSQL
18.3 on Ries's laptop (Apple silicon, local SSD), `wal_level = replica`,
`full_page_writes = on`, `wal_compression = off`, `max_wal_size = 1GB`. A `CHECKPOINT` ran
just before, so every first touch of a page wrote a full page image: the worst case. The SQL
ran steps 1 to 5 in one transaction, with the full scope (all cuts, F and the old rows).

| Part | Rows | Time | WAL |
|---|---|---|---|
| Find the rows (temporary tables) | 684,147 to delete, 160,769 to fold | 1.5 s | 0.2 MB |
| Fold into 39,627 insert snapshots | 39,627 updated | 9.3 s | 332 MB |
| Move 3,051 parent refs, strip the B key from 229 snapshots | | 3.3 s | 28 MB |
| Delete refs (of deleted rows, and every direct ref) | 1,264,824 | 7.9 s | 305 MB |
| Delete log rows | 844,916 | 14.2 s | 768 MB |
| **Total, one transaction** | | **41 s** | **1,434 MB** |

A first run of a smaller scope (without F and without the old rows) took 57 s on a colder
cache, so read the time as 40 to 60 seconds on the laptop.

The result is exact: 271,421 log rows remain (357,634 of § 4 less the 86,213 old rows) and
233,805 ref rows (the parent refs of the rows that stay, and the 3,051 moved ones).

The fold is the most expensive part per row (about 8 kB of WAL per insert row). It writes a
new version of each row with a large jsonb value and adds entries in the six indexes of the
table, one of them GIN. The deletes write little per row, but each touches pages spread over
the whole table, so most of their WAL is full page images. A checkpoint inside the run starts
the full page images again; `max_wal_size = 1GB` caused at least one.

### The space comes back only with `VACUUM FULL`

A `DELETE` does not make the files smaller. On the copy, right after the purge, the audit tables
were *larger* than before (`oteny_audit_log` 573 MB to 637 MB, from the new row versions of the
fold), and the database grew from 1,974 MB to 2,038 MB.

| Step | Time | WAL | Lock | Result |
|---|---|---|---|---|
| `VACUUM (ANALYZE)` (autovacuum does this on its own) | 2.1 s | 524 MB | none that blocks writes | the free space is reused by new rows; the files keep their size |
| `VACUUM (FULL, ANALYZE) oteny_audit_log, oteny_audit_log_ref` | 5.9 s | 277 MB | `ACCESS EXCLUSIVE` on both tables | `oteny_audit_log` 218 MB, `oteny_audit_log_ref` 84 MB; database 1,214 MB |

So on this copy the audit tables go from 1,062 MB to 302 MB (-72 %), and the database from
1,974 MB to 1,214 MB (-38 %). While `VACUUM FULL` runs, every create, write and delete that
writes an audit row waits, which is almost every business write.

### Where it runs: a one-shot cron after the upgrade, not the upgrade itself

Decided by Ries on 7-Nov-2026 (§ 9, question 5). Ries asked whether a dedicated cron that runs
after the upgrade is wiser than a migration. It is, for four reasons:

- **The upgrade stays short and safe.** A migration runs inside the one transaction of the
  upgrade. The purge would add 40 to 60 seconds on the laptop and an estimated 2 to 5 minutes
  on odoo.sh to every deploy that carries it, and a failure in it would roll back the whole
  upgrade. In a cron, a failure rolls back one batch, and the deploy is already done.
- **`VACUUM FULL` can be automatic.** `VACUUM` cannot run inside a transaction. In a migration
  it cannot run at all: a second connection would wait for the upgrade transaction, which
  waits for the migration. A cron can commit its last batch, then run `VACUUM FULL` on a
  separate connection in autocommit mode. Without the cron it is a manual step in `psql` on the
  production build.
- **It runs at a quiet hour.** The cron runs at night, like `Audit Log: Cleanup Old Records`
  (`0 2 * * *`), so the `ACCESS EXCLUSIVE` lock of `VACUUM FULL` falls when nobody works.
- **Batches spread the WAL.** Each batch is its own transaction, and checkpoints run between
  them, so `pg_wal` peaks lower. The total WAL stays about the same.

**The design:**

- **A new scheduled action** in `oteny_audit`, `Audit Log: Purge Rows Outside Coverage`,
  inactive by default, `0 2 * * *`. Its method does steps 1 to 5 in batches (by `id` range of
  `oteny_audit_log`; for the fold, one batch holds whole insert rows with all their folded
  rows), and calls `ir.cron._commit_progress(processed, remaining=...)` after each batch. Odoo
  19 runs the cron again while `remaining` is above zero, and `_commit_progress` returns the
  time left before the cron time limit, so the method stops in time and the next run goes on.
  When nothing remains, it runs step 6 on a separate autocommit connection and deactivates
  itself (`deactivate=True`).
- **Each batch is idempotent:** a batch that fails rolls back alone, and the next run finds the
  same rows again.
- **The migration only switches it on:** an `oteny_audit` post-migrate script at the version of
  the release sets the cron active and calls `_trigger()` for the next 02:00. It writes no audit
  row and takes milliseconds.
- **H stays in its own layer:** the SQL that finds reconciliation timelines knows the fields of
  `crewradar.salary.timeline`, so `crewradar` adds it through a hook that the purge calls: a
  method `_oteny_audit_purge_record_sql()` on the model that declares
  `_oteny_audit_ignore_record`, returning the `WHERE` part over `oteny_audit_log` (decided by
  Ries on 7-Nov-2026). The
  `ir.attachment` and `mail.message` hooks in `oteny_audit` give theirs the same way. The purge
  itself names no model.
- **The window between the upgrade and the night run is harmless.** The new code writes no new
  row outside the coverage, and the old rows stay readable until the purge removes them. One
  exception: the view of cut F reads direct rows from `oteny_audit_log`, so it must ignore the
  direct refs that still exist (`NOT ref.is_direct` in its ref branches) from the first day,
  or each change shows twice until the purge has run.
- **Staging and laptops:** odoo.sh neutralizes a staging database and turns its crons off, and
  a laptop runs crons only with the `crmain-cron` launch configuration. So on `test1` and on
  `crmain`, start the cron by hand (Scheduled Actions, Run Manually) to test and measure it.

### Estimate for production

- Production holds the same retention window, so the row counts are close to the backup's.
- An odoo.sh build has slower storage than the laptop. Expect the purge to take 2 to 5 minutes
  of batches and `VACUUM FULL` 15 to 40 seconds with the tables locked. This is an estimate,
  not a measurement.
- WAL: about 1.4 GB for the purge and about 0.3 GB for `VACUUM FULL`. When `VACUUM FULL`
  follows the purge in the same night, autovacuum finds little to do on the rewritten tables.
  PostgreSQL recycles WAL files after the checkpoints; WAL archiving or a standby on odoo.sh,
  if any, gets the same volume.
- **Measure it for real at the release check:** the deploy to `test1` restores production.
  Run the cron by hand there, read the time per batch in the log, the WAL with
  `pg_current_wal_lsn()` before and after, and the sizes before and after `VACUUM FULL`.

### Seen on the way, not part of this plan

`oteny_audit_log_ref` has two identical indexes on `audit_log_id`
(`oteny_audit_log_ref__audit_log_id_index` from `index=True` on the field, and
`oteny_audit_log_ref_audit_log_idx` from `init()`), 30 MB each on the backup.

## 12. Noise inside the rows that stay

Rules N1 to N4 are part of the build and of the purge (decided by Ries on 7-Nov-2026, § 9,
question 6). Asked by Ries on 7-Nov-2026, from a screenshot of **Audit > Log**: an attachment insert shows
"Stored Filename", "Resource Model", "Resource ID 0", "Is public document", "Url", and much of
that looks like noise. This section looks at the fields inside a row (the keys of an insert
snapshot, and the update rows of one field), not at whole models.

Measured on the backup of 27-Oct-2026, on the rows that stay after cuts A to H: 833,686 field
values in insert snapshots, 99 MB. The keys are small, so this section saves little storage
(about 13 MB). Its value is the screen: every key is one line under the header of the row in
**Audit > Log**.

### The noise, and the rule for each kind

**N1. Empty values in an insert snapshot, for a field without a default** (65,105 values,
3.8 MB). `patched_create` skips a value that equals the default of the field, and a value that
is `None` or `""`. A field without a default (most computed fields, and plain fields like
`ir.attachment.res_id`) then still writes `False`, `0`, `0.0` or `[]`. Examples:
`account.move.line.is_storno` False (8,240), `credit` 0 (8,060), `debit` 0 (7,008),
`ir.attachment.res_id` 0 (7,155: the "Resource ID 0" of a file uploaded into a wizard),
`riverflow.service.days_relative_to_project` 0 (3,415), `mail.message.is_internal` False
(2,214).
- **Why it is safe:** for a field without a default, "absent from the snapshot" already means
  "empty", and Odoo reads a `NULL` integer or boolean as `0` or `False` anyway. The rebuild of a
  record (insert, then updates, then delete) gives the same values.
- **Not included:** an empty value of a field *with* a default is information (10,277 values).
  `mail.message.email_add_signature` has the default True, so False says that the signature was
  off. The delete snapshot keeps its rule: it holds every value that is not `None` or `""`, on
  purpose (oteny-audit skill, "Tombstone Snapshots").
- **Lever:** in `patched_create`, also skip a falsy value when the field has no default.

**N2. `parent_path`** (17,809 values, 1.3 MB; on `riverflow.service` and
`res.partner.category` today). Odoo writes this materialized path of ids (for example
`12/345/`) for every model with `_parent_store`. It repeats `parent_id`, which is audited, and
the ids of its ancestors, which their own rows hold.
- **Lever:** skip the field `parent_path` on every model with `_parent_store`, in
  `oteny_audit` (a generic rule, no declaration per model).

**N3. How Odoo sent a message, on `mail.message`** (76,099 values, 7.5 MB, no update rows):
`reply_to` (the catchall alias address), `record_alias_domain_id` (one value in the whole
database), `record_company_id` (the company of the record, which the record's own rows hold),
`email_layout_xmlid` (the mail layout template), `email_add_signature`, `reply_to_force_new`.
Odoo fills them when it posts; nobody types them, and none of them changes what the message
says or to whom it went.
- **Not included:** `email_from` (for an incoming mail this is the outside sender),
  `author_id`, `message_type`, `subtype_id`, `is_internal`, `model`, `res_id`, `parent_id`,
  `date`, `subject`, `body`, `incoming_email_cc`.
- **Lever:** `_oteny_audit_ignore_fields` (cut B) on `mail.message`, in
  `oteny_audit/models/mail_message_override.py`.

**N4. Web asset bundles** (432 inserts, on 91 days). Odoo stores each compiled asset bundle
(`web.assets_web.min.js`, `web_tour.interactive.min.js`, …) as an `ir.attachment` with `url`
`/web/assets/…`, `res_model` `ir.ui.view`, `public` True, written by OdooBot whenever the
assets change. These are the "Is public document" and "Url" lines in the screenshot. They are
build output: the next request builds them again.
- **Lever:** add `url` starting with `/web/assets/` to the per-record rule of cut C
  (`_oteny_audit_ignore_record` on `ir.attachment`). The `url` of a bundle never changes, so the
  rule answers the same at create and at delete.

### What looks like noise but stays

- **`ir.attachment.store_fname`** ("Stored Filename", 29,925 values). It looks technical, but it
  is the content hash of the file (`checksum` itself is `readonly=True`, so it is not
  audited). It is the only value in the audit log that proves which file content was uploaded,
  and that two uploads were the same file.
- **`ir.attachment.res_model` on a wizard** ("Resource Model rivercreds.document.import.wizard").
  It says where the file was uploaded. Skipping the attachments of wizards is not safe: of the
  7,619 attachment inserts on a transient model, 1,713 were moved to a business record later
  (an update of `res_model`), and 1,685 sit on a business record today. Their insert row is
  their only record of who uploaded the file.
- **The values that a service copies from its template** (`use_project_deadline_from`,
  `weekend_deadline_rule`, `days_relative_to_project`, `mail_template_id`,
  `responsible_team_id`, `daily_prio`). They explain how the deadline of the service was
  computed, and the template can change later.
- **Accounting** (`account.move.line`): only N1 applies (the zero amounts and `is_storno`
  False). The amounts that are not zero stay.

### Effect

N1 to N3 remove 159,013 of the 833,686 values in the insert snapshots (19 %), so an insert in
**Audit > Log** shows about one line in five fewer. For a `mail.message` insert that is six to
eight lines fewer. N4 removes the asset bundle inserts. Storage: about 13 MB.

### In the purge (§ 11)

To match the coverage, the purge also strips N1 to N3 from the insert snapshots that exist,
and deletes the N4 rows. 56,745 insert rows carry such a key; 23,617 of them are fold targets,
so the fold `UPDATE` strips them at no extra cost. The other 33,128 rows need their own
`UPDATE`. Estimate from the fold measurement (about 8 kB of WAL and 0.24 ms per row on the
laptop): about 260 MB of WAL and 8 seconds more. Not measured.

### Seen on the way, not part of this plan

Attachments that still point to a wizard (10,499 on the backup). Most of them (8,382) are
live chatter files that the email sender wizard never moved to its service; 2,117 are real
orphans, and deleting them frees 122 MB, not 4.9 GB as first estimated here. Analysis and fix:
riverflow `plans/wizard-attachments.md`, built in riverflow 19.0.1.1407 on 7-Nov-2026.

## 13. Roadmap

- [ ] **Fewer audit rows, the same clarity** (this plan): cuts A to I and the noise rules N1 to N4 in one change (§ 3, § 8, § 12), and the one-shot night cron that purges the stored rows outside the coverage and runs `VACUUM FULL` (§ 11). Built on `feat/audit-storage-reduction` (oteny_audit 19.0.1.712); not deployed, not merged.

## 14. Outcome of the build (7-Nov-2026)

Built on `feat/audit-storage-reduction` in otenydoo and radar; oteny_audit 19.0.1.712 (the only
version bump: the other modules change Python attributes only).

- **What was built:** every cut of § 3 and every rule of § 12, as designed, plus two fixes found
  in the browser check of the audit log of a log entry and a service: a selection field showed
  its technical value ("Deadline From root_appointment") because `_get_display_value` read
  `get_values()`, which has no labels; it now stores the label, and the caption maps old rows
  to their label. And the fields of one insert or delete now read in the order of their label,
  not in the random order of `jsonb_each`.
- **Found in the build:**
  - an update of a record whose insert row is gone within the transaction (a test deletes it, a
    savepoint rolls it back) was lost by the fold; it is now logged as an update row;
  - the purge keeps its position in system parameters, and each write of them made an audit
    row; they are written with `oteny_audit_ignore`;
  - a manual run of the purge does not use the cron's `deactivate`; phase `done` switches the
    action off;
  - the insert snapshot is no sign of a reconciliation timeline, because the fold gives estimates
    `reconciliation_estimate_timeline_id` too; the purge finds deleted ones by `owner_timeline_id`
    in their delete snapshot.
- **Measured on `crmain`** (production backup of 27-Oct-2026): the purge took 77 s and 2.27 GB of
  WAL in 88 batches; 1,118,457 log rows → 270,692, 1,497,725 refs → 231,380, the audit tables
  1,065 MB → 289 MB, the database 1,984 MB → 1,208 MB. Before the purge the new view gave the
  same rows as the old one (473,483 rows since 1-Oct-2026, plus 67 rows of 6 log rows that the
  new code wrote without a direct ref). Screens after the purge: Audit › Log first page 20 ms,
  the audit log of log entry 8780 (3,320 rows, was 4,318) 8 ms in SQL and 1.4 to 1.9 s to draw,
  of service 24568 1.7 ms, a value search over 180 days 1.5 s (was 4.4 s).
- **Tests:** oteny_audit 131 tests, 0 failed (new: `test_audit_coverage`, `test_audit_purge`;
  18 existing tests now write in a later transaction through `tests/common.py:end_transaction`,
  and the ref tests count no direct ref); crewradar `test_salary_timeline_audit`; riverflow
  `test_transition_audit_remark` runs its transition in a later transaction. Full suite on
  `cr-test` (oteny_audit, riverflow, rivercreds, crewradar, crewradar_creds, crewradar_cuneus_sign,
  crewradar_wilma, crewradar_sign, oteny_oops, oteny_article, oteny_cloud_filestore, oteny_bot,
  oteny_shortcut): 0 failed, 0 errors of 5,698 tests.
