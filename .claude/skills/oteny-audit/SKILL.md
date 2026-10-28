---
name: oteny-audit
description: Automatic audit logging for all Odoo models with parent/child reference tracking, aggregated views, and configurable cleanup. Covers developer integration patterns (_oteny_audit_parent_field, _oteny_audit_ignore, _oteny_audit_ignore_record, _oteny_audit_html_strip_fields, _oteny_audit_redact_fields, context flags), secret redaction so credentials never reach an audit row, tombstone snapshot rows for inserts/deletes, default-ignored mail/discuss/cron infrastructure models, and end-user UI for viewing audit trails. Use when adding audit parent references to models, suppressing audit for specific operations, browsing audit logs, configuring retention settings, or querying snapshot data for record reconstruction.
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

The module patches `BaseModel.create`, `write`, `unlink`, and `_flush` at import time. Every stored, non-readonly field change is captured automatically. No per-model opt-in is needed.

**A stored computed field is never logged.** A compute without an inverse is readonly, so it falls outside "stored, non-readonly" — `crewradar.timesheet.billable_overtime_hours`, `tft_hours`, `contract_work_hours` and the `settled_*` / `pending_*` fields leave no audit row, however far the value moves. To explain such a value, read the audit rows of the **manual inputs** it depends on (here `worked_hours_on_timesheet`, and the log entry's dates and status), and check the deploy history for a formula change. Live example, 2026-09-10: four timesheet corrections were traced to `worked_hours_on_timesheet` edits with a user and a timestamp, while nine others left no row at all and were explained by arithmetic plus the TFT commit of 2025-11-20. Two more limits bite in the same investigation: retention is 180 days (`oteny_audit.retention_days`, cleanup enabled), so on live nothing before that horizon survives; and `riverflow.check.result`, `crewradar.salary.line` and `crewradar.salary.timeline.segment.adjustment` carry no audit rows at all, so the salary side must be read from the records themselves.

### Which model to query

If you're reading audit data (UI, custom reports, AI snippets, ad-hoc analysis), pick the right model up front:

| Need | Model | Why |
|---|---|---|
| Field-level drill-down ("what changed on this record", "who set X to Y", group-by-field, filter by `field_name`) | **`oteny.audit.log.aggregated`** (preferred) | One row per (event, field). Inserts/deletes stored as tombstones are auto-unnested into per-field virtual rows. The `'__snapshot__'` sentinel never surfaces. Same shape for every change_type, every era. |
| Full snapshot dict for a single event ("show every value at delete time, all in one place") | `oteny.audit.log` (raw) | The `snapshot` JSON column lives only here. Use when you need the dict as a unit. |
| Internal: parent/child reference table | `oteny.audit.log.ref` | Direct + parent refs that link logs to target records. Joined into the aggregated view automatically; rarely queried directly. |

### Three Data Models

| Model | Type | Purpose |
|-------|------|---------|
| `oteny.audit.log` | Regular | Audit log entries: one row per field change for **updates**; one tombstone snapshot row per record event for **inserts** and **deletes** (see Tombstone Snapshots below) |
| `oteny.audit.log.ref` | Regular | Reference table linking logs to target records (direct + parent refs) |
| `oteny.audit.log.aggregated` | DB View | Joins logs and refs, **UNNESTs snapshot rows back into one virtual row per captured field** so list views, filters by `field_name`, and Group-by-Field behave the same way regardless of underlying row shape (legacy per-field, or modern tombstone). The view's `audit_log_id` column points back to the underlying log row when callers need the full snapshot. |

### Parent/Child References

When a child model declares a parent field, its audit log entries are linked to the parent record via `oteny.audit.log.ref`. This means viewing the audit log for a parent record (e.g., a log entry) also shows changes to its children (e.g., services, billing lines). Parent resolution is recursive up to 3 levels.

### Tombstone Snapshots (Inserts/Deletes)

**Why this shape exists**: a record-level event (create, delete) is one logical action — not N field-set events. Storing N audit rows per insert/delete made the per-field log structure dominate audit storage (in production sampling, inserts averaged 5.8–8.4 captured fields per record; deletes 4.8–7.3). The tombstone collapses each insert/delete to a single row whose `snapshot` JSON column holds every captured non-default field value.

**Reconstruction property**: a deleted record's state is fully reconstructable from the audit log alone, until log expiry, by replaying:

```text
insert snapshot  →  per-field updates (in chronological order)  →  delete snapshot
```

The delete snapshot is intentionally more inclusive than the create snapshot — it captures every non-blank field at delete time (even fields that equalled their default at create time, which the create snapshot omitted). This is the audit log's commitment to forensic completeness. Updates remain per-field so old/new transitions are individually queryable for replay.

**Row shape**:

| Row kind | `change_type` | `field_name` | `snapshot` |
|---|---|---|---|
| Insert tombstone | `'i'` | `'__snapshot__'` (sentinel) | `{field: {raw, display, label}, ...}` |
| Update | `'u'` | the field name | NULL |
| Delete tombstone | `'d'` | `'__snapshot__'` | `{field: {raw, display, label}, ...}` |
| Legacy per-field insert/delete (pre-deploy) | `'i'` or `'d'` | the field name | NULL |
| Remark (19.0.1.687) | `'r'` | the caller's key (for example `transition`) | NULL |

The aggregated view caption renderer detects `field_name == '__snapshot__'` and expands the snapshot dict into one indented detail line per captured field, matching the visual feel of the legacy per-field rows.

**Two shapes coexist on databases that span the deploy.** Inserts/deletes recorded *before* the tombstone deploy are still per-field rows (one row per captured field, like an update — `field_name` is the actual field, `snapshot` is NULL). Inserts/deletes *after* the deploy are tombstone snapshot rows. A drill-down query that returns audit history for a long-lived record will return a mix of both shapes. **Use `field_name == '__snapshot__'` (not `change_type`) as the discriminator** when reading rows — branching on `change_type` will misclassify legacy `'i'`/`'d'` rows as updates.

### Remarks (change type `r`)

The audit log records changed values. It cannot say which action made a change: two buttons that write the same values look the same, and an action that changes nothing leaves no row. A **remark** is a line of text that says what a person or a process did (19.0.1.687; plan `riverflow/plans/transition-in-audit-log.md`, from live service 33239, where only an elimination showed that Kirsten pressed Received already and not Upload Ship A1):

```python
self.env["oteny.audit.log"]._log_remark(records, "transition", _("Transition"), transition.name)
```

- **One row per record:** `change_type = 'r'`, `field_name` = the caller's fixed key (the same in every language, so a filter on it keeps working), `field_display_name` = the label, `new_value` and `new_value_display_name` = the text. `old_value` and `snapshot` are empty.
- **Written like a field change.** It goes through `_create_audit_logs`, so it has the user, the time, the transaction and the parent references of a field change, and the same skip rules (`oteny_audit_ignore`, an ignored model or record, registry not loaded, restore detected). A skip writes nothing.
- **On screen.** The caption gives a remark its own header ("Remark … by … on …") and the line "label: **text**", always escaped as plain text, also when the key is the name of an HTML field. The search views have the filter Remarks.
- **Not over RPC.** The leading underscore keeps the function out of the web API, so a user cannot write a false remark.
- **No field value.** The rebuild of a deleted record (insert snapshot, then updates, then delete snapshot) skips `r` rows.
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

## Configuration

### The cleanup cron and database restores

`Audit Log: Cleanup Old Records` is a batch delete over the largest table in the database, and it is the reason a restore must not be run with a server still up. `pg_restore` re-adds constraints last, so a server that reconnects mid-load and fires this cron deletes `oteny_audit_log` rows while `oteny_audit_log_ref_audit_log_id_fkey` does not exist yet. The refs are orphaned, the foreign key can never be re-added, and the database cannot be upgraded again.

`riverdeploy/crewradar_db_restore.py::stop_odoo_servers` now stops such a server before the load, and `migrations/19.0.1.519/pre-migrate.py` repairs a database that already fell in. Full account, with the lock-diagnosis query, in the restore reference under *A running `crmain` server wrecks the restore*.

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

The parent `crewradar.salary.timeline` keeps audit enabled for tracking creation and state changes.

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

**Ignored by default** (no configuration needed):
- Transient models (wizards)
- Infrastructure models on `_DEFAULT_IGNORED_MODEL_NAMES` (mail/discuss/cron — see Default-Ignored Infrastructure Models above)
- System model prefixes: `oteny.audit`, `ir.ui.view`, `ir.model.data`, `bus.`, `mail.push`, `mail.tracking`, `res.device.log`

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

1. **Create**: `patched_create` runs after `original_create`, reads all stored non-readonly columns from the DB, compares to defaults, then folds every non-default value into a single **tombstone snapshot row** with `change_type='i'`, `field_name='__snapshot__'`, and `snapshot={...}`. One row per created record regardless of how many fields were captured.
2. **Write (relational)**: `patched_write` captures old values before write, then reads new values after write, logs immediately for m2m/o2m fields (per-field `change_type='u'` rows).
3. **Write (scalar)**: `patched_write` captures old values from cache, then `patched_flush` compares old vs new after the ORM flush, logging the difference (per-field `change_type='u'` rows). Late-arriving fields on newly-created records (e.g. computed-field side effects whose recompute happens after the create-time snapshot read) are also recorded as `'u'` rows — they're conceptually post-create updates, since the tombstone snapshot already covered the create event atomically.
4. **Unlink**: `patched_unlink` reads all stored field values before deletion, then folds every non-default value into a single **tombstone snapshot row** with `change_type='d'`, `field_name='__snapshot__'`, and `snapshot={...}`. One row per deleted record. The delete snapshot is intentionally inclusive of fields that were defaults at create time (omitted from the create snapshot), so the delete tombstone preserves the full reconstructable state.
5. **References**: After creating log records, `_create_audit_log_references` creates both direct refs (log points to itself) and parent refs (log points to parent chain, up to 3 levels). Snapshot rows get the same direct + parent ref treatment as per-field rows.

These four are also the four **emitting sites** — the only places a field value becomes an audit row. Each one runs `_record_audit_ignored` on the record (the per-record skip) and `_maybe_redact` on every raw and display value (the credential redaction). A change to either rule must touch all four, or it leaks through the one it missed; that is why the write-relational branch carries a redaction call it cannot currently need.

### Transaction-Scoped Deduplication

The module uses `env.cr.precommit.data["oteny_audit"]` to store transaction-scoped state:
- `old_values`: Pre-write field values for scalar fields
- `logged_changes`: Set of (field, record_id) already logged in this transaction
- `newly_created_records`: Records created in this transaction (used to skip default-vs-changed comparisons for fields that arrive via flush after a create)
- `initially_logged_fields`: Fields covered by the create-time snapshot (informational; no longer drives change_type since `patched_flush` always emits `'u'` after the snapshot was committed)
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
# Works for inserts, updates, and deletes — uniform shape.
changes = env['oteny.audit.log.aggregated'].search([
    ('model_name', '=', 'crewradar.log.entry'),
    ('record_id', '=', le_id),
    ('field_name', 'in', ['state_id', 'site_id', 'active']),
])
# Inserts: captured value lands in new_value_display_name.
# Updates: old_value_display_name → new_value_display_name.
# Deletes: captured value lands in old_value_display_name.
```

**Get the value of a specific field on the create event**:
```python
state_at_create = env['oteny.audit.log.aggregated'].search([
    ('model_name', '=', 'crewradar.log.entry'),
    ('record_id', '=', le_id),
    ('change_type', '=', 'i'),
    ('field_name', '=', 'state_id'),
], limit=1).new_value_display_name
```

**Get the full snapshot dict for a record event** — this *is* the case for the raw table:
```python
snapshot_row = env['oteny.audit.log'].search([
    ('model_name', '=', 'crewradar.log.entry'),
    ('record_id', '=', le_id),
    ('change_type', '=', 'i'),
    ('field_name', '=', '__snapshot__'),
], limit=1)
all_captured_values = snapshot_row.snapshot  # {field: {raw, display, label}, ...}
```

**Querying raw `oteny.audit.log` directly?** Two shapes coexist on databases that span the deploy:
- Modern tombstone rows: `field_name='__snapshot__'`, values in the `snapshot` JSON.
- Legacy per-field rows (pre-deploy inserts/deletes + all updates): `field_name` is the actual field, values in `old_value`/`new_value`.

Branch on `field_name == '__snapshot__'` (NOT on `change_type` — legacy `'i'`/`'d'` rows look like updates and would be misclassified). To capture both shapes for a field-name filter, use `'|', ('field_name', 'in', [...]), ('field_name', '=', '__snapshot__')`. Or just switch to the aggregated view, which removes this complexity entirely.

**Replay a record's full history** from the raw log (works even after the record is deleted, and on databases with both legacy per-field and modern tombstone rows):

```python
state = {}
for log in env['oteny.audit.log'].search([
    ('model_name', '=', model),
    ('record_id', '=', rid),
], order='id ASC'):
    if log.field_name == '__snapshot__':
        # Modern tombstone (post-deploy insert/delete)
        if log.change_type == 'i':
            state = {f: e['raw'] for f, e in (log.snapshot or {}).items()}
        elif log.change_type == 'd':
            state = None  # record deleted
    else:
        # Per-field row: either an update OR a legacy pre-deploy insert/delete.
        # Both are replayed as a single-field write; legacy 'i' rows seed state,
        # 'u' rows transition, legacy 'd' rows just unset that field.
        if log.change_type == 'd':
            state = None if state is None else state  # legacy deletes lose per-field detail; treat as final tombstone signal
            # If there are no further rows, the record is gone.
        else:
            state = state if state is not None else {}
            state[log.field_name] = log.new_value
```

### Performance

- Database indexes on `oteny_audit_log`: model_name, create_date DESC, transaction_id, composite (model_name, record_id, create_date DESC, id DESC), GIN on `snapshot` (jsonb_path_ops, accelerates `?` / `@>` lookups), partial btree on `(create_date DESC) WHERE field_name = '__snapshot__'`.
- Database indexes on `oteny_audit_log_ref`: composite target index, audit_log_id, transaction-based index.
- Batch cleanup with configurable batch size and pause.
- MailThread tracking disabled to avoid double-logging overhead.

#### Aggregated view filter push-down

The `oteny.audit.log.aggregated` view is structured as a single SELECT with `LEFT JOIN LATERAL jsonb_each(log.snapshot) WITH ORDINALITY` (no UNION ALL, no window function). This shape lets PostgreSQL push outer filters — most importantly `WHERE create_date >= ...` from the default "From Yesterday onwards" filter — down to a Bitmap Index Scan on `oteny_audit_log_create_date_idx` before unnesting. An earlier UNION ALL + `ROW_NUMBER()` shape blocked push-down (window functions are an opaque planner barrier), forcing PostgreSQL to materialize all 4 M view rows before applying the filter. Measured on production-shape `crmain`: **4.8 s → 113 ms (~43×)** for the date-filtered count query, cold cache.

#### Row id encoding

Virtual rows in the aggregated view use `ref.id::bigint * 1000 + COALESCE(jek.idx, 0)` where `jek.idx` is the LATERAL `WITH ORDINALITY` counter (0 for non-snapshot rows, 1..N for snapshot-derived rows). This stays well within JavaScript's safe-integer range (2^53). An earlier `<<` 32 encoding overflowed that range for ref.id > 2 M and caused the web client to silently lose precision — the audit list view came up empty even though the SQL view returned rows.

### Key Files

| File | Description |
|------|-------------|
| `oteny_audit/models/base_patch.py` | Core ORM patches for create/write/unlink/flush |
| `oteny_audit/models/oteny_audit_log.py` | Raw audit log model, auto-setup, cleanup logic |
| `oteny_audit/models/oteny_audit_log_ref.py` | Reference model linking logs to parent records |
| `oteny_audit/models/oteny_audit_log_aggregated.py` | DB view model with HTML caption generation |
| `oteny_audit/models/oteny_audit_mixin.py` | Optional mixin adding audit_log_ids to models |
| `oteny_audit/models/mail_thread_override.py` | Disables Odoo's MailThread tracking |
| `oteny_audit/models/mail_message_override.py` | `mail.message`: HTML strip on `body`, and the per-record skip for a message in an audit-ignored container |
| `oteny_audit/models/ir_config_parameter_override.py` | `ir.config_parameter`: redact `value` when the parameter `key` names a credential |
| `oteny_audit/migrations/19.0.1.519/post-migrate.py` | One-off scrub of credentials already stored in the audit log |
| `oteny_audit/migrations/19.0.1.598/pre-migrate.py` | Converts `transaction_id` to `bigint` on the log and ref tables (the 2026-09-12 outage) |
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
- [ ] Purge the legacy residue of the already-ignored models. Every model on `_DEFAULT_IGNORED_MODEL_NAMES` stopped writing on 2026-04-28, but the rows written before that date remain: **851,370 rows / 515 MB** on `test1`, one third of the whole table. A one-off delete removes it; the retention cron never will, because these rows predate the window and are re-aged by nothing
- [ ] `mail.message` rows targeting `riverflow.service` are now the largest single content block: 84,993 rows / **114 MB** on `test1`, still growing. They are legitimately audited, but consider whether the body needs to live in the audit copy while the message itself is immutable and still present
- [ ] `wilma.transport.review.assessment` is not in any `_oteny_audit_html_strip_fields`, so the pre-`19.0.1.519` rows hold full unstripped LLM HTML (56 MB on `test1`). The model is ignored now, so this only matters if the flag is ever reverted

### Completed

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
- [x] Insert ordering: snapshot row precedes any subsequent updates; computed-field side effects logged as `'u'` post-snapshot rather than `'i'`
- [x] Aggregated view caption no longer hard-depends on `riverflow.service.DATETIME_FORMAT` — falls back to inline default formats when riverflow isn't installed
