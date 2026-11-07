# audit_log/models/base_patch.py
from collections import defaultdict
import html as _html_module
import logging
import re

from odoo import models, api
from odoo.tools import SQL

_logger = logging.getLogger(__name__)

# Pre-compiled regexes used by _strip_html_for_audit. Compiling once at import
# time avoids per-row recompilation in the hot create/write/unlink paths.
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_HTML_WHITESPACE_RE = re.compile(r"\s+")


def _strip_html_for_audit(value):
    """Reduce an HTML string to a compact plain-text form for audit storage.

    Used for fields listed in `_oteny_audit_html_strip_fields` on a model
    class. Strips tags, decodes HTML entities (&lt;, &nbsp;, &amp; ...) so the
    stripped text reads naturally, then collapses runs of whitespace. Typical
    reduction observed on real chatter bodies: ~90-95%, while the prose
    remains fully readable.

    Non-string inputs are returned unchanged.
    """
    if not value or not isinstance(value, str):
        return value
    no_tags = _HTML_TAG_RE.sub(" ", value)
    decoded = _html_module.unescape(no_tags)
    return _HTML_WHITESPACE_RE.sub(" ", decoded).strip()


def _maybe_strip_html(model, field_name, value):
    """Apply HTML stripping to value if the model marks the field as strippable."""
    strip_fields = getattr(model, "_oteny_audit_html_strip_fields", None)
    if not strip_fields or field_name not in strip_fields:
        return value
    return _strip_html_for_audit(value)


# --- secret redaction ---------------------------------------------------------------
#
# The audit log is readable by every internal user (security/ir.model.access.csv grants
# `base.group_user` read on oteny.audit.log + the aggregated view, and the list view can
# be searched by value). So a credential that reaches an audit row is exposed to the whole
# staff for the whole retention window. The raw SQL read in patched_create/patched_unlink
# also bypasses Odoo's field-level `groups=`, which is what protects `ir.mail_server.smtp_pass`
# and `iap.account.account_token` everywhere else. Redaction is therefore fail-closed: the
# audit log keeps the FACT of the change (who, when, which field) and drops only the value.
AUDIT_REDACTED = "***redacted***"

# Field names that always hold a credential, whatever model carries them. The match runs on
# the field NAME; the field TYPE gate below then drops anything a credential cannot be.
#
# EXACT names. `smtp_pass` and `pin` have no marker word, and `credential` is exact rather
# than a marker because rivercreds names a whole family `credential_*` after work-permit
# DOCUMENTS (`credential_type_id`, `credential_plan_item_id`) — none of which is a secret.
_SECRET_FIELD_NAMES = frozenset({
    "password", "passwd", "secret", "token", "api_key", "apikey",
    "access_token", "refresh_token", "private_key",
    "smtp_pass", "pin", "credential",
})

# Marker words, matched ANYWHERE in the name. A suffix rule alone missed real credentials:
# `stripe_secret_key`, `google_calendar_rtoken`, `firebase_push_certificate_key`.
_SECRET_NAME_MARKERS = ("password", "passwd", "secret", "token", "apikey")

# Suffixes. `_key` is the largest credential family in core + enterprise — `openai_key`,
# `avalara_api_key`, `sendcloud_secret_key`, `wise_api_key`, `partner_key`, `app_key`.
_SECRET_FIELD_SUFFIXES = ("_key", "_credential")

# The few `*_key` fields that are lookup or correlation keys rather than credentials. Only
# names measured in THIS database go here: `wilma.api.cache.cache_key` and
# `rivercreds.document.import.job.gemini_request_key` (which is literally `job-<id>`).
# Bare `key` is not in any set above, so `ir.config_parameter.key` — the parameter NAME an
# auditor must read — and `sign.template.dto.override.key` are never touched.
_NOT_SECRET_FIELD_NAMES = frozenset({"cache_key", "gemini_request_key"})

# A credential is never a number, a flag, a date or a relation. Everything else can hold one,
# so this is a DENY list. An ALLOW list of {char, text, html} silently let a Binary private
# key through: `ir.mail_server.smtp_ssl_private_key` is `Binary(attachment=False)`, a real
# `bytea` column, and it wrote a full PEM into the audit log on every mail-server edit.
_NON_SECRET_FIELD_TYPES = frozenset({
    "integer", "float", "monetary", "boolean", "date", "datetime",
    "many2one", "one2many", "many2many", "many2one_reference",
})


def _is_secret_field_name(name):
    """True when a field name alone marks the field as holding a credential.

    Name-only, so the data scrub in `migrations/19.0.1.519/post-migrate.py` can reuse the
    exact same shape against stored rows, where no field object survives. The runtime adds
    a field-TYPE gate on top of this; the scrub cannot, but a stored value is a string by
    then anyway.
    """
    if name in _NOT_SECRET_FIELD_NAMES:
        return False
    if name in _SECRET_FIELD_NAMES or name.endswith(_SECRET_FIELD_SUFFIXES):
        return True
    return any(marker in name for marker in _SECRET_NAME_MARKERS)


def _is_secret_field(model, field, record=None):
    """True when `field` on `model` holds a credential that must not be logged.

    Three levers, checked in order:
      1. `_oteny_audit_redact_fields` on the model class — an explicit opt-in that mirrors
         `_oteny_audit_html_strip_fields`, and the only one that can force a numeric or
         relational field. Nothing in production declares it today; it is the escape hatch
         for a credential the name shape cannot see.
      2. The name shape above — the fail-closed default that covers every credential-shaped
         field in the database, present and future.
      3. `_oteny_audit_redact_record_field(field_name, record)` on the model class — for the
         case where the answer depends on the RECORD, not the field. Only `ir.config_parameter`
         needs it today.
    """
    name = field.name
    declared = getattr(model, "_oteny_audit_redact_fields", None)
    if declared and name in declared:
        return True
    if field.type in _NON_SECRET_FIELD_TYPES:
        return False
    if _is_secret_field_name(name):
        return True
    hook = getattr(model, "_oteny_audit_redact_record_field", None)
    if hook is None or record is None:
        return False
    try:
        return bool(hook(name, record))
    except Exception:
        # An audit hook must never break the write it is observing. Fail OPEN to auditing:
        # a value we could not classify is kept, which is the module's normal behaviour.
        return False


