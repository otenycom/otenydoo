import logging

from odoo import api, models
from odoo.tools import config

from ..tools.cloud_store import CloudStore

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "oteny_cloud_filestore."

# One CloudStore per full set of settings, so a read does not build a boto3
# client every time, and a settings change builds a new one. The keys of
# this dict hold secrets: never log or print them.
_STORE_CACHE = {}


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    @api.model
    def _cloud_filestore_param(self, key, default=""):
        return self.env["ir.config_parameter"].sudo().get_param(PARAM_PREFIX + key, default) or default

    @api.model
    def _cloud_filestore_mode(self):
        """Return "off", "production" or "clone".

        "off": no endpoint or bucket is set, so Odoo behaves as without the
        module. "production": the write key is in odoo.conf and the database
        is not neutralized. Every other case is "clone": a copy of the
        database must never put or delete objects in the bucket, even if the
        write key was copied into its odoo.conf.
        """
        if not (self._cloud_filestore_param("endpoint_url") and self._cloud_filestore_param("bucket")):
            return "off"
        has_write_key = bool(
            config.get("cloud_filestore_write_access_key_id")
            and config.get("cloud_filestore_write_secret_access_key")
        )
        is_neutralized = bool(self.env["ir.config_parameter"].sudo().get_param("database.is_neutralized"))
        if has_write_key and not is_neutralized:
            return "production"
        return "clone"

    @api.model
    def _cloud_filestore_store(self):
        """Return the cached CloudStore for the current mode; None when off."""
        mode = self._cloud_filestore_mode()
        if mode == "off":
            return None
        if mode == "production":
            access_key_id = config.get("cloud_filestore_write_access_key_id")
            secret_access_key = config.get("cloud_filestore_write_secret_access_key")
        else:
            access_key_id = self._cloud_filestore_param("read_access_key_id")
            secret_access_key = self._cloud_filestore_param("read_secret_access_key")
        if not (access_key_id and secret_access_key):
            # Empty keys would let boto3 fall back to ambient credentials
            # (environment, ~/.aws, instance profile): never use those.
            _logger.warning("Cloud filestore: no complete access key for mode %s, store disabled", mode)
            return None
        settings = (
            self._cloud_filestore_param("endpoint_url"),
            self._cloud_filestore_param("region"),
            self._cloud_filestore_param("bucket"),
            self._cloud_filestore_param("addressing_style", "path"),
            self._cloud_filestore_param("key_prefix"),
            access_key_id,
            secret_access_key,
        )
        store = _STORE_CACHE.get(settings)
        if store is None:
            store = _STORE_CACHE[settings] = CloudStore(
                endpoint_url=settings[0],
                region=settings[1],
                bucket=settings[2],
                addressing_style=settings[3],
                key_prefix=settings[4],
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
            )
        return store
