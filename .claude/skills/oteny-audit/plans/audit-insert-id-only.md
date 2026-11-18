# Plan: an insert writes only the record's id, and the first page of the audit log opens at once

Status: analysed and built on 18-Nov-2026 on `feat/audit-insert-id-only` (otenydoo and radar), on
`crmain` restored from `~/Downloads/cr.zip` (production backup of 18-Nov-2026). Not merged.
Asked by Ries on 18-Nov-2026, with two goals:

1. The first page of the audit log opens at once from every Action menu that shows it (the
   generated **Audit Log for …** server actions, and `action_view_audit_log` on the log entry and
   the service).
2. The log gets smaller and easier to read. The principle (Ries): an insert writes one audit row
   with the id of the new record and nothing more, because the current values of the other
   fields are in the record itself. The log must still allow to analyse, recover and blame every
   insert, update and delete.

Not user-facing as a feature (the screens keep their shape), so no Basecamp to-do.

## 1. Measured on the production backup of 18-Nov-2026

The audit tables: `oteny_audit_log` 212 MB (281,245 rows, since 21-May-2026),
`oteny_audit_log_ref` 86 MB (239,128 rows); the database 1,024 MB.

| Row kind | Rows | Row bytes |
|---|---|---|
| Insert snapshot (`i`) | 87,306 | 90 MB |
| Update (`u`) | 178,679 | 41 MB |
| Delete snapshot (`d`) | 12,536 | 11 MB |
| Remark (`r`) | 2,724 | 0.6 MB |

The insert snapshots are 63 % of the bytes of the log rows.

### Why the first page is slow

The **Audit Log for Logbook Entry** of log entry 8780 (3,307 view rows) took 1,339 ms in
`web_search_read`; the server log gave 10 queries, 1.322 s of SQL and 0.016 s of Python. The
search itself (the 80 ids, and the count) takes 13 ms each. The slow query is the read of the
other fields of the 80 rows: Odoo reads a record by `WHERE id IN (...)`, and the `id` of the view
is an expression (`(log.id * 1000 + ordinal) * 2`). PostgreSQL cannot use an index for it, so it
builds the whole view, with every snapshot unnested, and then filters: 1,758 ms. Every read by
id pays this: the fields that the caption needs and the list does not show, the form of one row,
the next page.

### Why the first page is wrong

The list sorts by `id DESC`. A direct row takes its id from `oteny_audit_log.id`, a child row
from `oteny_audit_log_ref.id`: two sequences that do not match (the ref sequence is at 6.97 M,
the log sequence at 4.92 M). So all child rows sort above all direct rows. On log entry 8780,
75 of the 80 rows of the first page were not among the 80 newest rows: the newest direct change
(10-Aug-2026) came after child rows back to July.

### What an insert snapshot holds that nothing else holds

An insert snapshot repeats the current record, except in three cases:

- **A record that is gone without a delete row.** 926 of the 87,265 inserted records. The causes,
  per model:
  - a database cascade: Odoo deletes the record with SQL, and PostgreSQL deletes the rows that
    point to it with `ON DELETE CASCADE`; the audit patch sees only the ORM delete of the top
    record. `crewradar.plan.item`: 453 of 463 lost with their log entry (`log_entry_id`);
    `riverflow.service`: 29 of 97 lost with their parent service (`parent_id`).
  - a delete that the audit patch does not see: during a module upgrade (the patch writes no row
    while the registry is not ready), with the context `oteny_audit_ignore`, or by SQL. The other
    68 services were archived on 23-Jun-2026 and later deleted without any audit row. The cause
    of 87 site contacts (`crewradar.site.contact`) is the same kind: their ship still exists.
- **A field that changed later without an audit row**: a database `SET NULL` when the record it
  points to is deleted, a write with `oteny_audit_ignore`, or SQL.
- **A field that the audit log does not cover** (a read-only compute, `_oteny_audit_ignore_fields`):
  not in the snapshot either, so nothing changes.

## 2. Decisions

1. **An insert writes one row: the id of the record.** `change_type = 'i'`, `field_name = 'id'`,
   `field_display_name = 'ID'`, `new_value` and `new_value_display_name` = the id, no snapshot.
   The row keeps the user, the time, the transaction, the record name and the parent refs. In
   **Audit > Log** an insert shows its header line only ("Insert Service X by Kirsten on …").
   The create also gets faster: the patch no longer reads every column of the new rows and no
   longer computes a display name for every many2one value.
2. **A change in the transaction of the create writes nothing.** It replaces the fold of cut E
   (plan `audit-storage-reduction.md`): nobody else could see the value before the commit, and the
   value at the commit is the record itself. When the change sets the parent field
   (`_oteny_audit_parent_field`, `_model_parent_keys`), the insert row still gets the parent refs,
   so a service that gets its log entry after the create shows under that log entry.
