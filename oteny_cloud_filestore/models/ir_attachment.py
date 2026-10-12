import logging
import os

from odoo import api, fields, models
from odoo.tools import config

from ..tools.cloud_store import CloudStore, CloudStoreError

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "oteny_cloud_filestore."

# One CloudStore per full set of settings, so a read does not build a boto3
# client every time, and a settings change builds a new one. The keys of
# this dict hold secrets: never log or print them.
_STORE_CACHE = {}


class IrAttachment(models.Model):
    _inherit = "ir.attachment"

    # Stored, because a later batch job must know which files never go to the
    # bucket, and the install_mode context of the create is not kept anywhere.
    cloud_local_only = fields.Boolean(
        string="Stays Local",
        readonly=True,
        copy=False,
        help="Asset bundles and files that a module installs stay in the local filestore.",
    )

    @api.model
    def _cloud_filestore_is_local_only(self):
        """True while the current write is for a file that never goes to the bucket."""
        return bool(self.env.context.get("install_mode") or self.env.context.get("cloud_filestore_local_only"))

    @api.model_create_multi
    def create(self, vals_list):
        """Flag asset bundles and module files as local-only.

        A bundle is rebuilt by Odoo at any time and a module file is
        reinstalled from its module, so neither belongs in the bucket.
        """
        install_mode = bool(self.env.context.get("install_mode"))
        vals_list = [dict(vals) for vals in vals_list]
        for vals in vals_list:
            is_bundle = (vals.get("url") or "").startswith("/web/assets/")
            # An explicit True is kept; a vals can never turn a bundle into a bucket file.
            vals["cloud_local_only"] = bool(is_bundle or install_mode or vals.get("cloud_local_only"))
        if all(vals["cloud_local_only"] for vals in vals_list):
            # _file_write only receives the content and its hash, so it reads this context.
            self = self.with_context(cloud_filestore_local_only=True)
        return super().create(vals_list)

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

    def _set_attachment_data(self, asbytes):
        """A content change of local-only rows must not reach the bucket either."""
        if self and all(self.mapped("cloud_local_only")):
            self = self.with_context(cloud_filestore_local_only=True)
        return super()._set_attachment_data(asbytes)

    def _file_write(self, bin_value, checksum):
        """Write the file locally as Odoo does, then upload it in production.

        The user's save never fails because of the bucket: on a bucket error
        the file stays in the local filestore and the admins get one alert
        per outage. After a confirmed upload the local copy is removed.
        """
        fname = super()._file_write(bin_value, checksum)
        if self._cloud_filestore_mode() != "production" or self._cloud_filestore_is_local_only():
            return fname
        alert = self.env["oteny.cloud.filestore.alert"]
        store = self._cloud_filestore_store()
        try:
            if store is None:
                # An incomplete key is a failure too, never an ambient-credential fallback.
                raise CloudStoreError("no complete write key")
            store.put(fname, bin_value)
        except CloudStoreError as error:
            _logger.warning("Cloud filestore: upload of %s failed, file stays local: %s", fname, error)
            alert._bucket_failed(str(error))
            return fname
        try:
            os.unlink(self._full_path(fname))
        except FileNotFoundError:
            pass
        alert._bucket_ok()
        return fname
