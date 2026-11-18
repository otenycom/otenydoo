---
name: oteny-audit
description: Automatic audit logging for all Odoo models with parent/child reference tracking, aggregated views, and configurable cleanup. Covers developer integration patterns (_oteny_audit_parent_field, _oteny_audit_ignore, _oteny_audit_ignore_record, _oteny_audit_html_strip_fields, _oteny_audit_redact_fields, context flags), secret redaction so credentials never reach an audit row, an id row for each insert and a tombstone snapshot row for each delete, the rows of a database cascade, default-ignored mail/discuss/cron infrastructure models, and end-user UI for viewing audit trails. Use when adding audit parent references to models, suppressing audit for specific operations, browsing audit logs, configuring retention settings, or querying snapshot data for record reconstruction.
---

# Oteny Audit

Automatic audit logging module for Odoo 19. Patches the ORM to log all create, update, and delete operations across all models. Provides parent/child reference tracking so changes to child records (e.g., services on a log entry) appear in the parent's audit trail.

## When to Use This Skill

- Adding a new model whose changes should appear under a parent record's audit trail
- Suppressing audit logging for a model or a specific operation (e.g., bulk import)
- Browsing the audit log UI to investigate who changed what and when
- Configuring audit log retention and cleanup settings
- Understanding how the audit system works under the hood

## Quick Start

**No setup needed.** Once the `oteny_audit` module is installed, all models are audited automatically. Transient models (wizards) and system models are excluded by default.

### View the global audit log

Navigate to **Audit > Log**. The default view shows aggregated changes from yesterday onwards.

The Transaction ID column (hidden by default, turn it on in the column menu) is a link: a click adds the filter Transaction ID = <value>, so the list shows every change made in the same database transaction.

### View audit for a specific record

From any list or form view, select one or more records, then use the **Action menu (gear icon) > Audit Log**. This opens the aggregated view filtered to that record, including changes to child records.

### Configure cleanup

Navigate to **Audit > Configuration > Settings** to adjust retention period and cleanup schedule.

## Core Concepts

### Automatic ORM Patching

The module patches `BaseModel.create`, `write`, `unlink`, and `_flush` at import time. No per-model opt-in is needed. One helper, `base_patch._is_audited_field`, decides at all four places which fields are logged (19.0.1.723, plan `plans/audit-storage-reduction.md`):

- every **stored** field, also one that is `readonly=True` **without a compute** (cut I): in Odoo that means read-only on screen, not "never written", and code writes such fields — `account.move.state`, `riverflow.service.invoice_line_id` and `salary_timeline_id`, `crewradar.timesheet.billed_on_move_line_id`, `crewradar.salary.timeline.type`;
- except a **read-only computed or related** field (see below), a read-only `active` (only a retire writes it, right before the delete; a model with a user archive declares `active` writable, and that one is logged), `parent_path` (Odoo builds it from `parent_id`), the access columns (`create_uid`, `write_date`, ...), and the names in `_oteny_audit_ignore_fields` (see *Ignoring Fields*).

A selection value is stored with its label as the display value ("Incoming Email", not `email`); before 19.0.1.723 the label never showed, and the caption maps the old rows to their label.

