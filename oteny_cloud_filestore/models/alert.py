import logging

from odoo import SUPERUSER_ID, api, fields, models

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "oteny_cloud_filestore."


class CloudFilestoreAlert(models.AbstractModel):
    """One Discuss message when the bucket starts to fail, one when it works again.

    The state is the system parameter `outage_since`: empty while the bucket
    works. Both methods run in a separate cursor, so a rollback of the
    user's transaction does not drop the alert or the state.
    """

    _name = "oteny.cloud.filestore.alert"
    _description = "Cloud filestore outage alert"

    @api.model
    def _outage_since(self):
        return self.env["ir.config_parameter"].sudo().get_param(PARAM_PREFIX + "outage_since", "")

    @api.model
    def _bucket_failed(self, reason):
        """Start an outage and tell the admins once; do nothing when one is open."""
        if self._outage_since():
            return
        self._switch(
            outage_since=fields.Datetime.to_string(fields.Datetime.now()),
            expect_open=False,
            message=(
                f"The cloud filestore bucket of database {self.env.cr.dbname} is not working "
                f"({reason}). New files stay in the local filestore until the bucket works again."
            ),
        )

    @api.model
    def _bucket_ok(self):
        """End an outage and tell the admins once. Cheap when no outage is open."""
        if not self._outage_since():
            return
        self._switch(
            outage_since="",
            expect_open=True,
            message=(
                f"The cloud filestore bucket of database {self.env.cr.dbname} works again. "
                "Files that stayed local are uploaded by the catch-up job."
            ),
        )

    @api.model
    def _switch(self, outage_since, expect_open, message):
        """Switch the outage state once, even with several workers and saves at the same time.

        The state row is read and locked with SQL in the alert cursor: the
        parameter cache is process-wide and the user's cursor has an old
        snapshot, so neither shows the committed state. A lock that is not
        available means another worker is switching the state now. Any error
        is logged and swallowed, because an alert must never fail a save.
        A state row that an admin deleted is created again (empty: no
        outage), or no alert would ever be posted.
        """
        key = PARAM_PREFIX + "outage_since"
        try:
            with self.env.registry.cursor() as cr:
                # The channel row can be locked by the user's own transaction; never wait for it.
                cr.execute("SET LOCAL lock_timeout = '2s'")
                cr.execute(
                    """
                    INSERT INTO ir_config_parameter
                           (key, value, create_uid, create_date, write_uid, write_date)
                    VALUES (%s, '', %s, now() AT TIME ZONE 'UTC', %s, now() AT TIME ZONE 'UTC')
                    ON CONFLICT (key) DO NOTHING
                    """,
                    [key, SUPERUSER_ID, SUPERUSER_ID],
                )
                cr.execute("SELECT value FROM ir_config_parameter WHERE key = %s FOR UPDATE", [key])
                row = cr.fetchone()
                if row is None or bool(row[0]) != expect_open:
                    return
                cr.execute("UPDATE ir_config_parameter SET value = %s WHERE key = %s", [outage_since, key])
                env = api.Environment(cr, SUPERUSER_ID, {})
                channel_id = int(env["ir.config_parameter"].get_param(PARAM_PREFIX + "alert_channel_id", "0") or 0)
                channel = env["discuss.channel"].browse(channel_id).exists()
                if channel:
                    channel.message_post(body=message, message_type="comment", subtype_xmlid="mail.mt_comment")
                else:
                    _logger.warning("Cloud filestore: no alert channel to post to: %s", message)
            self.env.registry.clear_cache("stable")
        except Exception as error:  # noqa: BLE001
            _logger.warning("Cloud filestore: alert not posted: %s", type(error).__name__)