3. **A database cascade writes the rows that an ORM delete would write.** Before the delete,
   `patched_unlink` finds the records that PostgreSQL will delete or change through a foreign key
   of a stored many2one field that points to the deleted model:
   - `ondelete='cascade'`: a delete snapshot for each such record, and recursively for the
     records that point to it;
   - `ondelete='set null'`: an update row (old value: the deleted record; new value: empty), for
     an audited field.
   Only audited models and records count; the lookups are cached per registry. Cost: one
   `SELECT id ... WHERE field IN (...)` per inbound field and delete batch, of the same kind as
   the scan that PostgreSQL makes for the foreign key itself. Without this, an id-only insert
   would lose the whole content of a cascaded record.
4. **Reconstruction rule** (replaces "insert snapshot → updates → delete snapshot"): the value of
   a field at the insert is the `old_value` of the first update row of that field after the
   insert; without such a row, it is the current value of the record, or for a deleted record the
   value in its delete snapshot. The value at any time T is found the same way from the first
   update row after T. What this cannot restore: a value that changed only without an audit row
   (an upgrade, `oteny_audit_ignore`, SQL). That gap existed before for every value between two
   updates; now it also covers the value at the insert.
5. **The view sorts by time.** `_order = "create_date DESC, log_id DESC, id DESC"`: the
   transaction time, then the log row (one sequence for direct and child rows), then the field
   order inside a snapshot. `log_id` is a plain integer copy of `audit_log_id`: an order on a
   many2one sorts by the comodel's own `_order` (`id desc`) through a join, which reversed the
   direction in the first build.
6. **A read by id uses an index.** `_search` of the view model adds `audit_log_id IN (...)` to
   every search with an `id` condition at its top level (the log row ids follow from the view
   ids; for a child row through the ref id). Odoo's `fetch()` reads known records through
   `_search([('id', 'in', ids)])`, so this reaches every read. The condition pushes into every
   branch of the view as an index lookup on `oteny_audit_log`.
7. **The stored insert snapshots are stripped by the purge cron.** A new phase `inserts` of the
   one-shot scheduled action **Audit Log: Purge Rows Outside Coverage** turns each insert snapshot
   into an id row, in batches, when the record still exists or has a delete row. It keeps the
   snapshot of a record that is gone without a delete row (926 rows on the backup): it is the only
   copy of that record. Then the `vacuum` phase gives the space back. The migration of the new
   version sets the phase to `inserts` and switches the action on for the next night, 01:00 UTC
   (asked by Ries on 18-Nov-2026: the conversion runs from a cron after the upgrade, so it does
   not hold up the upgrade).
8. **`VACUUM FULL` runs in a job of its own.** Odoo's cron runner keeps the cursor that locked
   the cron row open for the whole job, and runs the action in a second cursor. The snapshot of
   that first cursor is older than the batches of the job, so a `VACUUM FULL` in the same job
   must keep every row version that the batches made dead: on `crmain` the log table grew from
   212 MB to 234 MB. So when the last batch is done, the action reports no work left (the job
   ends) and triggers itself one minute later; that new job runs `VACUUM FULL`. This also
   applies to the purge of 19.0.1.723, which ran its VACUUM in the job of its last batches.