def _maybe_redact(model, field, value, record=None):
    """Replace a credential value with a placeholder before it reaches the audit log."""
    if not value:
        return value
    if _is_secret_field(model, field, record):
        return AUDIT_REDACTED
    return value


# Fields that Odoo itself writes on every record: the log row already holds who and when.
_MAGIC_FIELD_NAMES = frozenset({"id", "create_uid", "create_date", "write_uid", "write_date"})


def _ignored_field_names(model):
    """The union of `_oteny_audit_ignore_fields` over every class of the model.

    A class attribute of an Odoo model is overridden, not merged, by a later class in the
    inheritance chain: a mixin's set would disappear as soon as the model declares its own.
    So the sets of all classes in the MRO are joined, and each module declares only the
    fields it owns (riverflow's tombstone mixin `to_be_deleted`, a model's own compute, ...).
    """
    cls = type(model)
    cached = cls.__dict__.get("_oteny_audit_ignore_fields_all")
    if cached is not None:
        return cached
    names = frozenset().union(
        *(klass.__dict__["_oteny_audit_ignore_fields"]
          for klass in cls.__mro__ if "_oteny_audit_ignore_fields" in klass.__dict__)
    )
    cls._oteny_audit_ignore_fields_all = names
    return names


def _is_audited_field(model, field):
    """True when a change of `field` on `model` belongs in the audit log.

    - A stored field only.
    - A computed or related field that is read-only stays out: the log explains its value
      through the inputs it depends on. A computed field with an inverse (or readonly=False)
      is logged, so a user's override is visible.
    - A read-only field WITHOUT a compute is logged: in Odoo `readonly=True` means
      read-only on screen, not "never written", and code writes such fields (an invoice
      link, the state of an invoice, the type of a salary timeline).
    - A read-only `active` is not logged: only code that retires the record right before
      its delete writes it (riverflow.tombstone.mixin); a model with a user archive
      declares `active` writable, and that one is logged.
    - `parent_path` is not logged: Odoo builds it from `parent_id`, which is logged.
    - The Odoo access columns and the names in `_oteny_audit_ignore_fields` stay out.
    """
    if not field.store:
        return False
    name = field.name
    if name in _MAGIC_FIELD_NAMES:
        return False
    if field.readonly and (field.compute or field.related):
        return False
    if name == "active" and field.readonly:
        return False
    if name == "parent_path" and model._parent_store:
        return False
    return name not in _ignored_field_names(model)


def _is_empty_without_default(field, value):
    """True for an empty value (False, 0, empty x2many) of a field that has no default.

    For such a field, "absent from the insert snapshot" already means "empty", and Odoo
    reads a NULL number or boolean as 0 or False, so the rebuild of the record gives the
    same values. An empty value of a field WITH a default is information (the default was
    overridden), so it stays.
    """
    return not field.default and not value


def _record_audit_ignored(model, record):
    """Per-RECORD skip, for a model that is audited in general.

    `oteny.audit.log._is_audit_ignored` answers per MODEL, which cannot express the one case
    that needs finer grain: a `mail.message` is audited so chatter outlives the cascade-unlink
    of its business record, but a message posted into a container that is itself audit-ignored
    (a `discuss.channel`) has no such record to outlive. See mail_message_override.py.

    Fails OPEN — if the hook raises, the record is audited as usual.
    """
    hook = getattr(model, "_oteny_audit_ignore_record", None)
    if hook is None:
        return False
    try:
        return bool(hook(record))
    except Exception:
        return False


original_create = models.BaseModel.create
original_unlink = models.BaseModel.unlink
original_write = models.BaseModel.write
original_flush = models.BaseModel._flush

# Global flag to permanently disable auditing when database restore is detected
# This is set when we detect changes to database.uuid (which happens during restore/copy)
# and remains set for the lifetime of the process (which terminates after CLI restore anyway)
_auditing_disabled_due_to_db_restore = False

# Key field that indicates a database restore is in progress
_DB_RESTORE_INDICATOR_KEY = "database.uuid"


def _check_and_disable_on_restore(logs):
    """
    Check if any logs indicate a database restore operation and permanently disable auditing.

    During database restore and copy operations, Odoo regenerates database.uuid.
    When we detect this change, we permanently disable auditing for the remainder of
    the process lifetime. This is safe because:
    1. Restore/copy operations are CLI-driven and Odoo terminates after completion
    2. Attempting to audit during restore will fail due to schema mismatches
    3. We don't want to log the restore operation itself anyway

    Args:
        logs: List of log dictionaries to be created

    Returns:
        True if auditing should be disabled (restore detected), False otherwise
    """
    global _auditing_disabled_due_to_db_restore

    # If already disabled, return immediately
    if _auditing_disabled_due_to_db_restore:
        return True

    # Check if any log indicates database.uuid is being changed
    for log in logs:
        if (
            log.get("model_name") == "ir.config_parameter"
            and log.get("record_display_name") == _DB_RESTORE_INDICATOR_KEY
        ):
            _auditing_disabled_due_to_db_restore = True
            _logger.info(
                "Database restore detected (database.uuid change) - permanently disabling audit logging for this process"
            )
            return True

    return False


def _should_skip_audit_logging(env):
    """
    Determine if audit logging should be skipped based on the current environment state.

    Audit logging is skipped in the following scenarios:
    1. Database restore has been detected (global flag set)
    2. Context explicitly disables auditing (oteny_audit_ignore=True)
    3. Registry is not ready (during module installation or upgrade)

    Args:
        env: The Odoo environment

    Returns:
        True if audit logging should be skipped, False otherwise
    """
    # Check if auditing was permanently disabled due to database restore
    if _auditing_disabled_due_to_db_restore:
        return True

    # Check if auditing is explicitly disabled via context
    if env.context.get("oteny_audit_ignore", False):
        return True

    # Check if registry is not ready (during module installation, upgrade)
    # When registry.ready is False, the system is in an initialization phase
    if not env.registry.ready:
        return True

    return False


