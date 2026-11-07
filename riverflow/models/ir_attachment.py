import logging

from odoo import api, fields, models
from odoo.tools import SQL

_logger = logging.getLogger(__name__)


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.autovacuum
    def _gc_wizard_uploads(self):
        """Delete the files that users uploaded in a wizard and that nothing uses.

        A file uploaded in a wizard (a transient model) gets res_model = the
        wizard model and res_id = 0, before the wizard is saved. Odoo moves such
        a file to the record only for its own mail composer
        (mail.thread._process_attachments_for_post), and deletes the composer's
        lost uploads after one day (mail.compose.message._gc_lost_attachments).
        A custom wizard gets neither, so its uploads stay behind after Odoo has
        vacuumed the wizard. A wizard that copies the bytes elsewhere (an import
        queue) also leaves its upload behind.

        An attachment is deleted when all of these are true:
        - its res_model is a transient model and res_field is empty;
        - it has no access_token. An image pasted into an HTML text gets a token,
          and a sent email or a chatter message shows it through a URL that no
          database link reveals;
        - create_date and write_date are more than one day old, so a wizard
          that is still open keeps its files;
        - no row of a table that does not belong to a wizard points to it
          (a chatter message, a credential document, an import job, ...).
        """
        transient_models = [name for name, model in self.env.registry.items() if model._transient]
        if not transient_models:
            return
        limit_date = fields.Datetime.subtract(fields.Datetime.now(), days=1)
        conditions = [
            SQL("upload.res_model IN %s", tuple(transient_models)),
            SQL("upload.res_field IS NULL"),
            SQL("upload.access_token IS NULL"),
            SQL("upload.create_date < %s", limit_date),
            SQL("upload.write_date < %s", limit_date),
        ]
        for table, column in self._wizard_upload_links(transient_models):
            conditions.append(
                SQL(
                    "NOT EXISTS (SELECT 1 FROM %s AS link WHERE %s = upload.id)",
                    SQL.identifier(table),
                    SQL.identifier("link", column),
                )
            )
        rows = self.env.execute_query(
            SQL("SELECT upload.id FROM ir_attachment AS upload WHERE %s", SQL(" AND ").join(conditions))
        )
        uploads = self.sudo().browse([row[0] for row in rows])
        if uploads:
            _logger.info("Deleting %d wizard uploads that nothing uses", len(uploads))
            uploads.unlink()

    @api.model
    def _wizard_upload_links(self, transient_models):
        """Return (table, column) for every foreign key to ir_attachment that a
        record outside a wizard can hold.

        The foreign keys come from the database, so a link that any installed
        module adds is found without code in that module. A key on the table of
        a transient model does not count, and neither does a key on a relation
        table whose other side is the table of a transient model (the
        attachments of a wizard's Many2many field).
        """
        transient_tables = [self.env[name]._table for name in transient_models]
        rows = self.env.execute_query(
            SQL(
                """
                SELECT owner.relname, attribute.attname
                  FROM pg_constraint AS fkey
                  JOIN pg_class AS owner ON owner.oid = fkey.conrelid
                  JOIN pg_attribute AS attribute
                    ON attribute.attrelid = fkey.conrelid
                   AND attribute.attnum = fkey.conkey[1]
                 WHERE fkey.contype = 'f'
                   AND fkey.confrelid = 'ir_attachment'::regclass
                   AND owner.relname != ALL(%(transient_tables)s)
                   AND NOT EXISTS (
                        SELECT 1
                          FROM pg_constraint AS other
                          JOIN pg_class AS target ON target.oid = other.confrelid
                         WHERE other.contype = 'f'
                           AND other.conrelid = fkey.conrelid
                           AND other.oid != fkey.oid
                           AND target.relname = ANY(%(transient_tables)s)
                   )
                 ORDER BY owner.relname, attribute.attname
                """,
                transient_tables=transient_tables,
            )
        )
        return [(table, column) for table, column in rows]