9. **Every audit row names its record as "<what> on <where>"** (asked by Ries on 18-Nov-2026,
   after a walk of Audit > Log on `crmain` showed "Insert Message by Fred van Rookhuijzen" with
   no hint of what was posted). Since an insert holds the id only, the record name is what says
   which record a row is about. On the backup, about 19,000 insert rows had no readable name:
   15,132 messages (Odoo's display name of a message is its subject), and every row of four
   models without a name field, which showed Odoo's fallback "model,id"
   (`crewradar.entry.billing.line` 1,568, `crewradar.timesheet.log.entry` 905,
   `rivercreds.document.import.job` 1,732, `crewradar.medical.expense` 49). And 16,511
   attachment inserts named only a file, often the timestamp of a scan. `_audit_record_name` in
   `base_patch.py` builds the name at all six places that write a row:
   - a model may declare `_oteny_audit_display_name(record)`: `mail.message` gives the subject,
     else the first 80 characters of the body, else the subtype, on its document;
     `ir.attachment` gives the file name on its record;
   - else the display name; when that is empty or the fallback "model,id", the model's label
     and the id, on the record's audit parent ("Log Entry Billing Line 8934 on － Philipskercke I
     | Tan | DU | Working | 27-Nov-26").
   A name is not a field value: every audit row already stores one. Cost: 1 to 3 ms per row of
   these models (one read of the parent's name). `account.move` keeps "Draft Invoice": that is
   its true name at the insert, and the posting row shows the number. Not in this change: real
   display names for the four models (they also show "model,id" in the rest of the screens);
   `crewradar.medical.expense` and `rivercreds.document.import.job` have no audit parent, so
   their audit name is the label and the id only.

## 3. Build and checks

- `oteny_audit/models/base_patch.py`: decisions 1 to 3.
- `oteny_audit/models/oteny_audit_log_aggregated.py`: decisions 5 and 6; the caption shows only
  the header for an id row.
- `oteny_audit/models/oteny_audit_log.py`: the `inserts` phase; migration `19.0.1.747`.
- radar: `crewradar/tests/salary/test_salary_timeline_audit.py` (read the type from the record,
  not from the insert snapshot); the Wilma data analyst's description of the audit shape
  (`crewradar_wilma/models/riverflow_service_analysis.py`).
- Tests: an insert writes one id row and no update rows in its transaction; a later update writes
  its row; a parent field set after the create gives the parent ref; a cascade delete writes a
  delete snapshot for the child and its grandchild; a set-null writes an update row; an ignored
  child model writes nothing; the view order is by time over direct and child rows; a read by id
  returns the same rows as before; the purge strips an insert of a live record and of a deleted
  record with a delete row, and keeps the one without.
- Measure on `crmain`: the first page of log entry 8780 before and after; a service create and a
  service delete before and after (time and audit rows); the purge (time, WAL, sizes).

## 4. Outcome (18-Nov-2026, local; not merged)

Built on `feat/audit-insert-id-only` in otenydoo and radar; oteny_audit 19.0.1.747.

- **Measured on `crmain`** (production backup of 18-Nov-2026), purge run at once by the cron
  runner (`method_direct_trigger`, as Run Manually): first job 3.1 s (86,379 insert snapshots
  became id rows; 927 stayed, records gone without a delete row), second job `VACUUM FULL`
  1.6 s; 251 MB of WAL. `oteny_audit_log` 212 MB → 110 MB, the database 1,025 MB → 932 MB. The
  insert rows: 90 MB → 13.5 MB. The upgrade itself (`-u oteny_audit`) took 22 s; the migration
  only sets the phase.
- **First page**, Audit Log for Logbook Entry of log entry 8780: `web_search_read` 1,339 ms →
  79 ms in the browser (server 69 ms: search 36 ms, count 16 ms, read by id 2.7 ms); 3,307 →
  2,036 rows; newest change first. The form of one row: 54 ms. Audit > Log, from yesterday:
  34 ms; a busy service: 20 ms.
- **Cascade capture cost:** 1.8 ms for the delete of a service without children; a service with
  children 102 ms, because it writes the 44 rows of what PostgreSQL deletes with it.
- **End to end** in the browser on `crmain`: a contact under a company, an edit, then the delete
  of the company. The contact's audit log shows the insert as one header line, the edit, and
  the cleared company link (SET NULL) as an update row; a parent with a child wrote the child's
  delete snapshot through the cascade.
- **Found in the build:** the `VACUUM FULL` of the purge could not shrink the table inside the
  job of its batches (decision 8); an order on the many2one `audit_log_id` sorted by the log
  model's `_order` (decision 5); `fetch()` reads by `_search`, not `_as_query` (decision 6);
  `riverdeploy/crewradar_db_restore.py` refused the backup of 18-Nov-2026 (`unrecognized
  parameter "pg_attver"`, an odoo.sh column option) and now skips those lines.
- **Tests:** oteny_audit 135 tests, 0 failed (new: the id insert, no rows in the create
  transaction, cascade and set-null rows, one delete row after a cascade, order by time over
  direct and child rows, the read by id); tests that read an insert snapshot now read the delete
  snapshot or the update row. Full suite on `cr-test`, the 20 modules of `odoo.installModules`:
  0 failed, 0 errors of 6,157 tests.
- **Record names (decision 9):** on `crmain` a message on a service reads "'Flight booked : KL
  808, MNL → AMS, …' on Adelaide | Book Flight ON | Labacana Cruz | CS"; an attachment
  "image004.png on Integro | Arrange Sign OFF transport | …"; a timesheet log entry
  "Crewradar Timesheet Log Entry 7498 on Tyda Kyra | Pinero, Melchor Jay T. | October 2026".
  oteny_audit 142 tests, full suite 0 failed, 0 errors of 6,164 tests. Old rows keep their
  stored names.
- **Not done:** the release check on `test1`.