**A stored computed field without an inverse is never logged.** It is readonly, so the log explains it through its inputs — `crewradar.timesheet.billable_overtime_hours`, `tft_hours`, `contract_work_hours` and the `settled_*` / `pending_*` fields leave no audit row, however far the value moves. To explain such a value, read the audit rows of the **manual inputs** it depends on (here `worked_hours_on_timesheet`, and the log entry's dates and status), and check the deploy history for a formula change. Live example, 2026-09-10: four timesheet corrections were traced to `worked_hours_on_timesheet` edits with a user and a timestamp, while nine others left no row at all and were explained by arithmetic plus the TFT commit of 2025-11-20. Two more limits bite in the same investigation: retention is 180 days (`oteny_audit.retention_days`, cleanup enabled), so on live nothing before that horizon survives; and `riverflow.check.result`, `crewradar.salary.line` and `crewradar.salary.timeline.segment.adjustment` carry no audit rows at all, so the salary side must be read from the records themselves.

### Which model to query

If you're reading audit data (UI, custom reports, AI snippets, ad-hoc analysis), pick the right model up front:

| Need | Model | Why |
|---|---|---|
| Field-level drill-down ("what changed on this record", "who set X to Y", group-by-field, filter by `field_name`) | **`oteny.audit.log.aggregated`** (preferred) | One row per (event, field). A delete snapshot is unnested into per-field virtual rows; an insert is one row with `field_name` `id` (since 19.0.1.747). The `'__snapshot__'` sentinel never surfaces. |
| Full snapshot dict for a single event ("show every value at delete time, all in one place") | `oteny.audit.log` (raw) | The `snapshot` JSON column lives only here. Use when you need the dict as a unit. |
| Internal: parent reference table | `oteny.audit.log.ref` | Parent refs that link a child's log rows to its parents. Joined into the aggregated view automatically; rarely queried directly. Rows with `is_direct = True` are from before 19.0.1.723: the view ignores them, and the purge removes them. |

### Three Data Models

| Model | Type | Purpose |
|-------|------|---------|
| `oteny.audit.log` | Regular | Audit log entries: one row per field change for **updates**; one row with the record's id per **insert**; one tombstone snapshot row per **delete** (see Tombstone Snapshots below) |
| `oteny.audit.log.ref` | Regular | Reference table linking a child's logs to its parents (no direct refs since 19.0.1.723: a log row shows under its own record straight from `oteny.audit.log`) |
| `oteny.audit.log.aggregated` | DB View | Reads the direct rows from the log and the child rows through the refs, **UNNESTs snapshot rows back into one virtual row per captured field** so list views, filters by `field_name`, and Group-by-Field behave the same way regardless of underlying row shape (legacy per-field, or modern tombstone). The view's `audit_log_id` column points back to the underlying log row when callers need the full snapshot. |

### Parent/Child References

When a child model declares a parent field, its audit log entries are linked to the parent record via `oteny.audit.log.ref`. This means viewing the audit log for a parent record (e.g., a log entry) also shows changes to its children (e.g., services, billing lines). Parent resolution is recursive up to 3 levels.

### Inserts and Tombstone Snapshots (Deletes)

**An insert writes one row with the record's id, and no field value** (19.0.1.747, plan `plans/audit-insert-id-only.md`, asked by Ries on 18-Nov-2026). The current values of the fields are in the record itself; a later change writes an update row with its old value; a delete writes a snapshot. The row keeps the user, the time, the transaction, the record name and the parent refs, so "who made this record, and when" stays answered. Until 19.0.1.747 the insert row held a snapshot of every field: 63 % of the bytes of the log on the production backup of 18-Nov-2026.

**A delete writes one tombstone snapshot row** whose `snapshot` JSON holds every non-blank audited field value at the delete. A delete is one logical action, not N field events; the delete snapshot is the only copy of the record once it is gone.

**Reconstruction rule.** The value of a field at time T (also at the insert) is the `old_value` of its first update row after T; without such a row it is the current value of the record, or for a deleted record the value in its delete snapshot. Walk the update rows backwards from the record (or from the delete snapshot). What this cannot restore is a value that changed only without an audit row: a write during a module upgrade (the patch writes nothing while the registry is not ready), with the context `oteny_audit_ignore`, or by SQL. That gap exists for every value between two updates; since 19.0.1.747 it also covers the value at the insert.

**A database cascade writes its rows too** (19.0.1.747). Odoo deletes a record with SQL; PostgreSQL then deletes the rows that point to it with `ON DELETE CASCADE` and clears those with `ON DELETE SET NULL`, and the ORM never sees either. Before the delete, `patched_unlink` (`_log_database_cascades` in `base_patch.py`) reads the foreign keys from `pg_constraint` (cached per registry, only tables of regular models) and writes a delete snapshot for each record a cascade deletes (recursively, not through an audit-ignored model) and an update row (old value: the deleted record, new value: empty) for each audited field a SET NULL clears. On the backup of 18-Nov-2026, 453 crew planning items and 29 services had been lost with their log entry or parent without a delete row. Cost: one `SELECT` per inbound foreign key and delete batch, 1.8 ms for a service without children. A record that a cascade already deleted gets no second, empty delete row when code deletes it again through the ORM. Not covered: the rows of a many2many relation table.

**Row shape**:

| Row kind | `change_type` | `field_name` | `new_value` | `snapshot` |
|---|---|---|---|---|
| Insert (19.0.1.747) | `'i'` | `'id'` | the record id | empty |
| Insert snapshot (before 19.0.1.747; kept only for a record gone without a delete row) | `'i'` | `'__snapshot__'` | | `{field: {raw, display, label}, ...}` |
| Update | `'u'` | the field name | the new value | empty |
| Delete tombstone | `'d'` | `'__snapshot__'` | | `{field: {raw, display, label}, ...}` |
| Remark (19.0.1.687) | `'r'` | the caller's key (for example `transition`) | the text | empty |

The caption shows an insert as its header line only ("Insert Service X by Kirsten on …"), and expands a delete snapshot into one indented line per field. **Use `field_name == '__snapshot__'` (not `change_type`) as the discriminator** for a snapshot row.

### The name of a record in its audit rows

Every audit row stores the name of its record (`record_display_name`, the header of the row in **Audit > Log**; a child row under its parent shows the same name). Since an insert holds the id only (19.0.1.747), this name is what says which record a row is about. The rule is one structure, "<what> on <where>", built by `base_patch._audit_record_name` at all six places that write a row (19.0.1.747, plan `plans/audit-insert-id-only.md`, decision 9; asked by Ries on 18-Nov-2026):

1. A model may declare `_oteny_audit_display_name(record)` when its display name does not say what the record is. Two do:
   - `mail.message` (`mail_message_override.py`): the subject, else the first 80 characters of the body as plain text, else the subtype ("Note"), then "on <document>": "'Flight booked : KL 808, MNL → AMS, …' on Adelaide | Book Flight ON | Labacana Cruz | CS". Odoo's display name of a message is its subject, and 64 % of the message inserts on the production backup of 18-Nov-2026 had none.
   - `ir.attachment` (`ir_attachment_override.py`): the file name on its record ("image004.png on Integro | Arrange Sign OFF transport | …"); a file name alone, often the timestamp of a scan, does not say which record the file belongs to.
2. Else the record's `display_name`.
3. When that is empty or Odoo's technical fallback "model,id" (a model without a name field), the model's label and the id, on the record's audit parent (`_oteny_audit_parent_field`, `_model_parent_keys`): "Log Entry Billing Line 8934 on － Philipskercke I | Tan | DU | Working | 27-Nov-26". Without an audit parent only the label and the id remain ("Medical Expense 85", "Document Import Job 9810"); a real display name in the model's own module is the better fix there, and it also mends the breadcrumbs and links that show "model,id".

A name is not a field value: it costs one read of the parent's name per row (1 to 3 ms). Rows stored before 19.0.1.747 keep their old names. `account.move` keeps "Draft Invoice": that is its true name at the insert, and the update row of the posting shows the number.

### Remarks (change type `r`)

The audit log records changed values. It cannot say which action made a change: two buttons that write the same values look the same, and an action that changes nothing leaves no row. A **remark** is a line of text that says what a person or a process did (19.0.1.687; plan `riverflow/plans/transition-in-audit-log.md`, from live service 33239, where only an elimination showed that Kirsten pressed Received already and not Upload Ship A1):

```python
self.env["oteny.audit.log"]._log_remark(records, "transition", _("Transition"), transition.name)
```

- **One row per record:** `change_type = 'r'`, `field_name` = the caller's fixed key (the same in every language, so a filter on it keeps working), `field_display_name` = the label, `new_value` and `new_value_display_name` = the text. `old_value` and `snapshot` are empty.
- **Written like a field change.** It goes through `_create_audit_logs`, so it has the user, the time, the transaction and the parent references of a field change, and the same skip rules (`oteny_audit_ignore`, an ignored model or record, registry not loaded, restore detected). A skip writes nothing.
- **On screen.** The caption gives a remark its own header ("Remark … by … on …") and the line "label: **text**", always escaped as plain text, also when the key is the name of an HTML field. The search views have the filter Remarks.
- **Not over RPC.** The leading underscore keeps the function out of the web API, so a user cannot write a false remark.
- **No field value.** The rebuild of a record (see *Inserts and Tombstone Snapshots*, reconstruction rule) skips `r` rows.
- **Callers:** riverflow writes key `transition` for every transition of the transition wizard, and key `process` for the Done cascade, the cancel cascade and the parent auto-progress (riverflow 19.0.1.1373). Tests: `test_audit_log_remark`.

### Default-Ignored Infrastructure Models

A short list of high-noise infra models is ignored by default (see `_DEFAULT_IGNORED_MODEL_NAMES` on `oteny.audit.log`). These models produce many rows with little audit value because their content already lives in the source table itself or is pure plumbing churn:

- `mail.mail` (outgoing-queue copy of mail.message bodies)
- `mail.notification` (delivery/read status)
- `mail.followers` (follower-list churn)
- `mail.presence` (online/offline pings)
- `discuss.channel` (per-conversation Wilma scratchpad)
- `discuss.channel.member` (membership churn)
- `ir.cron.progress` (cron-internal counters)
- `ir.cron.trigger` (a row in the scheduler queue; 19.0.1.723)
- `ir.model.constraint` (the reflection of database constraints, rewritten by every upgrade; 19.0.1.723)

**`mail.message` is deliberately NOT on this list** (the code comment on `_DEFAULT_IGNORED_MODEL_NAMES` says so, and `test_mail_message_is_audited_with_html_strip` pins it). Chatter on a business record is cascade-deleted with that record, so the audit log is its only durable copy afterwards; the cost is paid down with HTML stripping instead.

**A message posted into an ignored container is skipped, one record at a time.** The reason `mail.message` stays audited is the cascade-unlink guarantee above — but a message posted into a `discuss.channel` has no business record to outlive. It lives as long as the channel does and dies with it, and the channel is already judged near-zero audit value. So `mail.message` declares `_oteny_audit_ignore_record` (see *Per-Record Ignore* below) and those messages produce no audit rows. Measured on `test1` on 2026-08-25 before the change: 15,282 rows / 57 MB all time, and 248 of 259 audited message inserts in a single Barney session-day. Chatter on a real record is untouched — `test_audit_log_ignored_container.py` pins both halves.

A message with an **empty** `model` is still audited. Zero such rows existed on `test1`, so there was no measured noise to remove and no reason to widen the rule.

**Know what this costs.** The rule reaches every Discuss message, not only a bot's. A channel that is later deleted takes its messages with it, and the audit log no longer holds a copy — Odoo's own AI autovacuum (`ai/models/discuss_channel.py::_remove_ai_chat_channels`) deletes an `ai_chat` channel a day after its last interest, so a Wilma AI-chat transcript is the concrete case. Wilma's own `wilma.debug.session` / `wilma.debug.turn` remain the record of those runs. If a channel transcript must outlive its channel, the lever is that debug log's retention, not the audit trail.

A specific module can still re-enable audit on any of these by setting `_oteny_audit_ignore = False` on the model class. Production-data analysis showed ~95% of audit storage and ~50% of audit rows came from these models combined, with mail bodies in particular being ~98% redundant with content already in `mail_message`/`mail_mail`.

### Secret Redaction — the audit log never stores a credential

**Who can read the audit log decides how bad a leak is, so start there.** `security/ir.model.access.csv` grants `base.group_user` read on `oteny.audit.log`, `oteny.audit.log.ref` and `oteny.audit.log.aggregated`. There is no record rule, no audit-manager group, and no `groups=` on the **Audit** menu. The aggregated search bar matches on value (`unaccent(new_value_display_name) ILIKE …`). So any internal user can search the whole retained history for a value shape.

Two things then made a credential leak, rather than merely a risk:

1. The create/delete snapshot is built from a raw `SELECT` on the table, which **bypasses Odoo's field-level `groups=`** — the very thing that protects `ir.mail_server.smtp_pass` and `iap.account.account_token` everywhere else.
2. An **update** row keeps the old value beside the new one, so a rotation preserves the superseded credential rather than replacing it.

Since `19.0.1.519` a credential value is replaced by the placeholder `***redacted***` before it reaches an audit row, on every change type and in both storage shapes. The row itself survives — who, when, which record, which field. Only the value goes. Three levers, listed by how often you reach for one (`base_patch._is_secret_field` checks `_oteny_audit_redact_fields` first, then the name shape, then the record-aware hook):

| Lever | Where | Use it for |
|---|---|---|
| **Field-name shape** (automatic) | `_SECRET_FIELD_NAMES`, `_SECRET_NAME_MARKERS`, `_SECRET_FIELD_SUFFIXES` in `base_patch.py` | The default, and what covers real code. No declaration needed, on any model in the database. |
| **`_oteny_audit_redact_fields`** | a set on the model class | A credential the name shape cannot see. Mirrors `_oteny_audit_html_strip_fields`. It is also the only lever that can force a numeric or relational field. Nothing in production declares it today. |
| **`_oteny_audit_redact_record_field(field_name, record)`** | a method on the model class | The answer depends on the RECORD, not the field. Only `ir.config_parameter` needs it today. |

```python
class MyModel(models.Model):
    _name = "my.model"
    _oteny_audit_redact_fields = {"vault_handle"}   # a name the shape genuinely misses
```

**What the name shape catches**, in `_is_secret_field_name`:

- **exact names** — `password`, `passwd`, `secret`, `token`, `api_key`, `apikey`, `access_token`, `refresh_token`, `private_key`, `smtp_pass`, `pin`, `credential`;
- **marker words anywhere in the name** — `password`, `passwd`, `secret`, `token`, `apikey`. A suffix rule alone missed `stripe_secret_key` and `google_calendar_rtoken`;
- **the suffixes** `_key` and `_credential`. `_key` is the largest credential family in core and enterprise: `openai_key`, `avalara_api_key`, `sendcloud_secret_key`, `wise_api_key`, `partner_key`, `app_key`.

`credential` is exact rather than a marker word, because rivercreds names a whole family `credential_*` after work-permit **documents** — none of them a secret.

**Two deliberate holes in the shape.** Bare `key` is in none of the sets, so `ir.config_parameter.key` (the parameter NAME an auditor must read) and `sign.template.dto.override.key` are never touched. And `_NOT_SECRET_FIELD_NAMES` spares the two measured `*_key` lookup keys: `wilma.api.cache.cache_key` and `rivercreds.document.import.job.gemini_request_key`, which is literally `job-<id>`. Add a name there only when you have measured that it is not a credential.

**The field-TYPE gate is a DENY list**, `_NON_SECRET_FIELD_TYPES`: integer, float, monetary, boolean, date, datetime and the relational types. Everything else — char, text, html, **binary, json**, selection — can hold a credential. It started as an allow-list of `{char, text, html}`, and that silently let `ir.mail_server.smtp_ssl_private_key` write a full PEM into the audit log on every mail-server edit: the field is `Binary(attachment=False)`, a real `bytea` column, and the audit patch reads it with raw SQL that also bypasses its `groups="base.group_system"`. The deny list keeps the property that motivated the gate — an Integer counter (`tokens_in`, `token_count`) or a Boolean (`is_token_out_of_sync`) can never be caught by accident. `_oteny_audit_redact_fields` bypasses the gate, because you asked for it explicitly.

**`ir.config_parameter` is the record-aware case.** Every system parameter stores its content in the same `value` column, so `web.base.url` and `ai.anthropic_key` are the same field. `ir_config_parameter_override.py` therefore keys on the parameter name, and then on the value shape:

- `is_secret_param_key()` splits the key on `.` and `_` and matches WORDS — `key`, `token`, `secret`, `password`, `passwd`, `credential`, `apikey`. A substring test over-matched badly: `wilma.llm_max_tokens` (the word is `tokens`) and `portal.allow_api_keys` (`keys`) both looked like credentials. Two real keys are explicit exceptions in `_NOT_SECRET_PARAM_KEYS`: `auth_signup.reset_password` is a mode (`b2b` / `b2c`) and is exactly the setting an auditor wants to watch, and `recaptcha_public_key` is public by design.
- `is_secret_param_value()` then spares a shape no credential has — a flag, or a number of eight digits or fewer. That is what rescues `auth_password_policy.minlength`, whose key says password and whose value is `8`.

The `key` field itself is never redacted, both for the auditor and because `_check_and_disable_on_restore` matches `record_display_name == "database.uuid"`.

**Already-stored secrets are scrubbed once, by migration.** `migrations/19.0.1.519/post-migrate.py` redacts — never deletes — the rows written before this existed. It reaches both shapes, because a scrub of only the flat `old_value`/`new_value` columns leaves every insert and delete untouched, and for a rotated-out parameter the DELETE tombstone is exactly the row that preserved the old secret.

The scrub imports `_is_secret_field_name`, `is_secret_param_key` and `is_secret_param_value` from the runtime, so the two cannot disagree about which NAME or which VALUE is a credential. They differ in exactly two ways, both in the safe direction — the scrub redacts a little more, never less. It has no field-TYPE gate, because a stored row carries no field object, so it also redacts an Integer or Boolean whose name matches (one row on `test1`). And it cannot read a per-class `_oteny_audit_redact_fields`, which nothing declares in production; a declaration added later needs its own scrub, because a migration is forward-only. `test_audit_log_secret_scrub_migration.py` pins the rest.

> **Redaction is not rotation.** A value that sat in the audit log was readable by every internal user for as long as it was there. The migration removes it going forward; it does not un-expose it. Rotate anything that was exposed — the list and the owner are in the roadmap below.
>
> The migration logs its summary at **INFO**, not WARNING. It replays on every copy of the database — each staging rebuild, each production restore, each laptop restore — and re-redacts the same rows the restore just brought back, so at WARNING it turned every odoo.sh build amber for work nobody can do on that copy. A build that is always amber is one nobody reads. The rotation is a one-time human task and it lives in the roadmap, not in a log line.

**Known residual.** `record_display_name` — the RECORD's name, not a field value — is not redacted, and it propagates into `oteny_audit_log_ref.target_display_name` / `parent_display_name`. No model in this database has a credential as its `_rec_name`, so nothing leaks through it today. A model that did would need the display name redacted at all four emitting sites.

### Per-Record Ignore (`_oteny_audit_ignore_record`)

`_is_audit_ignored` answers per **model**. One case needs finer grain: a model that is audited in general, where a particular *record* has no audit value. Declare a method on the model class and `base_patch._record_audit_ignored` calls it at each emitting site:

```python
class MailMessage(models.Model):
    _inherit = "mail.message"

    def _oteny_audit_ignore_record(self, record):
        target = record.model
        if not target:
            return False
        return self.env["oteny.audit.log"]._is_audit_ignored(target)
```

Notes for anyone adding a second one:

- **Declare it only where it is needed.** A model without the attribute costs one failed `getattr`, and `patched_flush` skips its whole precompute pass.
- **It fails OPEN.** If the hook raises, the record is audited as usual. An audit hook must never break the write it observes.
- **Answer the same way at create and at delete.** The four sites are `patched_create`, the relational branch of `patched_write`, `patched_flush` and `patched_unlink`. A predicate that changes its mind between insert and delete leaves a half-recorded record.
- **Give the purge its SQL.** Declare `_oteny_audit_purge_record_sql()` beside it: it returns the SQL condition (over the alias `log`) that finds the stored rows of the records the rule skips, for the one-shot purge of 19.0.1.723 (see *The purge of the rows outside the coverage*). The condition must not rest on an insert snapshot key that the fold can add.

The rules today (19.0.1.723):

| Model | Skips | Where |
|---|---|---|
| `mail.message` | a message posted into an audit-ignored container (`discuss.channel`) | `oteny_audit/models/mail_message_override.py` |
| `ir.attachment` | a generated image (`res_field` `thumbnail`, `image_128` to `image_1024`) and a web asset bundle (`url` `/web/assets/…`) | `oteny_audit/models/ir_attachment_override.py` |
| `crewradar.salary.timeline` | a reconciliation timeline: only the builder makes, changes and deletes it; its estimate stays audited | radar `crewradar/models/salary/crewradar_salary_timeline.py` |

### HTML Stripping for Selected Fields

Fields listed on a model's `_oteny_audit_html_strip_fields` set are stored in the audit log as plain text (HTML tags removed, common entities like `&lt;`/`&nbsp;` decoded, whitespace collapsed). This is meant for HTML-shaped fields that bloat audit storage 10x with markup the user never reads, while preserving full prose readability. Production sampling on real chatter bodies showed 89–95% size reduction; the stripped output is fully readable for forensic purposes.

```python
class MailMessage(models.Model):
    _inherit = "mail.message"
    _oteny_audit_html_strip_fields = {"body"}
```

The strip applies to both raw values (`old_value` / `new_value`) and display values (`old_value_display_name` / `new_value_display_name`), and works inside snapshot rows as well as per-field update rows.

### MailThread Tracking Replaced

The module disables Odoo's built-in `MailThread` tracking (which logs to the chatter) by patching `MailThread.create` and `write` to set `tracking_disable=True`. This avoids duplicate logging and improves performance.

### Auto-Setup of Action Menu Items

On module install or upgrade, server actions are automatically created for every eligible model. These actions add an "Audit Log" entry to each model's Action menu (gear icon). This runs via `registry_patch.py` on registry invalidation and via `ir_module_module.py` after runtime module installs.

## End User UI Guide

### Menu Structure

| Menu Path | Description |
|-----------|-------------|
| **Audit > Log** | Aggregated audit log (default view, yesterday onwards) |
| **Audit > Configuration > Settings** | Cleanup and retention settings |
| **Audit > Configuration > Plain Audit Log** | Raw log entries (technical, one row per field) |
| **Audit > Configuration > Enable Audit Log** | Developer-mode action to reinstall audit actions |

### Aggregated Log View

The main audit log view (`Audit > Log`) shows changes with formatted **Caption** entries:

- **Header line**: Shows the verb (Insert/Update/Delete), model name, record name, user, and timestamp
- **Detail lines**: Indented below the header, showing each field change with old and new values
- Headers are only shown when the record or transaction changes, keeping the view compact

By default only direct (parent) logs are shown. Child logs appear when viewing from a specific record via the Action menu.

### Search and Filters

**Time filters**: Today, Yesterday onwards, Last week, Last month

**Type filters**: Inserts, Updates, Deletes, Child Logs, Parent Logs

**Group by**: Parent Model, Parent Record, Record, Field, Field Model, Change Type, User, Date, Transaction ID, Child Model

### Viewing Audit for a Record

1. Open any list or form view
2. Select one or more records
3. Click the **Action menu** (gear icon)
4. Select **Audit Log**
5. The aggregated view opens, filtered to the selected record(s), including child record changes

### The Audit tab of a form

Every audited form that has tabs shows **Audit** as its last tab (19.0.1.747, plan `plans/audit-insert-id-only.md`, decision 10; asked by Ries on 18-Nov-2026). It holds the newest 160 rows of the record's audit log, the same rows as the Action menu's **Audit Log for …** (the record's own changes and its children's), grouped per database transaction: a header with the user and the time, the changes of that transaction below it, and an alternating background per transaction. Below the rows, "160 of N rows; **Full Screen** shows all." links to the whole list (a record with 160 rows or fewer shows the link alone).

- **When it reads:** only when the user opens the tab (Odoo draws a notebook page only then), and again after a save (the record's `write_date` changes). On log entry 8780 of the production backup (2,036 rows): 113 ms from the click to the rows.
- **How it is built, without code per model:** `models/base_audit_tab.py` extends `_get_view` on `base` and appends the page (`name="oteny_audit_log"`, `invisible="not id"`) to the form's main notebook, for a stored, audited model with `write_date`; Odoo caches the arch per view. The page holds the view widget `oteny_audit_log` (`static/src/widgets/audit_log_tab/`). The widget asks for the captions with the context `oteny_audit_caption_without_user`, so the record headers leave out "by <user> on <time>", which the transaction header shows once.
- **Switch it off** on a model with `_oteny_audit_form_tab = False`. A form without tabs gets no page: 131 audited models keep only the Action menu.
- **Who sees it:** every internal user, because every internal user reads the audit log today; when the roadmap item "Restrict who can read the audit log" is built, the page gets that group.
- **After a deploy** the web client may draw the form from its own view cache once; the next load shows the tab.
- **Tests:** `tests/test_audit_form_tab.py` (which forms get the page, the caption without the user) and `static/tests/audit_log_tab.test.js` (Hoot, desktop only; it runs in radar's `crewradar/tests/test_hoot.py`, whose suites include `@oteny_audit` since 19.0.1.747).

## Configuration

### The cleanup cron and database restores

`Audit Log: Cleanup Old Records` is a batch delete over the largest table in the database, and it is the reason a restore must not be run with a server still up. `pg_restore` re-adds constraints last, so a server that reconnects mid-load and fires this cron deletes `oteny_audit_log` rows while `oteny_audit_log_ref_audit_log_id_fkey` does not exist yet. The refs are orphaned, the foreign key can never be re-added, and the database cannot be upgraded again.

`riverdeploy/crewradar_db_restore.py::stop_odoo_servers` now stops such a server before the load, and `migrations/19.0.1.519/pre-migrate.py` repairs a database that already fell in. Full account, with the lock-diagnosis query, in the restore reference under *A running `crmain` server wrecks the restore*.

### The purge of the rows outside the coverage (19.0.1.723)

The coverage rules of 19.0.1.723 apply to new rows. The scheduled action **Audit Log: Purge Rows Outside Coverage** makes the stored rows match them, once (plan `plans/audit-storage-reduction.md`, § 11). It is inactive by default; the 19.0.1.723 post-migrate switches it on for the next night at 01:00 UTC, and it switches itself off at the end.

- **Phases**, kept in the system parameter `oteny_audit.purge_phase` (and `oteny_audit.purge_next_id`), one batch per call; Odoo's cron loop calls again while the progress reports work left (`ir.cron._commit_progress`):
  `deletes` (rows of ignored or removed models, update rows of fields that are not audited, rows that a per-record rule skips through `_oteny_audit_purge_record_sql`) → `fold` (updates in the transaction of their record's insert go into the snapshot; snapshots lose the keys that the rules now leave out; parent refs of folded rows move to the insert row) → `refs` (the direct refs) → `inserts` (19.0.1.747, below) → `vacuum` (`VACUUM (FULL, ANALYZE)` of the two tables on its own autocommit connection, `lock_timeout` 60 s; on a timeout the next night tries again) → `done` (a later run only switches the action off). Inside a cron run, also Run Manually, the action is switched off by `_commit_progress(deactivate=True)`, never by a write: Odoo refuses a write on a cron that is running, and on test3 on 7-Nov-2026 that refusal rolled back the end of the purge twice.
- The parameters are written with `oteny_audit_ignore`, or each batch would add audit rows to the table it purges.
- **Phase `inserts`** (19.0.1.747): an insert snapshot becomes an id row when its record still exists or has a delete row; the snapshot of a record that is gone without a delete row stays, because it is the only copy. The 19.0.1.747 migration sets the phase to `inserts` and switches the action on for the next night. Measured on `crmain` (backup of 18-Nov-2026): 3.1 s for 86,379 rows (927 snapshots kept), `VACUUM FULL` 1.6 s, 251 MB of WAL; `oteny_audit_log` 212 MB → 110 MB.
- **`VACUUM FULL` runs in a job of its own.** Odoo's cron runner keeps the cursor that locked the cron row open for the whole job, so its snapshot is older than the batches of the job, and a `VACUUM FULL` in that job must keep every row version they made dead (on `crmain` the log table grew from 212 MB to 234 MB). When the last batch is done, the action reports no work left and triggers itself one minute later; the new job runs `VACUUM FULL`.
- **Measured on `crmain`** (production backup of 27-Oct-2026, laptop): 88 calls, 77 s (deletes 38 s, fold 26 s, refs 2 s, VACUUM FULL 10 s), 2.27 GB of WAL. 1,118,457 log rows → 270,692; 1,497,725 refs → 231,380; the audit tables 1,065 MB → 289 MB; the database 1,984 MB → 1,208 MB.
- `VACUUM FULL` locks both tables while it runs, so almost every business write waits; that is why the action runs at night.
- **On a copy whose crons are off** (a staging build, a laptop), run it by hand (Settings › Technical › Scheduled Actions › Run Manually, repeated until it switches off), or in a shell: call `env["oteny.audit.log"]._purge_rows_outside_coverage()` until `oteny_audit.purge_phase` is `done`, with a commit between the calls. `_purge_all_now()` runs the phases without a commit and without VACUUM, for tests.

### Cleanup Settings

Navigate to **Audit > Configuration > Settings**:

| Setting | Default | Description |
|---------|---------|-------------|
| Enable Audit Log Cleanup | Enabled | Toggle automatic cleanup on/off |
| Retention Period | 180 days | Records older than this are deleted |
| Cleanup Batch Size | 5000 | Records deleted per batch (prevents timeouts) |
| Cleanup Pause | 5 seconds | Pause between batches (reduces DB load) |
| Cleanup Cron Time | `0 2 * * *` | Cron schedule (default: 2 AM daily) |

The cleanup runs as a scheduled action (`Audit Log: Cleanup Old Records`). It processes in batches with commits between each batch to prevent memory issues and timeouts. Orphaned reference records are cleaned up after the main deletion.

### System Parameters

Settings are stored as `ir.config_parameter` values:

| Parameter | Default |
|-----------|---------|
| `oteny_audit.cleanup_enabled` | `True` |
| `oteny_audit.retention_days` | `180` |
| `oteny_audit.batch_size` | `5000` |
| `oteny_audit.cleanup_pause_seconds` | `1` |
| `oteny_audit.cleanup_cron_time` | `0 2 * * *` |

## Technical Reference

### Developer Integration

#### Configuring Parent References

Set `_oteny_audit_parent_field` on a model class to link its audit logs to a parent record. This is the most common integration point.

**Single relational field** (most common):

```python
class RiverflowService(models.Model):
    _inherit = "riverflow.service"
    _oteny_audit_parent_field = "log_entry_id"
```

**Tuple format** for polymorphic references (model + id fields):

```python
# Predefined in OtenyAuditLog._model_parent_keys:
_model_parent_keys = {
    "mail.message": "model,res_id",
    "ir.attachment": "res_model,res_id",
    "mail.followers": "res_model,res_id",
    "res.partner": "parent_id",
}
```

Parent resolution is recursive. If model A points to model B, and model B points to model C, then A's audit logs appear under both B and C (up to 3 levels deep).

**Crewradar examples** (13 models use this pattern):

| Model | Parent Field | Parent Model |
|-------|-------------|--------------|
| `riverflow.service` | `log_entry_id` | Log Entry |
| `crewradar.contract` | `employee_id` | Employee |
| `crewradar.timesheet` | `employee_id` | Employee |
| `crewradar.tft.contract` | `employee_id` | Employee |
| `crewradar.vacation.payout` | `employee_id` | Employee |
| `crewradar.rate` | `product_id` | Product |
| `crewradar.product.country.konto` | `product_id` | Product |
| `crewradar.site.required.crew` | `site_id` | Site |
| `crewradar.site.contact` | `site_id` | Site |
| `crewradar.entry.billing.line` | `log_entry_id` | Log Entry |
| `crewradar.timesheet.log.entry` | `timesheet_id` | Timesheet |
| `crewradar.service.leg.pax` | `leg_id` | Service Leg |
| `crewradar.log.entry.vacation.scheme.segment` | `log_entry_id` | Log Entry |

#### Ignoring Models

**Permanently ignore a model** by setting a class attribute:

```python
class CrewradarMissingTimesheet(models.Model):
    _name = "crewradar.missing.timesheet"
    _oteny_audit_ignore = True
```

**Ignoring computed/regenerated sub-tables**: When a parent record's children are fully rebuilt (deleted and recreated) during regeneration, auditing the children creates noise. Use `_oteny_audit_ignore = True` on the child models and keep audit enabled on the parent so users can track who created/changed it.

**Salary sub-models** (5 models ignored — all regenerated by timeline builder):

| Model | Reason |
|-------|--------|
| `crewradar.salary.timeline.segment` | Segments rebuilt on regenerate |
| `crewradar.salary.timeline.segment.adjustment` | Adjustments rebuilt on regenerate |
| `crewradar.salary.line` | Salary lines rebuilt on regenerate |
| `crewradar.salary.check.result` | Check results rebuilt on regenerate |
| `crewradar.salary.export.field` | Export fields rebuilt on regenerate |

The parent `crewradar.salary.timeline` keeps audit enabled for tracking creation and state changes, but only for estimates and sandboxes: a **reconciliation** timeline is skipped per record (19.0.1.723, see *Per-Record Ignore*). Only the builder makes, changes and deletes it, and the estimate that owns it stays audited with its `reconciliation_timeline_id`.

**`crewradar.salary`** is audited with `_oteny_audit_parent_field = "timeline_id"` because it has a user-editable `remarks` field. Builder operations (create/delete during timeline build/rebuild) use `oteny_audit_ignore=True` context to suppress regeneration noise. Only user edits to the salary (like changing remarks) are captured.

**Bot and agent activity logs** — *a log of a log has no audit value.* A model that exists to record what an AI agent did is already the durable record of that event. It is written once, by a machine, and no ordinary user may write it at all. Auditing it copies every row into `oteny.audit.log` a second time, and the copies then bury the business changes an auditor opened the trail to read. Ignore the model instead:

| Model | Written by | Reason |
|-------|-----------|--------|
| `oteny.bot.session` | the external Oteny bot, over `/json/2/` (`record_activity`) | The model IS the activity log — one request/response exchange, read under **Bot Activity** |
| `oteny.bot.turn` | same | Per-LLM-call detail under an already-ignored session |
| `wilma.debug.session` | the in-process Wilma `ai.agent` | Debug capture of one chat session |
| `wilma.debug.turn` | same | Per-LLM-call detail under an already-ignored session |
| `wilma.scratch.frame` | same | Per-conversation scratchpad |
| `wilma.validator.prompt.cache` | same | Cache of prompt responses |
| `wilma.api.cache` | same | Verbatim copy of a Google Places / Routes / Gemini response, regenerable by re-calling the API and vacuumed daily |
| `wilma.transport.review` | same | Regenerated daily by refresh-and-replace; no field is human-writable |

The rule of thumb: if the record answers *"what did the agent do?"* rather than *"who changed this business fact?"*, set `_oteny_audit_ignore = True` on it. Keep audit on the agent's **configuration** record (`oteny.bot` itself — the seam user, the home channel, the uplink ref), because a human does change that and it is security-relevant.

**The two tests that decide it.** Read the ACL and the views before you set the flag. If `base.group_user` is `1,0,0,0` and every form field is `readonly="1"`, no ORDINARY user can have written the row — an administrator still can, from the shell, so this test bounds who is in scope rather than proving nobody wrote it. Add the regeneration test, which is the load-bearing one: if a cron rebuilds the record from scratch on a clock, the audit rows record the machine overwriting its own draft, and re-running the job reproduces the content anyway. `wilma.transport.review` fails both tests, which is why it is on the list despite holding real AI output. Keeping that output past its retention window is a **retention** question — the lever is `wilma.review_retention_days`, not the audit log.

The counter-example is `crewradar.salary`: it has one genuinely human field (`remarks`), so it keeps audit and the builder suppresses its own churn with the `oteny_audit_ignore=True` context instead. Reach for that half-measure only when a human field really exists.

One thing this rule does **not** reach, so measure before you assume the noise is gone:

- **Ephemeral latch/lock fields on the config record.** `oteny.bot.login_dance_until` and `login_dance_user_id` churn once per attended-login click and are audited as ordinary updates on `oteny.bot`. The third field of that latch, `login_dance_token`, no longer stores its value — the name shape redacts it (see *Secret Redaction*).

**Discuss messages the bot posts** used to be the largest share of this noise. They are `mail.message` rows, and since `19.0.1.519` a message posted into a `discuss.channel` is skipped per record. See *Default-Ignored Infrastructure Models*.

**Regenerated or system-written models ignored since 19.0.1.723** (plan `plans/audit-storage-reduction.md`):

| Model | Reason |
|-------|--------|
| `rivercreds.plan.item` | The slot compute rebuilds every item from audited inputs (credentials, log entries, requirement windows); the views have create, edit and delete off. The daily cron that moves the planning window wrote about 4,000 date updates a day, 46 % of all audit rows |
| `oteny.oops.log` | The error log itself: a log of a log |

**Ignored by default** (no configuration needed):
- Transient models (wizards)
- Infrastructure models on `_DEFAULT_IGNORED_MODEL_NAMES` (mail/discuss/cron — see Default-Ignored Infrastructure Models above)
- System model prefixes: `oteny.audit`, `ir.ui.view`, `ir.model.data`, `bus.`, `mail.push`, `mail.tracking`, `res.device.log`

#### Ignoring Fields (`_oteny_audit_ignore_fields`)

Declare a set of field names that the audit log skips on a model (19.0.1.723): no update row, and no key in the insert and delete snapshots. Use it for a field whose every value follows from other audited fields, or that only the system writes and that says nothing about a business fact.

```python
class Service(models.Model):
    _inherit = "riverflow.service"
    _oteny_audit_ignore_fields = {"end_date_for_calendar"}
```

**The sets of all classes are joined.** A class attribute of an Odoo model is normally overridden by a later class in the inheritance chain, so a mixin's set would vanish as soon as the model declares its own. `base_patch._ignored_field_names` joins the sets over the MRO (and caches the result on the registry class), so each module declares only the fields it owns. The declarations today:

| Model or mixin | Fields | Why |
|---|---|---|
| `riverflow.service` | `end_date_for_calendar` | `end_date or deadline`; its inverse writes `end_date`; both audited |
| `riverflow.tombstone.mixin` | `to_be_deleted` | written by `_retire()` right before the delete |
| `riverflow.highlight.row.mixin` | `user_write_date` | changes with every user write; repeats the time of the audit row |
| `riverflow.state.mixin` (bot mixin) | `bot_released_token` | a one-time token |
| `mail.message` | `reply_to`, `record_alias_domain_id`, `record_company_id`, `email_layout_xmlid`, `email_add_signature`, `reply_to_force_new`, `message_id` | how Odoo delivered the message; `email_from` stays (the outside sender of an incoming mail) |
| `ir.attachment` (oteny_audit) | `index_content`, `checksum`, `mimetype`, `file_size` | technical values of the file; `store_fname` stays, the content hash |
| `ir.attachment` (oteny_cloud_filestore) | `cloud_local_only` | a flag of the Cloud filestore |
| `res.users` | `oauth_access_token`, `odoobot_state`, `odoobot_failed` | written by Odoo at every OAuth login, and the OdooBot onboarding |
| `oteny.article` | `html_field_history` | the article's own revision history |

#### HTML Stripping (`_oteny_audit_html_strip_fields`)

Declare a set of field names that should be HTML-stripped before storage:

```python
class MyModel(models.Model):
    _name = "my.model"
    _oteny_audit_html_strip_fields = {"body"}
```

Used for HTML-shaped fields where the markup dwarfs the prose (e.g. mail bodies, rich-text descriptions). The strip:
1. Removes all tags (`<...>` → space)
2. Decodes common HTML entities (`&lt;`, `&amp;`, `&nbsp;`, `&quot;`, …)
3. Collapses runs of whitespace

The strip applies to raw values, display values, snapshot entries, and per-field update rows. Other HTML fields on the same model are unaffected unless explicitly listed.

#### Secret Redaction (`_oteny_audit_redact_fields`)

Declare a set of field names whose value must never reach an audit row:

```python
class MyModel(models.Model):
    _name = "my.model"
    _oteny_audit_redact_fields = {"vault_handle"}
```

You rarely need it, and nothing in production declares it. A field is redacted automatically, on every model in the database, when its name carries a marker word (`password`, `passwd`, `secret`, `token`, `apikey`), ends in `_key` or `_credential`, or is one of the exact names `password`/`passwd`/`secret`/`token`/`api_key`/`apikey`/`access_token`/`refresh_token`/`private_key`/`smtp_pass`/`pin`/`credential` — unless its type is numeric, boolean, a date or a relation. Declare the set only when the name shape misses your field, or when a numeric or relational field must be redacted. When the answer depends on the record rather than the field, implement `_oteny_audit_redact_record_field(field_name, record)` instead. Full rationale, the type deny-list and the `ir.config_parameter` case are under *Secret Redaction* in Core Concepts.

#### Per-Record Ignore (`_oteny_audit_ignore_record`)

Declare a method to skip individual records of a model that is otherwise audited:

```python
class MailMessage(models.Model):
    _inherit = "mail.message"

    def _oteny_audit_ignore_record(self, record):
        return bool(record.model) and self.env["oteny.audit.log"]._is_audit_ignored(record.model)
```

It runs at all four emitting sites and fails open. See *Per-Record Ignore* in Core Concepts before adding a second one — the trap is a predicate that answers differently at insert and at delete.

#### Context-Based Suppression

Use the `oteny_audit_ignore` context flag to temporarily suppress logging for specific operations:

```python
# Suppress audit for a bulk operation
records.with_context(oteny_audit_ignore=True).write({"field": value})

# Explicitly enable (useful when parent context has it disabled)
records.with_context(oteny_audit_ignore=False).write({"field": value})
```

Common use cases:
- **Bulk imports**: Suppress logging during Excel/CSV imports to avoid noise
- **CLI operations**: The crewradar CLI client sets `oteny_audit_ignore=True` for all operations
- **Generated records**: Suppress logging when creating computed/generated records (e.g., missing timesheet records)

#### Audit Mixin (Optional)

Inherit `oteny.audit.mixin` to add an `audit_log_ids` computed field to a model. This enables showing audit logs directly on a record's form view.

```python
class MyModel(models.Model):
    _name = "my.model"
    _inherit = ["my.model", "oteny.audit.mixin"]
```

This is optional -- the Action menu approach works without the mixin.

#### Database Restore Detection

The module detects database restore/copy operations (by monitoring changes to `database.uuid`) and permanently disables auditing for the remainder of the process. This prevents schema mismatch errors during restore operations.

**Consequence for restored DBs (dev / profiling).** A database restored from a backup accumulates **no new audit rows**, and `patched_write` / `patched_flush` short-circuit to passthroughs once the restore is detected. Worth knowing when profiling an `-u` update on a restored DB: time the profiler attributes under `patched_write` there is the *real* ORM write path, **not** audit overhead — auditing is off. To measure true audit cost, profile on a live (non-restored) DB.

### How Changes Are Captured

1. **Create**: `patched_create` runs after `original_create` and writes one row per created record with its id (`field_name` `id`, no snapshot; 19.0.1.747). It reads no column of the new rows.
2. **Write (relational)**: `patched_write` captures old values before write, then reads new values after write, logs immediately for m2m/o2m fields (per-field `change_type='u'` rows).
3. **Write (scalar)**: `patched_write` captures old values from cache, then `patched_flush` compares old vs new after the ORM flush, logging the difference (per-field `change_type='u'` rows).
   **No row in the transaction of the create** (19.0.1.747; before, cut E of 19.0.1.723 folded the value into the insert snapshot). A change of a record in the transaction of its create writes nothing (the insert row ids are kept in `precommit.data["oteny_audit"]["insert_log_ids"]`): nobody else could see the value before the commit, and the record holds the value at the commit. When the change sets the field that decides the parent (`_oteny_audit_parent_field`, `_model_parent_keys`), the insert row gets the missing parent refs (`_parent_refs_for_insert_transaction`), so a service linked to its log entry after the create still shows under that log entry. When the insert row is gone (deleted in the transaction, or rolled back to a savepoint), the change is logged as an update row. A test that checks an update row writes after `tests/common.py:end_transaction(env)`.
4. **Unlink**: `patched_unlink` reads all stored field values before deletion, then folds every non-blank value into a single **tombstone snapshot row** with `change_type='d'`, `field_name='__snapshot__'`, and `snapshot={...}`. One row per deleted record. Then `_log_database_cascades` writes the rows of what PostgreSQL deletes or clears with it (see *Inserts and Tombstone Snapshots*).
5. **References**: After creating log records, `_create_audit_log_references` creates the parent refs (log points to its parent chain, up to 3 levels). Since 19.0.1.723 it writes no direct ref: the view reads a log row under its own record from `oteny_audit_log` itself (cut F; the direct refs were two thirds of the ref table).

These four are also the four **emitting sites** — the only places a field value becomes an audit row. Each one runs `_record_audit_ignored` on the record (the per-record skip) and `_maybe_redact` on every raw and display value (the credential redaction). A change to either rule must touch all four, or it leaks through the one it missed; that is why the write-relational branch carries a redaction call it cannot currently need.

### Transaction-Scoped Deduplication

The module uses `env.cr.precommit.data["oteny_audit"]` to store transaction-scoped state:
- `old_values`: Pre-write field values for scalar fields
- `logged_changes`: Set of (field, record_id) already logged in this transaction
- `newly_created_records`: Records created in this transaction (used to skip default-vs-changed comparisons for fields that arrive via flush after a create)
- `insert_log_ids`: The insert row of each record created in this transaction (a change of such a record writes no row)
- `most_recent_logs`: Prevents duplicate log entries for the same old->new transition

### The transaction number is a 64-bit column

Every audit row carries `transaction_id`, the PostgreSQL transaction number from `SELECT txid_current()`, so the screens can group the rows of one business action. That counter is 64-bit and belongs to the whole PostgreSQL server: every write transaction in every database on that server advances it, and a logical move to another server (dump and restore) replaces it with the new server's value. The field is declared with the module's own `BigInteger` (`oteny_audit/models/oteny_audit_fields.py`, `fields.Integer` with column type `int8`) on the log, the ref and the aggregated view.

Why: until 19.0.1.598 the field was a plain `fields.Integer`, which Odoo maps to `int4` (maximum 2,147,483,647). On 2026-09-12 at 05:30 the production database passed that number. Every audit insert then failed with `integer out of range`, and because the audit row is written inside the business transaction, every create and write in the system failed with it. The first backend page load after the 2026-09-11 deploy had to rebuild the web client asset bundles (`ir.attachment` rows); that insert failed too, the bundles returned HTTP 500 and every user saw a white screen. test1 and test2 restore from production and carried the same counter. A fresh test database never reaches 2^31, so no test could catch it.

Repair: `migrations/19.0.1.598/pre-migrate.py` drops the aggregated view (PostgreSQL will not alter a column a view selects), converts both columns to `bigint`, and the module's `init()` rebuilds the view in the same upgrade. The conversion rewrites the two tables (about 2.4 GB on production) and rebuilds the `transaction_id` index, so expect a few minutes with the audit tables locked. The field type makes Odoo's own column check perform the same conversion on a plain `-u`, and keeps a fresh install correct. Tests: `tests/test_audit_log_transaction_id_bigint.py`.

Why it took only one year (measured 2026-09-14, read-only on production and on two local restores): the counter is not ours. Production's own writes are under 1% of its growth; the rest comes from other databases on the same Odoo.sh PostgreSQL server. Until 2026-02-04 the server advanced about 1.2 M per day. Between 22:25 and 23:25 UTC on 2026-02-04 the value jumped from 174,132,186 to 884,353,610 while the hourly cron kept its schedule on both sides, so the database was logically moved to an older, busier server. Since then the growth is a flat 5.8 M per day (67 per second, weekends included). From 884 M at that rate, 2^31 was 217 days away, which gives 2026-09-12. Read the counter without consuming a number via `txid_snapshot_xmax(txid_current_snapshot())`; plain `txid_current()` assigns one.

Headroom: `int8` lasts about 4 billion years at this rate. The web client's exact-integer limit (2^53) comes first, after about 4 million years, and a future server move cannot bring either limit near (it would need an epoch in the millions). The fastest `id` sequence, `oteny_audit_log_ref_id_seq` at 6.5 M after one year, needs 250+ years to reach 2^31. Three properties of the value to keep in mind: it can go DOWN after a future logical move, so it is not unique over time (only the Group by Transaction ID filters in the list views would then merge unrelated rows; the aggregated view compares adjacent rows only); it is assigned at the first write, not at commit, so it is not chronological (order by `create_date`, `id`, never by `transaction_id`); and the web client's integer input parser refuses values above 2^31-1, so `transaction_id` must not be offered as a typed search field or custom-filter value. Over XML-RPC, `read()` returns values above 2^31-1 as floats (Odoo core does this for every Integer), while `read_group` on the field and `export_data` would fail; no client does either.

### Querying Audit Data

**Default to `oteny.audit.log.aggregated`.** It's a SQL view that already does the unnesting, so consumers see one row per (event, field) for every change_type. The `'__snapshot__'` sentinel never surfaces, no JSON parsing is needed, and `field_name` filters work uniformly across legacy per-field rows and modern tombstone rows. Reach for the raw `oteny.audit.log` only when you specifically need the snapshot dict.

**Find all events on a record (any change type)**:
```python
# Recommended: aggregated view — one row per (event, field).
logs = env['oteny.audit.log.aggregated'].search([
    ('model_name', '=', 'crewradar.log.entry'),
    ('record_id', '=', le_id),
], order='create_date ASC')
```

**Find all changes to specific fields**:
```python
# Finds updates and deletes; an insert holds the id only (field_name 'id').
changes = env['oteny.audit.log.aggregated'].search([
    ('model_name', '=', 'crewradar.log.entry'),
    ('record_id', '=', le_id),
    ('field_name', 'in', ['state_id', 'site_id', 'active']),
])
# Updates: old_value_display_name → new_value_display_name.
# Deletes: captured value lands in old_value_display_name.
```

**Get the value of a specific field on the create event** (the reconstruction rule):
```python
first_update = env['oteny.audit.log.aggregated'].search([
    ('model_name', '=', 'crewradar.log.entry'),
    ('record_id', '=', le_id),
    ('change_type', '=', 'u'),
    ('field_name', '=', 'state_id'),
], order='create_date ASC, log_id ASC', limit=1)
state_at_create = (first_update.old_value_display_name if first_update
                   else env['crewradar.log.entry'].browse(le_id).state_id.display_name)
# For a deleted record, take the value of its delete snapshot when there is no update row.
```

**Get the full snapshot dict for a record event** — this *is* the case for the raw table:
```python
snapshot_row = env['oteny.audit.log'].search([
    ('model_name', '=', 'crewradar.log.entry'),
    ('record_id', '=', le_id),
    ('change_type', '=', 'd'),
    ('field_name', '=', '__snapshot__'),
], limit=1)
all_captured_values = snapshot_row.snapshot  # {field: {raw, display, label}, ...}
```

**Querying raw `oteny.audit.log` directly?** Three shapes:
- Snapshot rows: `field_name='__snapshot__'`, values in the `snapshot` JSON (every delete; an insert from before 19.0.1.747 whose record is gone without a delete row).
- Insert rows: `field_name='id'`, the record id in `new_value`, no field value.
- Per-field rows (updates, remarks): `field_name` is the actual field, values in `old_value`/`new_value`.

Branch on `field_name == '__snapshot__'` (NOT on `change_type`). To capture both shapes for a field-name filter, use `'|', ('field_name', 'in', [...]), ('field_name', '=', '__snapshot__')`. Or just switch to the aggregated view, which removes this complexity entirely.

**Rebuild a record's history** from the raw log, backwards from the record (or from its delete snapshot):

```python
logs = env['oteny.audit.log'].search([('model_name', '=', model), ('record_id', '=', rid)], order='id DESC')
delete = logs.filtered(lambda log: log.change_type == 'd')
record = env[model].browse(rid).exists()
state = ({f: e['raw'] for f, e in delete.snapshot.items()} if delete
         else {name: str(record[name]) for name in record._fields if record._fields[name].store})
for log in logs.filtered(lambda log: log.change_type == 'u'):
    state[log.field_name] = log.old_value   # the value before this update
# `state` is now the record at its insert; stop at an earlier update for the state at that time.
```

### Performance

- Database indexes on `oteny_audit_log`: model_name, create_date DESC, transaction_id, composite (model_name, record_id, create_date DESC, id DESC), GIN on `snapshot` (jsonb_path_ops, accelerates `?` / `@>` lookups), partial btree on `(create_date DESC) WHERE field_name = '__snapshot__'`.
- Database indexes on `oteny_audit_log_ref`: composite target index, audit_log_id, transaction-based index.
- Batch cleanup with configurable batch size and pause.
- **The view sorts by time** (19.0.1.747): `_order = "create_date DESC, log_id DESC, id DESC"`. Until then it sorted by `id`, and a child row takes its id from the ref sequence, a direct row from the log sequence; every child row sorted above every direct row, so the first page of log entry 8780 showed 75 rows that were not among the 80 newest. `log_id` repeats `audit_log_id` as a plain integer, because an order on a many2one sorts by the comodel's `_order` through a join.
- **A read by id uses an index** (19.0.1.747): Odoo reads the fields of known rows with `_search([('id', 'in', ids)])`, and the view's id is an expression, so PostgreSQL built the whole view first (1.8 s). The view's `_search` adds `audit_log_id IN (...)` to any search with an `id` condition at its top level, which pushes into each branch as an index lookup. Measured on `crmain` (backup of 18-Nov-2026): the Audit Log of log entry 8780 `web_search_read` 1,339 ms → 79 ms; the form of one row 54 ms; Audit > Log from yesterday 34 ms.
- MailThread tracking disabled to avoid double-logging overhead.

#### Aggregated view filter push-down

Since 19.0.1.723 the view is a UNION ALL of four branches: {direct rows from `oteny_audit_log`, child rows through the parent refs} × {plain rows, snapshot rows unnested by a LATERAL `jsonb_each`}. The plain branches expose the log columns as they are, so a value search pushes into the log scan; there is no window function over the union. Inside the snapshot LATERAL a `row_number()` orders the fields of one insert or delete by their label, so they read alphabetically on screen; it runs per log row and does not block the push-down. Measured on `crmain` (production backup of 27-Oct-2026) after the purge: the Audit > Log first page 20 ms, the audit log of a busy log entry (3,320 rows) 8 ms, of a busy service 1.7 ms, a value search over the whole 180 days 1.5 s (4.4 s before the purge). The history of the shape: the view was once a single SELECT with `LEFT JOIN LATERAL jsonb_each(log.snapshot) WITH ORDINALITY` (no UNION ALL, no window function). That shape let PostgreSQL push outer filters — most importantly `WHERE create_date >= ...` from the default "From Yesterday onwards" filter — down to a Bitmap Index Scan on `oteny_audit_log_create_date_idx` before unnesting. An earlier UNION ALL + `ROW_NUMBER()` shape blocked push-down (window functions are an opaque planner barrier), forcing PostgreSQL to materialize all 4 M view rows before applying the filter. Measured on production-shape `crmain`: **4.8 s → 113 ms (~43×)** for the date-filtered count query, cold cache.

#### Row id encoding

Virtual rows use `(log.id * 1000 + idx) * 2` for direct rows and `(ref.id * 1000 + idx) * 2 + 1` for child rows (19.0.1.723), where `idx` is 0 for a plain row and 1..N for the fields of a snapshot. Even ids are direct rows, odd ids child rows; the ids stay unique over the four branches and well within JavaScript's safe-integer range (2^53). An earlier `<<` 32 encoding overflowed that range for ref.id > 2 M and caused the web client to silently lose precision — the audit list view came up empty even though the SQL view returned rows.

### Key Files

| File | Description |
|------|-------------|
| `oteny_audit/models/base_patch.py` | Core ORM patches for create/write/unlink/flush |
| `oteny_audit/models/oteny_audit_log.py` | Raw audit log model, auto-setup, cleanup logic |
| `oteny_audit/models/oteny_audit_log_ref.py` | Reference model linking logs to parent records |
| `oteny_audit/models/oteny_audit_log_aggregated.py` | DB view model with HTML caption generation |
| `oteny_audit/models/oteny_audit_mixin.py` | Optional mixin adding audit_log_ids to models |
| `oteny_audit/models/base_audit_tab.py` | The Audit tab: appends the page to the form of every audited model with tabs (`_get_view` on `base`) |
| `oteny_audit/static/src/widgets/audit_log_tab/` | The view widget of the Audit tab: the newest 160 rows grouped per transaction, and the Full Screen link |
| `oteny_audit/models/mail_thread_override.py` | Disables Odoo's MailThread tracking |
| `oteny_audit/models/mail_message_override.py` | `mail.message`: HTML strip on `body`, the per-record skip for a message in an audit-ignored container, the ignored delivery fields, the audit name (`_oteny_audit_display_name`) |
| `oteny_audit/models/ir_attachment_override.py` | `ir.attachment`: the per-record skip of generated images and asset bundles, the ignored technical fields, the audit name (`_oteny_audit_display_name`) |
| `oteny_audit/models/res_users_override.py` | `res.users`: the ignored fields that Odoo writes by itself |
| `oteny_audit/models/ir_config_parameter_override.py` | `ir.config_parameter`: redact `value` when the parameter `key` names a credential |
| `oteny_audit/migrations/19.0.1.519/post-migrate.py` | One-off scrub of credentials already stored in the audit log |
| `oteny_audit/migrations/19.0.1.598/pre-migrate.py` | Converts `transaction_id` to `bigint` on the log and ref tables (the 2026-09-12 outage) |
| `oteny_audit/migrations/19.0.1.723/post-migrate.py` | Switches on the one-shot purge of the rows outside the coverage for the next night |
| `oteny_audit/migrations/19.0.1.747/post-migrate.py` | Sets the purge to phase `inserts` (insert snapshots become id rows) and switches it on for the next night |
| `oteny_audit/models/oteny_audit_fields.py` | `BigInteger`: an `int8` field for the 64-bit transaction number |
| `oteny_audit/models/registry_patch.py` | Runs audit setup on registry invalidation |
| `oteny_audit/models/ir_module_module.py` | Runs audit setup after module install |
| `oteny_audit/models/res_config_settings.py` | Cleanup configuration fields |
| `oteny_audit/views/oteny_audit_log_views.xml` | List/form/search views, actions, menus |
| `oteny_audit/views/res_config_settings_views.xml` | Settings form extension |
| `oteny_audit/data/ir_cron_data.xml` | Cleanup cron job definition |

## Roadmap

### Pending

- [ ] Some writes during import are still logged despite context flag (some method flows clear the context)
- [ ] Service travel leg recursive parent logging (leg -> service -> entry)
- [ ] Add support for translated fields
- [ ] Add support for company-dependent fields
- [ ] Per-field audit log popup (icon next to each field showing its change history)
- [ ] **Impersonate a user, with both users in the audit log** (Ries, 27-Oct-2026). A system administrator acts as another user to see what that user sees. Odoo's own login as another user (today through odoo.sh) makes the administrator that user, so the audit log names only the impersonated user. The feature keeps the original login next to the current user: the current user is the impersonated one, the original user is kept for the revert, and the audit log records both. Ries built it so before in FBO One (a primary and a secondary user). Needed at the latest when a database leaves odoo.sh.
- [ ] One-shot SQL migration to consolidate existing per-field insert/delete rows in `crmain` into snapshot rows (natural decay via retention cron is the no-op alternative)
- [ ] **Rotate the credentials that were exposed.** `19.0.1.519` redacts them going forward and scrubs the stored rows, but every internal user could read them for months, so the values must be treated as compromised. On `test1` on 2026-08-25 the audit log held cleartext values for `ai.anthropic_key`, `ai.google_key`, `iap_vies.client_token`, `oteny.broker_token`, `oteny.broker_token_live_watch` and `oteny.broker_token_replay_view` (`ai.google_key` five times over, one row per rotation), plus 2,081 portal `access_token` values, 852 `document_token`, 53 `bot_claim_token`, 30 `login_dance_token` and 11 OAuth `refresh_token`. Owner: Ries
- [ ] **Restrict who can read the audit log.** `security/ir.model.access.csv` grants `base.group_user` read on `oteny.audit.log`, `oteny.audit.log.ref` and `oteny.audit.log.aggregated`; there is no record rule and no `groups=` on the Audit menu, and the aggregated search matches on value. Redaction shrinks what a leak costs; it does not answer whether every employee should read the whole change history of every model. An audit-reader group is the fix
- [ ] Access-rights check when viewing the audit log — hide or obfuscate values as `**No Access**` for models/records the user cannot access (the same problem seen from the record side)
- [ ] `mail.message` rows targeting `riverflow.service` are now the largest single content block: 84,993 rows / **114 MB** on `test1`, still growing. They are legitimately audited, but consider whether the body needs to live in the audit copy while the message itself is immutable and still present
- [ ] `wilma.transport.review.assessment` is not in any `_oteny_audit_html_strip_fields`, so the pre-`19.0.1.519` rows hold full unstripped LLM HTML (56 MB on `test1`). The model is ignored now, so this only matters if the flag is ever reverted

### Completed

- [x] 2026-11-18: an insert writes only the record's id, and the first page of the audit log opens at once (19.0.1.747, plan `plans/audit-insert-id-only.md`; built on `feat/audit-insert-id-only`, not merged yet). The rows of a database cascade (delete and SET NULL) are written; the view sorts by time and reads by id through an index; the purge phase `inserts` turns the stored insert snapshots into id rows, and `VACUUM FULL` runs in a job of its own. Every audit row names its record as "<what> on <where>" (messages, attachments, models without a name field). Every audited form with tabs has an Audit tab: the newest 160 rows grouped per transaction, and a Full Screen link. On the production backup of 18-Nov-2026: `oteny_audit_log` 212 MB → 110 MB, the first page of a busy log entry 1,339 ms → 79 ms.
- [x] 2026-11-07: fewer audit rows, the same clarity (19.0.1.723, plan `plans/audit-storage-reduction.md`). Cuts A to I and rules N1 to N4: credential plan items, the calendar end date, generated images and asset bundles, cron triggers, the fold of changes in the transaction of the create, no direct refs, the error log and reconciliation salary timelines leave the log; fields that code writes but users cannot edit (invoice links, salary links, the invoice state, the timeline type) enter it. A selection shows its label, and the fields of an insert read in label order. The one-shot night purge makes the stored rows match: on the production backup of 27-Oct-2026 the audit tables went from 1,065 MB to 289 MB.

- [x] Legacy residue of the already-ignored models: the retention cleanup removed it. On `crmain` restored from the production backup of 27-Oct-2026, no row of a model on `_DEFAULT_IGNORED_MODEL_NAMES` remains, and the oldest row is from 29-Apr-2026 (plan `plans/audit-storage-reduction.md`).
- [x] 2026-09-12: `transaction_id` is a 64-bit column (`BigInteger`, 19.0.1.598). Production passed 2^31 transactions and every write failed with `integer out of range` until the columns were widened. The counter is the shared Odoo.sh server's, not ours; see "The transaction number is a 64-bit column" for the growth analysis (2026-09-14).
- [x] Aggregated view exposes per-field rows for everything (snapshots are unnested via `jsonb_each` LATERAL), so Group-by-Field, `field_name` filters, and the standard list-view UX work uniformly across legacy per-field rows and modern tombstone rows. GIN index on `oteny_audit_log.snapshot` (jsonb_path_ops) accelerates targeted `?` / `@>` lookups on the underlying table.
- [x] Mail.message audit re-enabled with HTML-stripped body so chatter survives cascade-unlink (the audit log is the only durable copy after the host record is gone).
- [x] Cascade-unlink parent ref preservation: child cascade-deletes still carry a parent ref to the (just-unlinked) host, with display_name recovered from the host's own delete tombstone.
- [x] Basic audit logging and views
- [x] Action menu item for all models (auto-installed)
- [x] Many2many field support
- [x] Context-based audit suppression
- [x] Parent/child reference tracking with aggregated view
- [x] HTML caption display with transaction grouping
- [x] Audit log cleanup cron with configurable settings
- [x] Database restore detection and safety
- [x] MailThread tracking disabled globally
- [x] Salary sub-model audit ignore (5 regenerated sub-tables excluded, timeline parent kept; `crewradar.salary` was the sixth and stays audited for its user-editable `remarks`)
- [x] Bot/agent activity-log audit ignore: `oteny.bot.session` + `oteny.bot.turn` set `_oteny_audit_ignore = True`, so the external bot's own log is not copied into the audit trail a second time (`test_activity_log_models_are_audit_ignored` in `oteny_bot`)
- [x] `wilma.api.cache` + `wilma.transport.review` audit ignore — both are machine-written, regenerable and vacuumed, and neither has a human-writable field (`test_wilma_audit_ignore.py`). Removes ~86,500 rows / ~65 MB of future churn per the `test1` history
- [x] Per-record audit skip (`_oteny_audit_ignore_record`): a `mail.message` posted into an audit-ignored container produces no audit rows, while chatter on a business record keeps its cascade-preservation tombstone (`test_audit_log_ignored_container.py`). Worth 15,282 rows / 57 MB of the `test1` history
- [x] Secret redaction (`19.0.1.519`): a credential value is replaced by `***redacted***` at all four emitting sites, in the flat columns and inside the `snapshot` jsonb, driven by a field-name shape + `_oteny_audit_redact_fields` + a record-aware hook for `ir.config_parameter` (`test_audit_log_redaction.py`)
- [x] One-off scrub of credentials already stored (`migrations/19.0.1.519/post-migrate.py`) — redacts, never deletes, and shares its shape with the runtime rule so the two cannot drift (`test_audit_log_secret_scrub_migration.py`)
- [x] Restore-window orphan repair (`migrations/19.0.1.519/pre-migrate.py`) — deletes rows whose cascade parent is gone before the module's `init_models` re-adds the foreign key. `pg_restore` loads data before constraints, so anything deleting parents in that window leaves orphans the constraint can then never tolerate, and every later upgrade of the database fails. Re-arms `migrations/19.0.1.329`, which is unreachable on any database past 329. Measured recovering `crmain` on 2026-08-25: 78,297 `oteny_audit_log_ref`, 64 + 1 + 1 `discuss_channel_member`, 3 `documents_access`
- [x] Default-ignored mail/discuss/cron infrastructure models (~95% storage reduction in production)
- [x] Per-field HTML stripping via `_oteny_audit_html_strip_fields` (89-95% reduction on real chatter bodies)
- [x] Tombstone snapshot rows for inserts/deletes (~5-8x row reduction; per-field updates retained for replay-based reconstruction)
- [x] Insert ordering: snapshot row precedes any subsequent updates; computed-field side effects logged as `'u'` post-snapshot rather than `'i'` (replaced by the fold of 19.0.1.723, then by the id-only insert of 19.0.1.747)
- [x] Aggregated view caption no longer hard-depends on `riverflow.service.DATETIME_FORMAT` — falls back to inline default formats when riverflow isn't installed