def _create_audit_log_references(env, model, log_records):
    """
    Create the parent references of audit logs: a child log points to each of its parents,
    so it shows under the parent's audit trail.

    A log row needs no reference to its own record: the aggregated view reads the direct
    rows from `oteny_audit_log` itself. Until 19.0.1.712 every log row also got a "direct"
    reference that repeated its model, record, name, date and transaction; those were two
    thirds of the reference table (plan oteny-audit/plans/audit-storage-reduction.md, cut F).

    Args:
        env: The Odoo environment.
        model: The model class being audited.
        log_records: A recordset of `oteny.audit.log`.
    """
    if not log_records:
        return

    refs = []

    # Parent references (for child logs that have parent relationships)
    # Group log records by their source record id to minimize queries
    logs_by_record_id = defaultdict(list)
    for log in log_records:
        logs_by_record_id[log.record_id].append(log)

    if logs_by_record_id:
        # Browse all source records at once
        records_with_logs = env[model._name].sudo().browse(list(logs_by_record_id.keys()))

        for record in records_with_logs:
            # Check if the record still exists before trying to access its fields
            if not record.exists():
                continue

            try:
                # Use unified parent resolution with recursion support
                # The function will determine the appropriate config for each record individually
                # and return all parent references found through recursion (ordered: immediate -> top-most)
                all_parent_refs = _resolve_parent_reference_unified(env, record, max_depth=3)

                # Create parent reference records for each level of recursion
                # List order: [immediate_parent, grandparent, great-grandparent, ...]
                if all_parent_refs:
                    for parent_ref_data in all_parent_refs:
                        parent_model_name = parent_ref_data["parent_model_name"]
                        parent_record_id = parent_ref_data["parent_record_id"]
                        parent_display_name = parent_ref_data["parent_display_name"]

                        # Create parent reference for each audit log of this record
                        for log in logs_by_record_id[record.id]:
                            refs.append(
                                {
                                    "audit_log_id": log.id,
                                    "target_model_name": parent_model_name,
                                    "target_record_id": parent_record_id,
                                    "target_display_name": log.record_display_name,  # Use child's display name for child logs
                                    "parent_display_name": parent_display_name,  # Store parent display name separately
                                    "is_direct": False,
                                    "create_date": log.create_date,
                                    "transaction_id": log.transaction_id,
                                }
                            )
            except Exception:
                # Failsafe for cases where parent record might be deleted or access rights issues.
                continue

    # Bulk create all references
    if refs:
        env["oteny.audit.log.ref"].sudo().create(refs)


def _recover_unlinked_display_name(env, model_name, record_id):
    """Look up a historical display_name for a record that was unlinked earlier.

    When a parent is unlinked in the same transaction as one of its children
    (typical cascade-unlink scenario), the parent record no longer exists in
    the DB by the time the child's audit row is being written. We still want
    the parent ref to be created so the child's chatter trail remains visible
    when filtering audit by the parent — and we want a sensible display_name
    on the ref. Recover it from the most recent oteny.audit.log row for the
    same (model, record_id), which carries `record_display_name` captured
    while the parent still existed.

    Returns None if no matching audit row exists (parent never had any audit
    log; falls back to a generic "ID: N" placeholder at the call site).
    """
    log = env["oteny.audit.log"].sudo().search(
        [("model_name", "=", model_name), ("record_id", "=", record_id)],
        order="id DESC",
        limit=1,
    )
    return log.record_display_name if log else None


def _resolve_parent_reference_unified(env, record, max_depth=3, current_depth=0):
    """
    Unified function to recursively resolve parent references up to max_depth levels.
    Handles both single field references and tuple formats for both _oteny_audit_parent_field and _model_parent_keys.

    Determines the appropriate parent configuration for each record individually:
    1. First checks for model-specific _oteny_audit_parent_field
    2. Falls back to predefined _model_parent_keys if no model-specific field exists

    Cascade-unlink robustness: when the parent record was unlinked earlier in
    the same transaction (typical for chatter / mail.message tied to an
    unlinked business record), the parent ref is still created, with the
    parent's historical display_name recovered from the most recent audit log
    row for that (model, record_id). Without this, child cascade-unlink
    tombstones would be orphan refs invisible to the parent's audit filter.

    Args:
        env: The Odoo environment.
        record: The record to find parent for.
        max_depth: Maximum recursion depth (default 3).
        current_depth: Current recursion depth.

    Returns:
        list: List of parent reference data dictionaries, each containing:
            - parent_model_name: The model name of the parent record
            - parent_record_id: The ID of the parent record
            - parent_display_name: The display name of the parent record
    """
    parent_refs = []

    if current_depth >= max_depth:
        return []

    # Determine parent configuration for this specific record
    parent_config = None

    # Check for model-specific _oteny_audit_parent_field first (takes precedence)
    model_parent_field = getattr(record.__class__, "_oteny_audit_parent_field", None)
    if model_parent_field:
        parent_config = model_parent_field
    else:
        # Fall back to predefined parent keys
        parent_keys = getattr(env["oteny.audit.log"], "_model_parent_keys", {})
        model_parent_key = parent_keys.get(record._name)
        if model_parent_key:
            parent_config = model_parent_key

    if not parent_config:
        return parent_refs

    parent_record = None
    parent_model_name = None
    parent_record_id = None

    try:
        # Parse the parent configuration - handle both single field and tuple formats
        if "," in parent_config:
            # Tuple format: "model,res_id" or similar
            config_fields = [field.strip() for field in parent_config.split(",")]
            if len(config_fields) == 2:
                model_field, id_field = config_fields
                if model_field in record._fields and id_field in record._fields:
                    parent_model_name = record[model_field]
                    parent_record_id = record[id_field]

                    if parent_model_name and parent_record_id and parent_model_name in env:
                        # Browse without `.exists()` so that a parent unlinked
                        # earlier in the same transaction still yields a ref.
                        parent_record = env[parent_model_name].sudo().browse(parent_record_id)
        else:
            # Single field format: "parent_id"
            parent_field = parent_config
            if parent_field in record._fields:
                parent_record = record[parent_field]
                if parent_record:
                    parent_model_name = parent_record._name
                    parent_record_id = parent_record.id

        if parent_record and parent_model_name and parent_record_id:
            # Read display_name; if the parent was unlinked in this transaction,
            # recover the historical display_name from the audit log itself.
            parent_existed = parent_record.exists()
            if parent_existed and hasattr(parent_record, "display_name"):
                parent_display_name = parent_record.display_name
            else:
                parent_display_name = (
                    _recover_unlinked_display_name(env, parent_model_name, parent_record_id)
                    or f"ID: {parent_record_id}"
                )

            parent_refs.append(
                {
                    "parent_model_name": parent_model_name,
                    "parent_record_id": parent_record_id,
                    "parent_display_name": parent_display_name,
                }
            )

            # Recurse only when the parent still exists. If the parent has
            # already been unlinked, recursion would either fail or repeat
            # the same recovery; either way the grandparent ref isn't worth
            # the lookup cost in cascade-unlink scenarios.
            if parent_existed and current_depth < max_depth - 1:
                child_parent_refs = _resolve_parent_reference_unified(
                    env, parent_record, max_depth, current_depth + 1
                )
                parent_refs.extend(child_parent_refs)

        return parent_refs

    except Exception:
        # Failsafe for cases where parent record might be deleted or access rights issues.
        return parent_refs


