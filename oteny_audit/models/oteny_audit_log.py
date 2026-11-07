# audit_log/models/audit_log.py
import hashlib
import json
import logging
from collections import defaultdict

from odoo import Command, api, fields, models
from odoo.sql_db import db_connect
from odoo.tools import SQL

from .oteny_audit_fields import BigInteger

_logger = logging.getLogger(__name__)

_AUDIT_ACTION_XML_MODULE = "oteny_audit"
_AUDIT_ACTION_XML_PREFIX = "action_audit_log_"

# Sentinel field_name for a "tombstone" snapshot row (Measure 3).
# Inserts and deletes are recorded as a single audit log row per record event,
# with the full set of non-default field values stored in `snapshot`. The
# sentinel keeps the existing `field_name` NOT NULL constraint intact.
SNAPSHOT_FIELD_NAME = "__snapshot__"


class OtenyAuditLog(models.Model):
    _name = "oteny.audit.log"
    _description = "Oteny Audit Log"
    _order = "id desc"

    # Predefined parent references or id-keys for well-known models
    _model_parent_keys = {
        "mail.message": "model,res_id",
        "ir.attachment": "res_model,res_id",
        "mail.followers": "res_model,res_id",
        "res.partner": "parent_id",
    }

    # PostgreSQL transaction number (txid_current), a 64-bit counter: see oteny_audit_fields.
    transaction_id = BigInteger(required=False, string="Transaction ID")
    model_name = fields.Char(required=True)
    record_id = fields.Integer(required=True, string="Record ID")
    record_ref = fields.Reference(
        string="Record Link",
        selection="_selection_record_ref",
        compute="_compute_record_ref",
        readonly=True,
    )
    record_display_name = fields.Char(string="Record Name")
    field_name = fields.Char(required=True, string="Field Raw Name")
    field_display_name = fields.Char(string="Field")
    old_value = fields.Text(string="Old Value Raw")
    new_value = fields.Text(string="New Value Raw")
    old_value_display_name = fields.Char(string="Old Value")
    new_value_display_name = fields.Char(string="New Value")
    # A remark ("r") is a line of text that says what a person or a process did, written by
    # _log_remark. It is not a field change and holds no field value.
    change_type = fields.Selection(
        [("i", "Insert"), ("u", "Update"), ("d", "Delete"), ("r", "Remark")], required=True
    )
    # Measure 3: tombstone-style snapshot for inserts and deletes.
    # Holds the full set of non-default field values for the record event in
    # the shape {field_name: {"raw": str, "display": str}}. Together with the
    # per-field update rows that remain unchanged, this makes the record state
    # fully reconstructable from log + (current record if still present),
    # while collapsing the row count of inserts/deletes by ~5-8x.
    snapshot = fields.Json(
        string="Snapshot",
        help=(
            "For tombstone insert/delete rows: full non-default field values "
            "at the time of the event, structured as "
            "{field_name: {'raw': ..., 'display': ...}}. NULL on per-field rows."
        ),
    )

    @api.model
    def init(self):
        """Initialize the model and create necessary database indexes."""
        super().init()

        _logger.info("Initializing OtenyAuditLog indexes...")

        # Create performance indexes for the audit log queries
        cr = self.env.cr

        # Index for model_name (used in JOINs with ir_model)
        if not self._index_exists("oteny_audit_log_model_name_idx"):
            _logger.info("Creating index oteny_audit_log_model_name_idx")
            cr.execute(
                """
                CREATE INDEX oteny_audit_log_model_name_idx
                ON oteny_audit_log (model_name)
            """
            )
        else:
            _logger.info("Index oteny_audit_log_model_name_idx already exists")

        # Index for create_date (used in ORDER BY operations)
        if not self._index_exists("oteny_audit_log_create_date_idx"):
            _logger.info("Creating index oteny_audit_log_create_date_idx")
            cr.execute(
                """
                CREATE INDEX oteny_audit_log_create_date_idx
                ON oteny_audit_log (create_date DESC)
            """
            )
        else:
            _logger.info("Index oteny_audit_log_create_date_idx already exists")

        # Index for transaction_id (used in window functions)
        if not self._index_exists("oteny_audit_log_transaction_id_idx"):
            _logger.info("Creating index oteny_audit_log_transaction_id_idx")
            cr.execute(
                """
                CREATE INDEX oteny_audit_log_transaction_id_idx
                ON oteny_audit_log (transaction_id)
            """
            )
        else:
            _logger.info("Index oteny_audit_log_transaction_id_idx already exists")

        # Composite index for window function partitioning (model_name, record_id, create_date DESC, id DESC)
        if not self._index_exists("oteny_audit_log_model_record_date_id_idx"):
            _logger.info("Creating index oteny_audit_log_model_record_date_id_idx")
            cr.execute(
                """
                CREATE INDEX oteny_audit_log_model_record_date_id_idx
                ON oteny_audit_log (model_name, record_id, create_date DESC, id DESC)
            """
            )
        else:
            _logger.info("Index oteny_audit_log_model_record_date_id_idx already exists")

        # Composite index serving field_name= equality + create_date range
        # lookups in the audit search (no existing index here leads with field_name).
        if not self._index_exists("oteny_audit_log_field_name_create_date_idx"):
            _logger.info("Creating index oteny_audit_log_field_name_create_date_idx")
            cr.execute(
                """
                CREATE INDEX oteny_audit_log_field_name_create_date_idx
                ON oteny_audit_log (field_name, create_date DESC)
            """
            )
        else:
            _logger.info("Index oteny_audit_log_field_name_create_date_idx already exists")

        _logger.info("OtenyAuditLog indexes initialization completed")

    def _index_exists(self, index_name):
        """Check if a database index exists."""
        cr = self.env.cr
        cr.execute(
            """
            SELECT 1 FROM pg_indexes
            WHERE tablename = %s AND indexname = %s
        """,
            (self._table, index_name),
        )
        return cr.fetchone() is not None

    def _selection_record_ref(self):
        models = self.env["ir.model"].search([])
        return [(model.model, model.name) for model in models]

    @api.depends("model_name", "record_id")
    def _compute_record_ref(self):
        for log in self:
            if log.model_name and log.record_id and log.model_name in self.env:
                record = self.env[log.model_name].browse(log.record_id).exists()
                log.record_ref = f"{log.model_name},{record.id}" if record else False
            else:
                log.record_ref = False

    # Infrastructure models whose audit value is near-zero: mail.mail is just
    # the outgoing-queue copy of mail.message bodies (auto-deleted after send),
    # mail.notification/followers/presence are notification plumbing,
    # discuss.channel holds Wilma per-conversation scratchpad, and
    # ir.cron.progress is pure cron-internal counter churn.
    #
    # `mail.message` is intentionally NOT in this list: chatter messages tied
    # to a business record are cascade-deleted when the parent is unlinked, so
    # the audit log is the only place that can preserve them after that point.
    # We pay the cost via HTML stripping on `mail.message.body` (see
    # mail_message_override.py) — body bytes drop ~94% while remaining
    # readable, and parent refs (via `_model_parent_keys`) make the chatter
    # appear under the business record's Audit Log view.
    #
    # A message posted into a container that is ITSELF on this list has no such
    # business record to outlive, so it is skipped one record at a time by
    # `mail.message._oteny_audit_ignore_record`. That is the per-RECORD lever;
    # this set is the per-MODEL one.
    #
    # A specific module can still re-enable auditing for any of these by
    # setting `_oteny_audit_ignore = False` on the model class.
    _DEFAULT_IGNORED_MODEL_NAMES = {
        "mail.mail",
        "mail.notification",
        "mail.followers",
        "mail.presence",
        "discuss.channel",
        "discuss.channel.member",
        "ir.cron.progress",
        # A row in the scheduler queue (a cron and a time); Odoo deletes it after the run.
        "ir.cron.trigger",
        # The reflection of database constraints, rewritten by every upgrade.
        "ir.model.constraint",
    }

    def _is_audit_ignored(self, model_name):
        """Check if a model should be ignored by the audit log."""
        if self.env.context.get("oteny_audit_ignore", False):
            return True

        # Model may exist in ir.model (database) but no longer in the registry
        # (e.g. after a module dropped a model). Can't audit what doesn't exist.
        try:
            model_class = self.env[model_name]
        except KeyError:
            return True
        if hasattr(model_class, "_oteny_audit_ignore"):
            return model_class._oteny_audit_ignore

        # Ignore TransientModel models (wizards) by default
        if getattr(model_class, "_transient", False):
            return True

        # Default-ignore for high-noise infrastructure models (see comment on
        # _DEFAULT_IGNORED_MODEL_NAMES above). A model in this set can still
        # opt back in by setting _oteny_audit_ignore = False.
        if model_name in self._DEFAULT_IGNORED_MODEL_NAMES:
            return True

        # Ignore system models by default, can be overridden by _oteny_audit_ignore in the model
        ignored_model_prefixes = [
            "oteny.audit",
            "ir.ui.view",
            "ir.model.data",
            "bus.",
            "mail.push",
            "mail.tracking",
            "res.device.log",
        ]
        if any(model_name.startswith(prefix) for prefix in ignored_model_prefixes):
            return True

        return False

    @api.model
    def _log_remark(self, records, key, label, text):
        """Add a remark to the audit log of each record: a line of text that says what a
        person or a process did, for example the transition a user ran.

        ``key`` is the caller's fixed technical name for the kind of remark. It goes in
        ``field_name``, so it is the same in every language and a filter on it keeps
        working. ``label`` goes in ``field_display_name`` and ``text`` in the new value.

        A remark is written like every other audit row (user, time, transaction, parent
        references) and follows the same skip rules. A skip writes nothing and raises
        nothing."""
        # Imported here: base_patch patches the ORM on import, after this module (see __init__).
        from .base_patch import _create_audit_logs, _record_audit_ignored, _should_skip_audit_logging

        if (
            not records
            or not self.env.registry.loaded
            or _should_skip_audit_logging(self.env)
            or self._is_audit_ignored(records._name)
        ):
            return

        logs = []
        for record in records:
            if _record_audit_ignored(records, record):
                continue
            try:
                record_display_name = record.display_name
            except Exception:
                record_display_name = f"ID: {record.id}"
            logs.append(
                {
                    "model_name": records._name,
                    "record_id": record.id,
                    "record_display_name": record_display_name,
                    "field_name": key,
                    "field_display_name": label,
                    "old_value": "",
                    "new_value": text,
                    "old_value_display_name": "",
                    "new_value_display_name": text,
                    "change_type": "r",
                }
            )
        _create_audit_logs(self.env, records, logs)

    @api.model
    def _audit_log_action_xml_id(self, technical_model_name):
        """Full XML id for the per-model audit server action (``module.name`` for ``ir.model.data``).

        Uses a SHA-256 hex suffix so distinct technical names never collide (unlike
        ``model.replace('.', '_')``, where e.g. ``a.b.c`` and ``a.b_c`` map to the same string).
        Only ``[a-z0-9_]`` appear in the suffix, which is valid for Odoo external identifiers.
        """
        digest = hashlib.sha256(technical_model_name.encode("utf-8")).hexdigest()
        return f"{_AUDIT_ACTION_XML_MODULE}.{_AUDIT_ACTION_XML_PREFIX}{digest}"

    def install_for_all_models_action(self):
        """Create audit log actions for all models that don't already have them.
        This will be called automatically when modules are installed.
        Creates one action per model for the raw aggregated view.
        """
        # Get all non-transient models except ourselves
        all_models = self.env["ir.model"].search([("transient", "=", False), ("model", "!=", self._name)])
        eligible_models = all_models.filtered(lambda m: not self._is_audit_ignored(m.model))
        # One ir.model row per technical model name (DB can contain duplicates, e.g. stale rows);
        # otherwise _batch_process_actions would try to create the same xml_id twice.
        seen_names = set()
        unique_ids = []
        for m in eligible_models.sorted("id"):
            if m.model in seen_names:
                continue
            seen_names.add(m.model)
            unique_ids.append(m.id)
        eligible_models = self.env["ir.model"].browse(unique_ids)

        _logger.info(f"Starting audit log action setup for {len(eligible_models)} models")

        # Process actions in batches for better performance and readability
        actions_created, actions_updated = self._batch_process_actions(eligible_models)

        # Clean up actions for models that are no longer eligible
        actions_removed = self._cleanup_obsolete_actions(eligible_models)

        # Log summary
        total_models = len(eligible_models)
        _logger.info(
            f"Audit log action setup complete: {actions_created} created, {actions_updated} updated, {actions_removed} removed "
            f"({total_models} models processed)"
        )

        return {
            "type": "ir.actions.client",
            "tag": "display_notification",
            "params": {
                "title": "Audit Log Setup Complete",
                "message": f"Created {actions_created} new audit actions ({total_models} models), updated {actions_updated} existing ones.",
                "sticky": False,
            },
        }

    def _get_action_code(self):
        """Get the action code template for the audit log action."""
        ACTION_CODE = """
action = {
    "name": "Audit Log",
    "type": "ir.actions.act_window",
    "res_model": "oteny.audit.log.aggregated",
    "view_mode": "list,form",
    "domain": [("model_name", "=", records._name), ("record_id", "in", records.ids)],
    "target": "current",
}
"""
        return ACTION_CODE.strip()

    def _batch_process_actions(self, models):
        """Process action creation/updates in batches for better performance."""
        action_code = self._get_action_code()

        actions_data = []

        for model in models:
            xml_id = self._audit_log_action_xml_id(model.model)
            actions_data.append(
                {
                    "xml_id": xml_id,
                    "name": f"Audit Log for {model.name}",
                    "model_id": model.id,
                    "code": action_code,
                }
            )

        # Process actions
        created, updated = self._process_action_batch(actions_data, "audit")

        return created, updated

    def _process_action_batch(self, actions_data, action_type):
        """Process a batch of actions, creating new ones and updating existing ones."""
        module_name = _AUDIT_ACTION_XML_MODULE
        actions_created = 0
        actions_updated = 0

        # Check which actions already exist
        xml_ids = [data["xml_id"] for data in actions_data]
        existing_refs = {}
        for xml_id in xml_ids:
            existing_ref = self.env.ref(xml_id, raise_if_not_found=False)
            if existing_ref:
                existing_refs[xml_id] = existing_ref

        # Process each action
        # Setting group_ids bypasses Odoo's default write access check on the bound model.
        # Without group_ids, server actions require write access to the model, which is
        # too restrictive for read-only audit log viewing. With group_ids set to
        # base.group_user, any internal user can view audit logs.
        group_user = self.env.ref("base.group_user")
        for action_data in actions_data:
            xml_id = action_data["xml_id"]
            action_vals = {
                "name": action_data["name"],
                "model_id": action_data["model_id"],
                "binding_model_id": action_data["model_id"],
                "state": "code",
                "binding_view_types": "list,form",
                "code": action_data["code"],
                "group_ids": [Command.set([group_user.id])],
            }

            if xml_id in existing_refs:
                # Update existing action
                _logger.debug(f"{action_type.title()} action already exists for model, updating it.")
                existing_refs[xml_id].write(action_vals)
                actions_updated += 1
            else:
                # Create new action
                new_action = self.env["ir.actions.server"].create(action_vals)
                self.env["ir.model.data"].create(
                    {
                        "name": xml_id.split(".")[1],
                        "module": module_name,
                        "res_id": new_action.id,
                        "model": "ir.actions.server",
                        "noupdate": True,
                    }
                )
                existing_refs[xml_id] = new_action
                actions_created += 1

        return actions_created, actions_updated

    def _cleanup_obsolete_actions(self, eligible_models):
        """Remove actions for models that are no longer eligible for auditing."""
        module_name = _AUDIT_ACTION_XML_MODULE
        actions_removed = 0

        # Build a set of expected XML IDs for all eligible models
        expected_xml_ids = {self._audit_log_action_xml_id(m.model) for m in eligible_models}

        # Find all existing audit log actions for this module
        existing_xml_id_records = self.env["ir.model.data"].search(
            [
                ("module", "=", module_name),
                ("name", "like", f"{_AUDIT_ACTION_XML_PREFIX}%"),
                ("model", "=", "ir.actions.server"),
            ]
        )

        for xml_id_record in existing_xml_id_records:
            full_xml_id = f"{xml_id_record.module}.{xml_id_record.name}"
            if full_xml_id not in expected_xml_ids:
                action_to_remove = self.env["ir.actions.server"].browse(xml_id_record.res_id)
                if action_to_remove.exists():
                    _logger.debug(f"Removing obsolete audit action with XML ID {full_xml_id}")
                    xml_id_record.unlink()
                    action_to_remove.unlink()
                    actions_removed += 1

        return actions_removed

    def _get_cleanup_config(self):
        """Get cleanup configuration from system parameters."""
        get_param = self.env["ir.config_parameter"].sudo().get_param
        return {
            "enabled": get_param("oteny_audit.cleanup_enabled", "True").lower() == "true",
            "retention_days": int(get_param("oteny_audit.retention_days", "180")),
            "batch_size": int(get_param("oteny_audit.batch_size", "5000")),
            "pause_seconds": int(get_param("oteny_audit.cleanup_pause_seconds", "1")),
        }

    def cleanup_old_audit_logs(self):
        """
        Clean up old audit log records based on configuration.
        This method is called by the cron job.

        Transaction Strategy:
        - Commits after each batch to free memory and prevent timeouts
        - Handles large datasets efficiently by processing in chunks
        - Includes error handling for commit operations
        """
        config = self._get_cleanup_config()

        if not config["enabled"]:
            _logger.info("Audit log cleanup is disabled")
            return

        # Calculate the cutoff date using Odoo's UTC time
        from odoo.fields import Datetime
        from datetime import timedelta

        cutoff_date = Datetime.now() - timedelta(days=config["retention_days"])

        _logger.info(
            f"Starting audit log cleanup. Deleting records older than {config['retention_days']} days "
            f"(before {cutoff_date.date()}). Batch size: {config['batch_size']}, "
            f"Pause: {config['pause_seconds']} seconds"
        )

        # Get total count of records to be deleted
        old_logs_count = self.search_count([("create_date", "<", cutoff_date)])
        _logger.info(f"Found {old_logs_count} audit log records to delete")

        if old_logs_count == 0:
            _logger.info("No old audit log records to delete")
            return

        deleted_count = 0
        import time

        # Delete in batches
        while True:
            # Find batch of records to delete
            old_logs = self.search(
                [("create_date", "<", cutoff_date)], limit=config["batch_size"], order="id"
            )

            if not old_logs:
                break

            batch_count = len(old_logs)
            _logger.info(f"Deleting batch of {batch_count} audit log records...")

            # Delete the batch
            old_logs.unlink()

            # Commit the transaction after each batch to free up memory and avoid timeouts
            try:
                self.env.cr.commit()
                _logger.debug(f"Committed batch deletion of {batch_count} records")
            except Exception as e:
                _logger.warning(f"Failed to commit batch deletion: {e}")

            deleted_count += batch_count
            _logger.info(f"Deleted {deleted_count}/{old_logs_count} audit log records")

            # Check if there are more records to delete
            remaining_count = self.search_count([("create_date", "<", cutoff_date)])
            if remaining_count == 0:
                break

            # Pause between batches to allow DB to process
            if config["pause_seconds"] > 0:
                _logger.info(f"Pausing for {config['pause_seconds']} seconds...")
                time.sleep(config["pause_seconds"])

        _logger.info(f"Audit log cleanup completed. Deleted {deleted_count} records")

        # Also clean up references for deleted logs
        _logger.info("Cleaning up orphaned references...")
        self.env.cr.execute(
            """
            DELETE FROM oteny_audit_log_ref
            WHERE audit_log_id NOT IN (SELECT id FROM oteny_audit_log)
        """
        )
        orphaned_refs_count = self.env.cr.rowcount
        _logger.info(f"Deleted {orphaned_refs_count} orphaned reference records")

        # Commit the final cleanup
        try:
            self.env.cr.commit()
            _logger.debug("Committed final cleanup of orphaned references")
        except Exception as e:
            _logger.warning(f"Failed to commit final cleanup: {e}")

        return {
            "deleted_logs": deleted_count,
            "deleted_refs": orphaned_refs_count,
            "cutoff_date": cutoff_date.date(),
        }


    # ------------------------------------------------------------------------------------
    # Purge of the stored rows that the current coverage would not write
    # (plan oteny-audit/plans/audit-storage-reduction.md, § 11)
    # ------------------------------------------------------------------------------------
    # The capture rules change in 19.0.1.712 (cuts A to I, rules N1 to N4). The rows written
    # before stay until this purge removes what the new rules would not have written. It runs
    # as a one-shot scheduled action at night, switched on by the 19.0.1.712 migration, in
    # batches with a commit each, and ends with VACUUM FULL, which gives the space back.
    # Phases, kept in a system parameter so a later run goes on where the last stopped:
    #   deletes - rows of ignored models, update rows of fields that are not audited, rows of
    #             records that a per-record rule skips (_oteny_audit_purge_record_sql);
    #   fold    - update rows in the transaction of their record's insert go into the insert
    #             snapshot; every snapshot loses the keys of fields that are not audited, and
    #             an insert snapshot its empty values of fields without a default;
    #   refs    - the direct references (cut F);
    #   vacuum  - VACUUM (FULL, ANALYZE) of the two tables, then the action switches off;
    #   done    - a later run does nothing but switch the action off again.
    _PURGE_PHASE_PARAM = "oteny_audit.purge_phase"
    _PURGE_NEXT_ID_PARAM = "oteny_audit.purge_next_id"
    _PURGE_BATCH = 20000
    _EMPTY_RAW_VALUES = ("", "False", "0", "0.0", "[]", "()")

    def _purge_params(self):
        """System parameters of the purge, written without an audit row: the position of a
        batch is no business change, and each batch would add rows to the table it purges."""
        return self.env["ir.config_parameter"].sudo().with_context(oteny_audit_ignore=True)

    def _purge_rows_outside_coverage(self):
        """Scheduled action: one batch of the purge per call; Odoo calls again while the
        progress reports work left."""
        Cron = self.env["ir.cron"]
        params = self._purge_params()
        phase = params.get_param(self._PURGE_PHASE_PARAM) or "deletes"
        if phase == "done":
            # Run again after the end (by hand, or a cron that was not switched off): nothing
            # to do, and no second VACUUM FULL.
            self._purge_switch_off()
            Cron._commit_progress(1, remaining=0, deactivate=True)
            return
        if phase == "vacuum":
            self.env.cr.commit()  # hold no lock on the audit tables while VACUUM waits for one
            if self._purge_vacuum():
                params.set_param(self._PURGE_PHASE_PARAM, "done")
                params.set_param(self._PURGE_NEXT_ID_PARAM, False)
                self._purge_switch_off()
                Cron._commit_progress(1, remaining=0, deactivate=True)
            else:
                Cron._commit_progress(1, remaining=0)  # the next night tries again
            return
        done, phase_done = self._purge_step(phase)
        if phase_done:
            phase = {"deletes": "fold", "fold": "refs", "refs": "vacuum"}[phase]
            params.set_param(self._PURGE_PHASE_PARAM, phase)
            params.set_param(self._PURGE_NEXT_ID_PARAM, False)
        Cron._commit_progress(max(done, 1), remaining=1)

    def _purge_switch_off(self):
        """Switch the purge action off. The cron's own `deactivate` covers a scheduled run;
        this covers a run by hand (Run Manually, a shell)."""
        cron = self.env.ref("oteny_audit.ir_cron_audit_log_purge", raise_if_not_found=False)
        if cron and cron.active:
            cron.sudo().with_context(oteny_audit_ignore=True).active = False

    def _purge_all_now(self):
        """Run every phase but VACUUM in this transaction, without a commit (tests, a shell)."""
        for phase in ("deletes", "fold", "refs"):
            self._purge_params().set_param(self._PURGE_NEXT_ID_PARAM, False)
            while not self._purge_step(phase)[1]:
                pass
        self._purge_params().set_param(self._PURGE_NEXT_ID_PARAM, False)

    def _purge_step(self, phase):
        """One batch of `phase`. Returns (rows handled, True when the phase is done)."""
        return getattr(self, f"_purge_step_{phase}")()

    def _purge_delete_rows(self, log_ids):
        """Delete log rows and their references (references first, so the cascade of the
        foreign key does not look them up one by one)."""
        if not log_ids:
            return
        ids = tuple(log_ids)
        self.env.cr.execute(SQL("DELETE FROM oteny_audit_log_ref WHERE audit_log_id IN %s", ids))
        self.env.cr.execute(SQL("DELETE FROM oteny_audit_log WHERE id IN %s", ids))

    def _purge_outside_condition(self):
        """SQL over `log` for the rows that the current coverage would not write."""
        from .base_patch import _is_audited_field

        cr = self.env.cr
        cr.execute("SELECT DISTINCT model_name FROM oteny_audit_log")
        model_names = [row[0] for row in cr.fetchall()]
        ignored = [name for name in model_names if self._is_audit_ignored(name)]
        conditions = []
        if ignored:
            conditions.append(SQL("log.model_name IN %s", tuple(ignored)))
        cr.execute(SQL(
            "SELECT DISTINCT model_name, field_name FROM oteny_audit_log WHERE change_type = 'u'"
        ))
        fields_out = [
            (model_name, field_name)
            for model_name, field_name in cr.fetchall()
            if model_name in self.env and model_name not in ignored
            and field_name in self.env[model_name]._fields
            and not _is_audited_field(self.env[model_name], self.env[model_name]._fields[field_name])
        ]
        for model_name, field_name in fields_out:
            conditions.append(SQL(
                "(log.change_type = 'u' AND log.model_name = %s AND log.field_name = %s)",
                model_name, field_name,
            ))
        for model_name in model_names:
            if model_name in ignored or model_name not in self.env:
                continue
            hook = getattr(self.env[model_name], "_oteny_audit_purge_record_sql", None)
            condition = hook() if hook else None
            if condition:
                conditions.append(SQL("(log.model_name = %s AND %s)", model_name, condition))
        return SQL(" OR ").join(conditions) if conditions else None

    def _purge_step_deletes(self):
        condition = self._purge_outside_condition()
        if condition is None:
            return 0, True
        self.env.cr.execute(SQL(
            "SELECT log.id FROM oteny_audit_log log WHERE %s ORDER BY log.id LIMIT %s",
            condition, self._PURGE_BATCH,
        ))
        ids = [row[0] for row in self.env.cr.fetchall()]
        self._purge_delete_rows(ids)
        _logger.info("Audit purge: deleted %d rows outside the coverage", len(ids))
        return len(ids), len(ids) < self._PURGE_BATCH

    def _purge_step_fold(self):
        """Fold and clean the snapshot rows with an id in the next range."""
        from .base_patch import _is_audited_field

        cr = self.env.cr
        params = self._purge_params()
        cr.execute("SELECT min(id), max(id) FROM oteny_audit_log")
        min_id, max_id = cr.fetchone()
        start = max(int(params.get_param(self._PURGE_NEXT_ID_PARAM) or 0), min_id or 0)
        max_id = max_id or 0
        end = start + self._PURGE_BATCH * 5
        cr.execute(SQL(
            """SELECT id, model_name, record_id, transaction_id, change_type, snapshot
                 FROM oteny_audit_log
                WHERE id >= %s AND id < %s AND field_name = '__snapshot__'
                  AND change_type IN ('i', 'd') AND snapshot IS NOT NULL""",
            start, end,
        ))
        snapshots = cr.fetchall()
        inserts = [row for row in snapshots if row[4] == "i"]
        folded = defaultdict(list)  # insert log id -> [(update id, field, raw, display, label)]
        if inserts:
            cr.execute(SQL(
                """SELECT ins.id, upd.id, upd.field_name, upd.new_value,
                          upd.new_value_display_name, upd.field_display_name
                     FROM oteny_audit_log ins
                     JOIN oteny_audit_log upd
                       ON upd.model_name = ins.model_name AND upd.record_id = ins.record_id
                      AND upd.transaction_id = ins.transaction_id AND upd.change_type = 'u'
                    WHERE ins.id IN %s
                    ORDER BY upd.id""",
                tuple(row[0] for row in inserts),
            ))
            for ins_id, upd_id, field_name, raw, display, label in cr.fetchall():
                folded[ins_id].append((upd_id, field_name, raw, display, label))

        def audited(model, name):
            field = model._fields.get(name) if model is not None else None
            # A field that no longer exists keeps its value: it is history, not a rule change.
            return field is None or _is_audited_field(model, field)

        handled = 0
        for log_id, model_name, _record_id, _txid, change_type, snapshot in snapshots:
            model = self.env[model_name] if model_name in self.env else None
            new = {}
            for name, entry in (snapshot or {}).items():
                if not audited(model, name):
                    continue
                if change_type == "i" and self._purge_empty_without_default(model, name, entry):
                    continue
                new[name] = entry
            for _upd_id, name, raw, display, label in folded.get(log_id, ()):
                if not audited(model, name):
                    continue
                entry = {"raw": raw or "", "display": display or "", "label": label or name}
                if self._purge_empty_without_default(model, name, entry):
                    new.pop(name, None)
                else:
                    new[name] = entry
            if new != snapshot:
                cr.execute(SQL("UPDATE oteny_audit_log SET snapshot = %s::jsonb WHERE id = %s",
                               json.dumps(new), log_id))
                handled += 1
        moves = [(ins_id, upd[0]) for ins_id, upds in folded.items() for upd in upds]
        if moves:
            # A parent reference of a folded row that its insert row lacks moves to it.
            cr.execute(SQL(
                """INSERT INTO oteny_audit_log_ref
                          (audit_log_id, target_model_name, target_record_id, target_display_name,
                           parent_display_name, is_direct, create_date, transaction_id,
                           create_uid, write_uid, write_date)
                   SELECT DISTINCT ON (m.ins_id, ref.target_model_name, ref.target_record_id)
                          m.ins_id, ref.target_model_name, ref.target_record_id,
                          ref.target_display_name, ref.parent_display_name, FALSE,
                          ref.create_date, ref.transaction_id, ref.create_uid, ref.write_uid,
                          ref.write_date
                     FROM (SELECT unnest(%s::int[]) AS ins_id, unnest(%s::int[]) AS upd_id) m
                     JOIN oteny_audit_log_ref ref ON ref.audit_log_id = m.upd_id AND NOT ref.is_direct
                    WHERE NOT EXISTS (
                          SELECT 1 FROM oteny_audit_log_ref own
                           WHERE own.audit_log_id = m.ins_id AND NOT own.is_direct
                             AND own.target_model_name = ref.target_model_name
                             AND own.target_record_id = ref.target_record_id)
                    ORDER BY m.ins_id, ref.target_model_name, ref.target_record_id, m.upd_id DESC""",
                [m[0] for m in moves], [m[1] for m in moves],
            ))
            self._purge_delete_rows([m[1] for m in moves])
        params.set_param(self._PURGE_NEXT_ID_PARAM, end)
        _logger.info("Audit purge: ids %d-%d, %d snapshots rewritten, %d updates folded",
                     start, end, handled, len(moves))
        return max(handled + len(moves), 1), end > max_id

    def _purge_empty_without_default(self, model, name, entry):
        """True for a stored empty value of a field without a default (rule N1)."""
        field = model._fields.get(name) if model is not None else None
        if field is None or field.default:
            return False
        return (entry or {}).get("raw", "") in self._EMPTY_RAW_VALUES

    def _purge_step_refs(self):
        self.env.cr.execute(SQL(
            """DELETE FROM oteny_audit_log_ref WHERE id IN (
                   SELECT id FROM oteny_audit_log_ref WHERE is_direct LIMIT %s)""",
            self._PURGE_BATCH * 5,
        ))
        count = self.env.cr.rowcount
        _logger.info("Audit purge: deleted %d direct references", count)
        return count, count < self._PURGE_BATCH * 5

    def _purge_vacuum(self):
        """VACUUM FULL gives the space of the deleted rows back to the disk. It takes an
        ACCESS EXCLUSIVE lock on both tables (seconds to minutes), so every write that adds
        an audit row waits meanwhile; the action runs at night. VACUUM cannot run inside a
        transaction, so it gets its own connection in autocommit. Returns False when it could
        not get its lock within a minute; the next run tries again."""
        try:
            with db_connect(self.env.cr.dbname).cursor() as vacuum_cr:
                vacuum_cr._cnx.autocommit = True
                vacuum_cr.execute("SET lock_timeout = '60s'")
                vacuum_cr.execute("VACUUM (FULL, ANALYZE) oteny_audit_log, oteny_audit_log_ref")
        except Exception:
            _logger.warning("Audit purge: VACUUM FULL failed; the next run tries again", exc_info=True)
            return False
        _logger.info("Audit purge: VACUUM FULL done")
        return True
