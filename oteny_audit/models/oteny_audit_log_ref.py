# oteny_audit/models/oteny_audit_log_ref.py
from odoo import fields, models, api

from .oteny_audit_fields import BigInteger
import logging

_logger = logging.getLogger(__name__)


class OtenyAuditLogRef(models.Model):
    _name = "oteny.audit.log.ref"
    _description = "Oteny Audit Log Reference"
    _order = "create_date desc, id desc"

    audit_log_id = fields.Many2one(
        "oteny.audit.log",
        required=True,
        ondelete="cascade",
        string="Audit Log Entry",
    )
    target_model_name = fields.Char(required=True, string="Target Model")
    target_record_id = fields.Integer(required=True, string="Target Record ID")
    target_display_name = fields.Char(string="Target Record Name")
    parent_display_name = fields.Char(string="Parent Record Name")
    is_direct = fields.Boolean(default=True, string="Is Direct Reference")
    create_date = fields.Datetime(string="Create Date", readonly=True)
    # Copied from the log row; 64-bit for the same reason (see oteny_audit_fields).
    transaction_id = BigInteger(string="Transaction ID")

    @api.model
    def init(self):
        """Initialize the model and create necessary database indexes."""
        super().init()

        _logger.info("Initializing OtenyAuditLogRef indexes...")

        cr = self.env.cr

        # Primary query pattern index (most important for performance)
        if not self._index_exists("oteny_audit_log_ref_target_idx"):
            _logger.info("Creating index oteny_audit_log_ref_target_idx")
            cr.execute(
                """
                CREATE INDEX oteny_audit_log_ref_target_idx 
                ON oteny_audit_log_ref (target_model_name, target_record_id, create_date DESC, id DESC)
            """
            )
        else:
            _logger.info("Index oteny_audit_log_ref_target_idx already exists")

        # Index for joining back to audit_log
        if not self._index_exists("oteny_audit_log_ref_audit_log_idx"):
            _logger.info("Creating index oteny_audit_log_ref_audit_log_idx")
            cr.execute(
                """
                CREATE INDEX oteny_audit_log_ref_audit_log_idx 
                ON oteny_audit_log_ref (audit_log_id)
            """
            )
        else:
            _logger.info("Index oteny_audit_log_ref_audit_log_idx already exists")

        # Optional: For transaction-based queries if needed
        if not self._index_exists("oteny_audit_log_ref_transaction_idx"):
            _logger.info("Creating index oteny_audit_log_ref_transaction_idx")
            cr.execute(
                """
                CREATE INDEX oteny_audit_log_ref_transaction_idx 
                ON oteny_audit_log_ref (target_model_name, target_record_id, transaction_id)
            """
            )
        else:
            _logger.info("Index oteny_audit_log_ref_transaction_idx already exists")

        _logger.info("OtenyAuditLogRef indexes initialization completed")

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