def _create_audit_logs(env, model, logs):
    """
    Helper to create audit log records and their references.

    Checks for database restore indicators and permanently disables auditing if detected.
    Also skips logging when the registry is not ready or when explicitly disabled via context.
    This prevents schema mismatch errors when the audit schema doesn't match code expectations.
    """
    if not logs or not env.registry.loaded:
        return

    # Check if this batch of logs contains database restore indicators
    # This will set the global flag if restore is detected
    if _check_and_disable_on_restore(logs):
        _logger.debug(
            "Skipping audit logging for %d changes - database restore detected or auditing disabled",
            len(logs),
        )
        return

    # Skip logging if we're in a state where auditing should be disabled
    if _should_skip_audit_logging(env):
        _logger.debug(
            "Skipping audit logging for %d changes - registry not ready or auditing disabled via context",
            len(logs),
        )
        return

    env.cr.execute("SELECT txid_current()")
    txid = env.cr.fetchone()[0]
    for log in logs:
        log["transaction_id"] = txid

    log_records = env["oteny.audit.log"].sudo().create(logs)
    _create_audit_log_references(env, model, log_records)
    return log_records


def _parent_config_fields(env, model):
    """The field names that decide the parent of a record of `model` (empty when none)."""
    config = getattr(type(model), "_oteny_audit_parent_field", None)
    if not config:
        config = getattr(env["oteny.audit.log"], "_model_parent_keys", {}).get(model._name)
    if not config:
        return set()
    return {name.strip() for name in config.split(",")}


def _live_insert_log_ids(env, model, record_ids):
    """{record id: insert log id} for the records of `model` created in this transaction
    whose insert row still exists. An insert row that is gone (deleted in this transaction,
    or rolled back to a savepoint) is forgotten, so a later change of that record is logged
    as an update row, as before."""
    insert_log_ids = env.cr.precommit.data.get("oteny_audit", {}).get("insert_log_ids", {}).get(model._name)
    if not insert_log_ids:
        return {}
    wanted = {insert_log_ids[rid] for rid in record_ids if rid in insert_log_ids}
    if not wanted:
        return {}
    alive = set(env["oteny.audit.log"].sudo().browse(list(wanted)).exists().ids)
    for rid in [rid for rid in record_ids if insert_log_ids.get(rid) in wanted - alive]:
        del insert_log_ids[rid]
    return {rid: insert_log_ids[rid] for rid in record_ids if rid in insert_log_ids}


def _fold_into_insert_snapshots(env, model, entries):
    """Write field changes of records created in THIS transaction into their insert snapshot.

    `entries` is a list of (record, field, new_cache_value). A value that changes inside the
    transaction of the create was never visible to another user or process: the insert
    snapshot with the value at the end of the transaction says everything anybody could see.
    Stored computes and code that writes right after the create (the salary timeline builder,
    the computes of a new service) used to give one to seven update rows per new record
    (plan oteny-audit/plans/audit-storage-reduction.md, cut E).

    Returns the entries that could not be folded (no insert row of this transaction), which
    the caller logs as update rows as before.
    """
    insert_log_ids = _live_insert_log_ids(env, model, [record.id for record, _field, _value in entries])
    alive = set(insert_log_ids.values())
    rest = []
    patches = defaultdict(dict)  # insert log id -> {field name: entry or None (drop the key)}
    records_by_log = {}
    for record, field, value in entries:
        log_id = insert_log_ids.get(record.id)
        if log_id not in alive:
            rest.append((record, field, value))
            continue
        records_by_log[log_id] = record
        if value is None or value == "" or (
            field.default
            and field.convert_to_cache(field.default(record), record) == value
        ) or _is_empty_without_default(field, value):
            patches[log_id][field.name] = None
            continue
        raw = str(value)
        display = _get_display_value(field, value, record.env)
        raw = _maybe_redact(model, field, _maybe_strip_html(model, field.name, raw), record)
        display = _maybe_redact(model, field, _maybe_strip_html(model, field.name, display), record)
        patches[log_id][field.name] = {"raw": raw, "display": display or "", "label": field.string or field.name}
    if not patches:
        return rest
    parent_fields = _parent_config_fields(env, model)
    for log in env["oteny.audit.log"].sudo().browse(list(patches)):
        snapshot = dict(log.snapshot or {})
        for name, entry in patches[log.id].items():
            if entry is None:
                snapshot.pop(name, None)
            else:
                snapshot[name] = entry
        log.snapshot = snapshot
        if parent_fields & set(patches[log.id]):
            _add_missing_parent_refs(env, log, records_by_log[log.id])
    return rest


def _add_missing_parent_refs(env, log, record):
    """Give `log` the parent references of `record` that it does not have yet (its parent
    field was set after the create, in the same transaction)."""
    if not record.exists():
        return
    Ref = env["oteny.audit.log.ref"].sudo()
    existing = {(ref.target_model_name, ref.target_record_id)
                for ref in Ref.search([("audit_log_id", "=", log.id), ("is_direct", "=", False)])}
    refs = []
    for parent in _resolve_parent_reference_unified(env, record, max_depth=3):
        key = (parent["parent_model_name"], parent["parent_record_id"])
        if key in existing:
            continue
        existing.add(key)
        refs.append({
            "audit_log_id": log.id,
            "target_model_name": key[0],
            "target_record_id": key[1],
            "target_display_name": log.record_display_name,
            "parent_display_name": parent["parent_display_name"],
            "is_direct": False,
            "create_date": log.create_date,
            "transaction_id": log.transaction_id,
        })
    if refs:
        Ref.create(refs)


