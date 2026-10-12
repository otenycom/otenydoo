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
        with self.env.registry.cursor() as cr:
            env = api.Environment(cr, SUPERUSER_ID, {})
            params = env["ir.config_parameter"]
            # Re-read in the new cursor: another worker may have switched already.
            if bool(params.get_param(PARAM_PREFIX + "outage_since", "")) != expect_open:
                return
            params.set_param(PARAM_PREFIX + "outage_since", outage_since)
            channel_id = int(params.get_param(PARAM_PREFIX + "alert_channel_id", "0") or 0)
            channel = env["discuss.channel"].browse(channel_id).exists()
            if channel:
                channel.message_post(body=message, message_type="comment", subtype_xmlid="mail.mt_comment")
            else:
                _logger.warning("Cloud filestore: no alert channel to post to: %s", message)
