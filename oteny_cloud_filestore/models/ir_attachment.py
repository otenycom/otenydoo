import logging
import os

from werkzeug.exceptions import NotFound

from odoo import api, fields, models
from odoo.http import Stream
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
        store = self._cloud_filestore_store()
        try:
            if store is None:
                # An incomplete key is a failure too, never an ambient-credential fallback.
                raise CloudStoreError("no complete write key")
            store.put(fname, bin_value)
        except CloudStoreError as error:
            _logger.warning("Cloud filestore: upload of %s failed, file stays local: %s", fname, error)
            self._cloud_filestore_alert("_bucket_failed", str(error))
            return fname
        try:
            os.unlink(self._full_path(fname))
        except FileNotFoundError:
            pass
        except OSError as error:
            # The upload is confirmed; a local copy that stays is only wasted disk space.
            _logger.warning("Cloud filestore: local copy of %s not removed: %s", fname, error)
        self._cloud_filestore_alert("_bucket_ok")
        return fname

    def _file_read(self, fname, size=None):
        """Read the local file; when it is not there, read it from the bucket.

        A bucket error never raises: the caller gets empty bytes, as Odoo
        returns for a missing local file. In production the admins get an
        alert; a clone only logs.
        """
        mode = self._cloud_filestore_mode()
        if mode == "off" or os.path.exists(self._full_path(fname)):
            return super()._file_read(fname, size=size)
        store = self._cloud_filestore_store()
        if store is None:
            return b""
        try:
            data = store.get(fname)
        except CloudStoreError as error:
            _logger.warning("Cloud filestore: read of %s failed: %s", fname, error)
            if mode == "production":
                self._cloud_filestore_alert("_bucket_failed", str(error))
            return b""
        if data is None:
            return b""
        return data[:size] if size is not None else data

    def _to_http_stream(self):
        """Stream a file that exists only in the bucket through Odoo.

        The standard method stats a path in the local filestore and fails for
        a bucket-only file. The browser never sees the bucket: the bytes are
        read here and sent by Odoo. The whole file is held in memory.
        """
        self.ensure_one()
        if (
            self.store_fname
            and self._cloud_filestore_mode() != "off"
            and not os.path.exists(self._full_path(self.store_fname))
        ):
            data = self._file_read(self.store_fname)
            if not data and self.file_size:
                # The bucket failed or lost the object. An empty 200 with the real
                # etag could be cached and stay blank after the bucket recovers.
                raise NotFound()
            return Stream(
                type="data",
                data=data,
                mimetype=self.mimetype,
                download_name=self.name,
                etag=self.checksum,
                public=self.public,
                last_modified=self.write_date,
                size=len(data),
            )
        return super()._to_http_stream()

    def _cloud_filestore_alert(self, method, *args):
        """Call an alert method; a failing alert never fails the user's save."""
        try:
            getattr(self.env["oteny.cloud.filestore.alert"], method)(*args)
        except Exception as error:  # noqa: BLE001
            _logger.warning("Cloud filestore: alert %s failed: %s", method, type(error).__name__)