def _safe_convert_to_cache(field, raw_value, record):
    """
    Safely convert a raw value to cache format with fallback handling.

    This function handles edge cases where database values may be in unexpected formats,
    such as serialized dictionary strings or corrupted data.

    Args:
        field: The Odoo field object
        raw_value: The raw value from database or input
        record: The record instance for context

    Returns:
        The converted value in cache format, or string representation as fallback
    """
    if raw_value is None:
        return None

    try:
        # Handle special cases for raw values
        if isinstance(raw_value, dict):
            # Convert dict to string for storage/logging purposes
            value_for_cache = str(raw_value)
        else:
            value_for_cache = raw_value

        # Attempt the conversion
        return field.convert_to_cache(value_for_cache, record)

    except (ValueError, TypeError) as e:
        # Fallback: if conversion fails, use the raw value as string
        # This handles cases where database contains unexpected data formats
        return str(raw_value)


def _get_display_value(field, value, record_env):
    """Helper to get the display value for a field's value."""
    if value is None or value is False:
        return ""
    try:
        if field.type == "many2one" and isinstance(value, int):
            return record_env[field.comodel_name].sudo().browse(value).display_name
        if field.type in ["one2many", "many2many"] and isinstance(value, (list, tuple)):
            records = record_env[field.comodel_name].sudo().browse(list(value))
            return ", ".join(records.mapped("display_name"))
        if field.type == "selection":
            # The label, not the technical value ("Incoming Email", not "email").
            # get_values() returns the values only, so the label never showed before 19.0.1.712.
            return dict(field._description_selection(record_env)).get(value, str(value))
    except Exception:
        # Fallback to string representation in case of errors (e.g., deleted records)
        # Limit string representation to 1000 characters to prevent excessive storage
        str_value = str(value)
        return str_value[:1000] + "..." if len(str_value) > 1000 else str_value
    return str(value)


@api.model_create_multi
def patched_create(self, vals_list):
    # Bypass if model is not yet fully loaded, auditing is disabled, or for the audit log model itself
    if (
        not self.env.registry.loaded
        or _should_skip_audit_logging(self.env)
        or self.env["oteny.audit.log"]._is_audit_ignored(self._name)
    ):
        return original_create(self, vals_list)

    records = original_create(self, vals_list)

    # Track newly created records in transaction scope
    # Also track which fields have been initially logged for each record
    audit_data = self.env.cr.precommit.data.setdefault("oteny_audit", {})
    if "newly_created_records" not in audit_data:
        audit_data["newly_created_records"] = defaultdict(set)
    if "initially_logged_fields" not in audit_data:
        audit_data["initially_logged_fields"] = defaultdict(lambda: defaultdict(set))
    newly_created = audit_data["newly_created_records"]
    newly_created[self._name].update(records.ids)

    # Skip if no records were created
    if not records:
        return records

    # The stored columns that the audit log covers (see _is_audited_field)
    columns = [
        name
        for name, field in self._fields.items()
        if field.column_type and _is_audited_field(self, field)
    ]

    if columns:
        query = SQL(
            "SELECT id, %s FROM %s WHERE id IN %s",
            SQL(", ").join(SQL.identifier(col) for col in columns),
            SQL.identifier(self._table),
            tuple(records.ids),
        )
        self.env.cr.execute(query)
        new_data = self.env.cr.dictfetchall()
        new_values = {row["id"]: {col: row[col] for col in columns} for row in new_data}

        # Measure 3: emit ONE snapshot row per created record, capturing every
        # non-default field value. This both compresses storage (~5-8x fewer
        # log rows for inserts) and aligns with the "tombstone" mental model
        # for record-level events. Per-field rows still apply to subsequent
        # updates (see patched_flush).
        from .oteny_audit_log import SNAPSHOT_FIELD_NAME

        logs = []
        snapshot_field_names = defaultdict(set)  # record_id -> {field_name, ...}
        model_fields = self._fields
        for record in records:
            if _record_audit_ignored(self, record):
                continue
            if hasattr(record, "display_name"):
                record_display_name = record.display_name
            else:
                record_display_name = f"ID: {record.id}"

            snapshot = {}
            for col in columns:
                field = model_fields[col]
                raw_val = new_values.get(record.id, {}).get(col)
                new_val_cached = _safe_convert_to_cache(field, raw_val, record)

                if new_val_cached is None:
                    continue

                if field.default:
                    default_value = field.default(record)
                    default_value_cached = field.convert_to_cache(default_value, record)
                    if new_val_cached == default_value_cached:
                        continue
                elif _is_empty_without_default(field, new_val_cached):
                    # "Absent" already means "empty" for a field without a default.
                    continue

                new_val_display = _get_display_value(field, new_val_cached, record.env)

                new_value_raw = str(new_val_cached) if new_val_cached is not None else ""
                # Measure 2: HTML-strip raw + display values for marked fields.
                new_value_raw = _maybe_strip_html(self, col, new_value_raw)
                new_val_display = _maybe_strip_html(self, col, new_val_display)
                # Never store a credential. The snapshot KEEPS the field, with a placeholder
                # value, so the change is still recorded and the aggregated view still unnests
                # one row for it.
                new_value_raw = _maybe_redact(self, field, new_value_raw, record)
                new_val_display = _maybe_redact(self, field, new_val_display, record)

                snapshot[col] = {
                    "raw": new_value_raw,
                    "display": new_val_display or "",
                    "label": field.string or col,
                }
                snapshot_field_names[record.id].add(col)

            # Even when no fields produced content (all defaults), emit a
            # snapshot row so the create operation is recorded. The snapshot
            # is just an empty dict in that case.
            logs.append(
                {
                    "model_name": self._name,
                    "record_id": record.id,
                    "record_display_name": record_display_name,
                    "field_name": SNAPSHOT_FIELD_NAME,
                    "field_display_name": f"{self._description or self._name} (snapshot)",
                    "old_value": "",
                    "new_value": "",
                    "old_value_display_name": "",
                    "new_value_display_name": "",
                    "change_type": "i",
                    "snapshot": snapshot,
                }
            )

        if logs:
            log_records = _create_audit_logs(self.env, self, logs)
            # Remember the insert row of each new record: a change later in this
            # transaction goes into that snapshot (_fold_into_insert_snapshots).
            if log_records:
                insert_log_ids = audit_data.setdefault("insert_log_ids", defaultdict(dict))
                for log in log_records:
                    insert_log_ids[self._name][log.record_id] = log.id

            # Mark all snapshot fields as initially logged for newly created
            # records so patched_flush won't double-log them as updates if a
            # subsequent flush in the same transaction touches the same fields.
            initially_logged = audit_data["initially_logged_fields"]
            for record_id, fnames in snapshot_field_names.items():
                if record_id in newly_created[self._name]:
                    initially_logged[self._name][record_id].update(fnames)

    return records


