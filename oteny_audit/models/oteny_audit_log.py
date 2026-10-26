# audit_log/models/audit_log.py
import hashlib
import logging

from odoo import Command, api, fields, models

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