def patched_unlink(self):
    # Bypass if model is not yet fully loaded, auditing is disabled, or for the audit log model itself
    if (
        not self.env.registry.loaded
        or _should_skip_audit_logging(self.env)
        or self.env["oteny.audit.log"]._is_audit_ignored(self._name)
    ):
        return original_unlink(self)

    # Skip if no records are being unlinked
    if not self:
        return True

    columns = [
        name
        for name, field in self._fields.items()
        if field.column_type and _is_audited_field(self, field)
    ]

    old_values = {}
    if columns:
        query = SQL(
            "SELECT id, %s FROM %s WHERE id IN %s",
            SQL(", ").join(SQL.identifier(col) for col in columns),
            SQL.identifier(self._table),
            tuple(self.ids),
        )
        self.env.cr.execute(query)
        old_data = self.env.cr.dictfetchall()
        old_values = {row["id"]: {col: row[col] for col in columns} for row in old_data}

    # Measure 3: emit ONE snapshot row per deleted record. The snapshot must
    # contain every non-default field value so that the deleted record can be
    # fully reconstructed from log + (current state of any still-existing
    # related records) until log expiry. This is the tombstone for the
    # delete event.
    from .oteny_audit_log import SNAPSHOT_FIELD_NAME

    logs = []
    model_fields = self._fields
    for record in self:
        if _record_audit_ignored(self, record):
            continue
        try:
            record_display_name = record.display_name
        except Exception:
            record_display_name = f"ID: {record.id}"

        snapshot = {}
        for col in columns:
            field = model_fields[col]
            raw_val = old_values.get(record.id, {}).get(col)
            old_val = _safe_convert_to_cache(field, raw_val, record)

            if old_val is None or old_val == "":
                continue

            old_val_display = _get_display_value(field, old_val, record.env)

            old_value_raw = str(old_val) if old_val is not None else ""
            # Measure 2: strip HTML to plain text for fields marked as strippable.
            old_value_raw = _maybe_strip_html(self, col, old_value_raw)
            old_val_display = _maybe_strip_html(self, col, old_val_display)
            # The delete tombstone is the row that would otherwise preserve a rotated-out
            # secret for the whole retention window, so it needs the same redaction.
            old_value_raw = _maybe_redact(self, field, old_value_raw, record)
            old_val_display = _maybe_redact(self, field, old_val_display, record)

            snapshot[col] = {
                "raw": old_value_raw,
                "display": old_val_display or "",
                "label": field.string or col,
            }

        # Always emit a snapshot row, even when the snapshot is empty (defaults
        # only). The row itself is the tombstone proof that the record existed
        # and was deleted at this point in time.
        logs.append(
            {
                "model_name": self._name,
                "record_id": record.id,
                "record_display_name": record_display_name,
                "field_name": SNAPSHOT_FIELD_NAME,
                "field_display_name": f"{self._description or self._name} (snapshot)",
                "old_value": "",
                "new_value": "",
                "old_value_display_name": "",
                "new_value_display_name": "",
                "change_type": "d",
                "snapshot": snapshot,
            }
        )

    if logs:
        _create_audit_logs(self.env, self, logs)

    return original_unlink(self)


def patched_write(self, vals):
    # Bypass if model is not yet fully loaded, auditing is disabled, or for the audit log model itself
    if (
        not self.env.registry.loaded
        or _should_skip_audit_logging(self.env)
        or self.env["oteny.audit.log"]._is_audit_ignored(self._name)
    ):
        return original_write(self, vals)

    # Skip if no records or values are provided
    if not self or not vals:
        return original_write(self, vals)

    # The fields in vals that the audit log covers (see _is_audited_field)
    fields_to_check = {
        name: self._fields[name]
        for name in vals
        if name in self._fields and _is_audited_field(self, self._fields[name])
    }

    relational_fields = {n: f for n, f in fields_to_check.items() if f.type in ("one2many", "many2many")}
    scalar_fields = {n: f for n, f in fields_to_check.items() if f.type not in ("one2many", "many2many")}

    # --- IMMEDIATE LOGGING FOR RELATIONAL FIELDS ---
    # Relational field writes (like m2m with commands) are executed immediately.
    # We must capture the state before and after the original_write call.
    if relational_fields:
        # Pre-fetch old values for all records and all relational fields at once
        old_values_list = self.read(list(relational_fields.keys()))
        old_values_map = {rec["id"]: rec for rec in old_values_list}

    # --- DEFERRED LOGGING SETUP FOR SCALAR FIELDS ---
    # Scalar fields are not written until flush, so we just capture the old value
    # and let patched_flush handle the logging.
    if scalar_fields:
        # Use transaction-scoped storage instead of cursor attributes
        audit_data = self.env.cr.precommit.data.setdefault("oteny_audit", {})
        if "old_values" not in audit_data:
            audit_data["old_values"] = defaultdict(dict)

        # If a field is written to again, it should be logged again.
        # We remove it from the logged_changes cache to allow the next flush to process it.
        if "logged_changes" in audit_data:
            logged_changes = audit_data["logged_changes"]
            for field in scalar_fields.values():
                if field in logged_changes:
                    # Remove the records being written from the set of logged changes for this field.
                    logged_changes[field] -= set(self.ids)

        dirty_fields = self.env._field_dirty
        # Pre-fetch scalar fields not in cache to get their old values
        records_to_read_ids = {
            rec.id
            for rec in self
            for field in scalar_fields.values()
            if rec.id is not None
            and rec.id not in dirty_fields.get(field, set())
            and not self.env.cache.contains(rec, field)
        }
        if records_to_read_ids:
            self.sudo().browse(list(records_to_read_ids)).read(list(scalar_fields.keys()))

        for record in self:
            for name, field in scalar_fields.items():
                # Check if this field is already dirty for this record
                if record.id not in dirty_fields.get(field, set()):
                    # First modification: capture old value from cache if present
                    if self.env.cache.contains(record, field):
                        old_val = self.env.cache.get(record, field)
                        audit_data["old_values"][field][record.id] = old_val

    # Perform the original write for ALL fields
    result = original_write(self, vals)

    # --- FINISH IMMEDIATE LOGGING FOR RELATIONAL FIELDS ---
    if relational_fields:
        # Invalidate cache to ensure we re-read from the database
        self.invalidate_recordset(fnames=list(relational_fields.keys()))
        new_values_list = self.read(list(relational_fields.keys()))
        new_values_map = {rec["id"]: rec for rec in new_values_list}

        logs = []
        fold_entries = []
        insert_log_ids = _live_insert_log_ids(self.env, self, self.ids)
        for record in self:
            if _record_audit_ignored(self, record):
                continue
            old_rec_vals = old_values_map.get(record.id, {})
            new_rec_vals = new_values_map.get(record.id, {})
            try:
                record_display_name = record.display_name
            except Exception:
                record_display_name = f"ID: {record.id}"

            for name, field in relational_fields.items():
                old_ids = old_rec_vals.get(name, [])
                new_ids = new_rec_vals.get(name, [])

                if record.id in insert_log_ids and set(old_ids) != set(new_ids):
                    # Created in this transaction: the change goes into its insert snapshot.
                    fold_entries.append((record, field, tuple(new_ids)))
                    continue

                # Odoo's read() returns a list of IDs for m2m.
                # To compare, we can just compare the sets of IDs.
                if set(old_ids) != set(new_ids):
                    # To get display values, we need to browse.
                    old_records = self.env[field.comodel_name].sudo().browse(old_ids)
                    new_records = self.env[field.comodel_name].sudo().browse(new_ids)

                    old_val_cache = field.convert_to_cache(old_records, record)
                    new_val_cache = field.convert_to_cache(new_records, record)

                    old_val_display = _get_display_value(field, old_val_cache, record.env)
                    new_val_display = _get_display_value(field, new_val_cache, record.env)

                    old_value_raw = str(old_val_cache) if old_val_cache is not None else ""
                    new_value_raw = str(new_val_cache) if new_val_cache is not None else ""
                    # Only x2many fields reach this branch, so no relational value can hold a
                    # credential today. The call is here so the redaction covers every emitting
                    # site by inspection, the way _maybe_strip_html should have.
                    old_value_raw = _maybe_redact(self, field, old_value_raw, record)
                    new_value_raw = _maybe_redact(self, field, new_value_raw, record)
                    old_val_display = _maybe_redact(self, field, old_val_display, record)
                    new_val_display = _maybe_redact(self, field, new_val_display, record)

                    logs.append(
                        {
                            "model_name": self._name,
                            "record_id": record.id,
                            "record_display_name": record_display_name,
                            "field_name": name,
                            "field_display_name": field.string,
                            "old_value": old_value_raw,
                            "new_value": new_value_raw,
                            "old_value_display_name": old_val_display,
                            "new_value_display_name": new_val_display,
                            "change_type": "u",
                        }
                    )
        if fold_entries:
            _fold_into_insert_snapshots(self.env, self, fold_entries)
        if logs:
            _create_audit_logs(self.env, self, logs)

    return result


def patched_flush(self, fnames=None):
    """
    Patched flush method that captures changes and logs them to audit table.
    Compatible with Odoo 18's flush signature.
    """
    # Bypass if model is not yet fully loaded, auditing is disabled, or for the audit log model itself
    if (
        not self.env.registry.loaded
        or _should_skip_audit_logging(self.env)
        or self.env["oteny.audit.log"]._is_audit_ignored(self._name)
    ):
        return original_flush(self)

    # Use transaction-scoped storage to prevent duplicate logging within the same transaction.
    audit_data = self.env.cr.precommit.data.setdefault("oteny_audit", {})
    if "logged_changes" not in audit_data:
        audit_data["logged_changes"] = defaultdict(set)
    logged_changes = audit_data["logged_changes"]

    records = self

    # Get dirty fields from cache
    dirty_fields_dict = self.env._field_dirty

    # Determine all fields of the current model that are dirty, regardless of `fnames`.
    # The original_flush will clear all of them from the cache, so we must audit all of them.
    all_dirty_model_fields = {
        field
        for field, dirty_ids in dirty_fields_dict.items()
        if field.model_name == self._name and dirty_ids
    }

    if not all_dirty_model_fields:
        # No dirty fields for this model to worry about.
        return original_flush(self)

    # The dirty fields that the audit log covers (see _is_audited_field).
    loggable_fields_dict = {
        field.name: field for field in all_dirty_model_fields if _is_audited_field(self, field)
    }

    if not loggable_fields_dict:
        return original_flush(self)

    # Collect all dirty record IDs for the fields we're flushing, skipping already logged ones.
    all_ids = set()
    batches = {}

    for name, field in loggable_fields_dict.items():
        dirty_ids = dirty_fields_dict.get(field, set())
        # If specific records are passed, only flush those that are dirty
        if records._ids:
            batch_ids = dirty_ids & set(records._ids)
        else:
            # If `self` is an empty recordset, it implies we should flush for all dirty records of the model.
            batch_ids = dirty_ids

        # Filter out changes that have already been logged in this transaction
        unlogged_ids = batch_ids - logged_changes[field]

        if unlogged_ids:
            batches[name] = unlogged_ids
            all_ids.update(unlogged_ids)

    if not all_ids:
        # All dirty fields have already been logged in this transaction.
        return original_flush(self)

    # Per-record skip (see _record_audit_ignored). Resolved once per flush rather than per
    # (field, record) pair, and only for a model that declares the hook at all.
    skipped_ids = set()
    if getattr(self, "_oteny_audit_ignore_record", None) is not None:
        skipped_ids = {
            rec.id for rec in self.sudo().browse(all_ids) if _record_audit_ignored(self, rec)
        }
        all_ids -= skipped_ids

    # Get display names for all affected records
    display_names = {}
    for rec in self.sudo().browse(all_ids):
        try:
            display_names[rec.id] = rec.display_name
        except Exception:
            display_names[rec.id] = f"ID: {rec.id}"

    # Collect old values
    old_values = {}
    missing_queries = defaultdict(list)  # field: [ids needing DB query]
    audit_old_values = audit_data.get("old_values", {})
    # Track all record IDs that need to be checked for existence
    all_record_ids_to_check = set()

    for name, field in loggable_fields_dict.items():
        field_old_values = audit_old_values.get(field, {})
        for rid in batches.get(name, []):
            all_record_ids_to_check.add(rid)
            if rid in field_old_values:
                old_values.setdefault(rid, {})[name] = field_old_values[rid]
            else:
                missing_queries[field].append(rid)

    # Check which records are newly created in this transaction
    newly_created = audit_data.get("newly_created_records", {}).get(self._name, set())
    initially_logged = audit_data.get("initially_logged_fields", {}).get(self._name, {})

    # Query database for missing old values in a single batch
    if missing_queries:
        all_missing_ids = list({rid for rids in missing_queries.values() for rid in rids})
        all_missing_fields = list(missing_queries.keys())
        if all_missing_ids:
            field_names = [field.name for field in all_missing_fields if field.column_type]
            if field_names:
                query = SQL(
                    "SELECT id, %s FROM %s WHERE id IN %s",
                    SQL(", ").join(SQL.identifier(col) for col in field_names),
                    SQL.identifier(self._table),
                    tuple(all_missing_ids),
                )
                self.env.cr.execute(query)
                db_data_map = {row["id"]: row for row in self.env.cr.dictfetchall()}

                for field in all_missing_fields:
                    if field.name not in field_names:
                        continue
                    for rid in missing_queries[field]:
                        row = db_data_map.get(rid)
                        if row:
                            record = self.sudo().browse(rid)
                            val = _safe_convert_to_cache(field, row[field.name], record)
                            old_values.setdefault(rid, {})[field.name] = val
                        elif rid in newly_created:
                            # For new records, set old value as None/empty
                            old_values.setdefault(rid, {})[field.name] = None

    # Perform original flush
    original_flush(self)

    # After flush, re-read all flushed fields to get their new values
    # Use oteny_audit_ignore context to prevent infinite recursion
    if all_ids:
        new_values_list = (
            self.sudo()
            .browse(list(all_ids))
            .with_context(oteny_audit_ignore=True)
            .read(list(loggable_fields_dict.keys()))
        )
        new_values_map = {rec["id"]: rec for rec in new_values_list}
    else:
        new_values_map = {}

    # Log changes using old and new values
    logs = []
    if "most_recent_logs" not in audit_data:
        audit_data["most_recent_logs"] = defaultdict(lambda: defaultdict(dict))
    most_recent_logs = audit_data["most_recent_logs"]

    insert_log_ids = _live_insert_log_ids(self.env, self, all_ids)
    fold_entries = []
    for name, field in loggable_fields_dict.items():
        for rid in batches.get(name, []):
            if rid in skipped_ids:
                continue
            record = self.sudo().browse(rid)
            old_val = old_values.get(rid, {}).get(name)

            new_val_raw = new_values_map.get(rid, {}).get(name)
            new_val = _safe_convert_to_cache(field, new_val_raw, record)

            if rid in insert_log_ids:
                # Created in this transaction: the value goes into its insert snapshot
                # (cut E). The snapshot keeps the value at the end of the transaction.
                if old_val != new_val:
                    fold_entries.append((record, field, new_val))
                logged_changes[field].add(rid)
                continue

            if old_val != new_val:
                most_recent = most_recent_logs[field].get(rid)
                if (
                    most_recent
                    and most_recent.get("old_value") == old_val
                    and most_recent.get("new_value") == new_val
                ):
                    continue

                # Measure 3: the create event is captured atomically by the
                # tombstone snapshot emitted in patched_create. Any field
                # change that surfaces later via flush (e.g. a computed field
                # whose recompute happens after the create read, or a write
                # that targets a freshly created record) is conceptually a
                # post-create update, so we always log it as 'u'. The legacy
                # "second 'i' row for late fields" pattern is no longer
                # needed and would produce double inserts per record event.
                change_type = "u"

                old_val_display = _get_display_value(field, old_val, record.env)
                new_val_display = _get_display_value(field, new_val, record.env)

                old_value_raw = str(old_val) if old_val is not None else ""
                new_value_raw = str(new_val) if new_val is not None else ""
                # Measure 2: strip HTML to plain text for fields marked as strippable.
                old_value_raw = _maybe_strip_html(self, name, old_value_raw)
                new_value_raw = _maybe_strip_html(self, name, new_value_raw)
                old_val_display = _maybe_strip_html(self, name, old_val_display)
                new_val_display = _maybe_strip_html(self, name, new_val_display)
                # A scalar write is the main leak path: a rotation puts BOTH the old and the
                # new credential in one row.
                old_value_raw = _maybe_redact(self, field, old_value_raw, record)
                new_value_raw = _maybe_redact(self, field, new_value_raw, record)
                old_val_display = _maybe_redact(self, field, old_val_display, record)
                new_val_display = _maybe_redact(self, field, new_val_display, record)

                logs.append(
                    {
                        "model_name": self._name,
                        "record_id": rid,
                        "record_display_name": display_names.get(rid, f"ID: {rid}"),
                        "field_name": name,
                        "field_display_name": field.string,
                        "old_value": old_value_raw,
                        "new_value": new_value_raw,
                        "old_value_display_name": old_val_display,
                        "new_value_display_name": new_val_display,
                        "change_type": change_type,
                    }
                )
                most_recent_logs[field][rid] = {"old_value": old_val, "new_value": new_val}

    if fold_entries:
        _fold_into_insert_snapshots(self.env, self, fold_entries)
    if logs:
        _create_audit_logs(self.env, self, logs)
        # Mark these changes as logged to prevent duplicates in the same transaction.
        for log in logs:
            field = self._fields.get(log["field_name"])
            if field:
                logged_changes[field].add(log["record_id"])

    # Clear old_values after flush
    if "old_values" in audit_data:
        # Only clear the fields/records we just flushed
        audit_old_values = audit_data["old_values"]
        for name, field in loggable_fields_dict.items():
            if field in audit_old_values:
                field_old_values = audit_old_values[field]
                for rid in batches.get(name, []):
                    field_old_values.pop(rid, None)
                # If no more records for this field, remove the field entry
                if not field_old_values:
                    del audit_old_values[field]


# Apply all patches
models.BaseModel.create = patched_create
models.BaseModel.unlink = patched_unlink
models.BaseModel.write = patched_write
models.BaseModel._flush = patched_flush
