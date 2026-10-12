import logging
import os
import re
import time

from werkzeug.exceptions import NotFound

from odoo import api, fields, models
from odoo.http import Stream
from odoo.tools import config, split_every

from ..tools.cloud_store import CloudStore, CloudStoreError

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "oteny_cloud_filestore."

DEFAULT_HOLD_DAYS = 365
SECONDS_PER_DAY = 86400

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

    def _mark_for_gc(self, fname):
        """Mark the name as Odoo does; in production the marker age restarts now.

        The hold time counts from the last delete, so a name that was reused
        and removed again gets a fresh marker time.
        """
        super()._mark_for_gc(fname)
        if self._cloud_filestore_mode() != "production":
            return
        marker = os.path.join(self._full_path("checklist"), re.sub("[.:]", "", fname).strip("/\\"))
        try:
            os.utime(marker)
        except OSError as error:
            _logger.warning("Cloud filestore: marker time not updated: %s", error)

    @api.model
    def _cloud_filestore_hold_days(self):
        """Return the hold time in days; a bad value never shortens it."""
        value = self._cloud_filestore_param("hold_days", str(DEFAULT_HOLD_DAYS))
        try:
            days = int(value)
        except ValueError:
            days = 0
        if days <= 0:
            _logger.warning(
                "Cloud filestore: hold_days %r is not a positive whole number, using %s",
                value,
                DEFAULT_HOLD_DAYS,
            )
            return DEFAULT_HOLD_DAYS
        return days

    def _gc_file_store_unsafe(self):
        """Garbage-collect the filestore; in production delete bucket objects only after the hold time.

        The checklist walk and the whitelist query are reproduced from Odoo,
        because Odoo's version deletes at once and has no place to wait. The
        marker files stay the master list of pending bucket deletes: a marker
        younger than the hold time is kept for the next run. A restore sets
        every marker to the restore date, and a lost checklist means no
        bucket delete at all, so both failures are safe.
        """
        if self._cloud_filestore_mode() != "production":
            return super()._gc_file_store_unsafe()

        checklist = {}
        for dirpath, _dirnames, filenames in os.walk(self._full_path("checklist")):
            dirname = os.path.basename(dirpath)
            for filename in filenames:
                checklist["%s/%s" % (dirname, filename)] = os.path.join(dirpath, filename)

        store = self._cloud_filestore_store()
        hold_seconds = self._cloud_filestore_hold_days() * SECONDS_PER_DAY
        now = time.time()
        store_failed = False
        local_removed = bucket_deleted = kept = 0
        for names in split_every(self.env.cr.IN_MAX, checklist):
            self.env.cr.execute("SELECT store_fname FROM ir_attachment WHERE store_fname IN %s", [names])
            whitelist = set(row[0] for row in self.env.cr.fetchall())

            for fname in names:
                marker = checklist[fname]
                if fname not in whitelist:
                    try:
                        os.unlink(self._full_path(fname))
                        local_removed += 1
                    except FileNotFoundError:
                        pass
                    except OSError as error:
                        _logger.warning("Cloud filestore: local file not removed: %s", error)
                    try:
                        is_old = now - os.path.getmtime(marker) > hold_seconds
                    except OSError:
                        is_old = False
                    if not (is_old and store is not None and not store_failed):
                        kept += 1
                        continue
                    try:
                        store.delete(fname)
                        bucket_deleted += 1
                    except CloudStoreError as error:
                        # One failure likely means the bucket is down: stop deleting in this run.
                        store_failed = True
                        kept += 1
                        _logger.warning("Cloud filestore: bucket delete failed, markers kept: %s", error)
                        self._cloud_filestore_alert("_bucket_failed", str(error))
                        continue
                try:
                    os.unlink(marker)
                except OSError:
                    pass
        _logger.info(
            "Cloud filestore gc: %d checked, %d local removed, %d bucket deleted, %d kept",
            len(checklist),
            local_removed,
            bucket_deleted,
            kept,
        )

    @api.model
    def _cloud_filestore_local_names(self):
        """Return the sorted names of the files in the local filestore.

        Only the two-level names `xx/<sha>` count. The checklist folder holds
        the delete markers and is skipped, and so is anything else that Odoo
        did not write.
        """
        root = self._filestore()
        names = []
        for folder in sorted(os.listdir(root)) if os.path.isdir(root) else []:
            folder_path = os.path.join(root, folder)
            if folder == "checklist" or not os.path.isdir(folder_path):
                continue
            for filename in sorted(os.listdir(folder_path)):
                if os.path.isfile(os.path.join(folder_path, filename)):
                    names.append("%s/%s" % (folder, filename))
        return names

    @api.model
    def _cloud_filestore_upload_pending(self, limit=500):
        """Move local user files to the bucket; return the number of confirmed uploads.

        This one job covers the first migration of an existing database and
        the catch-up after a bucket outage. A file is pending when at least
        one attachment that is not local-only uses its name. The local copy
        is removed only after the bucket reports the same size, so a lost or
        short object never costs the only copy. A run stops at the first
        bucket error, because the next file would most likely fail as well.
        """
        if self._cloud_filestore_mode() != "production":
            return 0
        store = self._cloud_filestore_store()
        if store is None:
            return 0
        uploaded = not_confirmed = 0
        failed = False
        names = self._cloud_filestore_local_names()
        for batch in split_every(self.env.cr.IN_MAX, names):
            if failed or uploaded >= limit:
                break
            self.env.cr.execute(
                "SELECT DISTINCT store_fname FROM ir_attachment WHERE store_fname IN %s AND NOT cloud_local_only",
                [tuple(batch)],
            )
            pending = set(row[0] for row in self.env.cr.fetchall())
            for name in batch:
                if name not in pending:
                    continue
                if uploaded >= limit:
                    break
                path = self._full_path(name)
                try:
                    with open(path, "rb") as local_file:
                        data = local_file.read()
                except OSError as error:
                    _logger.warning("Cloud filestore: local file %s not read: %s", name, error)
                    continue
                try:
                    store.put(name, data)
                    size = store.head(name)
                except CloudStoreError as error:
                    _logger.warning("Cloud filestore: upload job stopped at %s: %s", name, error)
                    self._cloud_filestore_alert("_bucket_failed", str(error))
                    failed = True
                    break
                if size != len(data):
                    # None can also mean a missing bucket: the local file is the only safe copy.
                    not_confirmed += 1
                    _logger.warning("Cloud filestore: upload of %s not confirmed, file stays local", name)
                    continue
                try:
                    os.unlink(path)
                except OSError as error:
                    _logger.warning("Cloud filestore: local copy of %s not removed: %s", name, error)
                uploaded += 1
        _logger.info(
            "Cloud filestore upload job: %d uploaded, %d not confirmed, %d files checked%s",
            uploaded,
            not_confirmed,
            len(names),
            ", stopped by a bucket error" if failed else "",
        )
        if uploaded:
            self._cloud_filestore_alert("_bucket_ok")
        return uploaded

    @api.model
    def _cloud_filestore_download_all(self, limit=500):
        """Bring files from the bucket back to the local filestore; return the number written.

        This is the reverse migration, started by hand. It never overwrites a
        local file, and it stops at the first bucket error. A run with a
        limit can be repeated: files that are local already are skipped.
        """
        if self._cloud_filestore_mode() == "off":
            return 0
        store = self._cloud_filestore_store()
        if store is None:
            return 0
        self.env.flush_all()
        self.env.cr.execute("SELECT DISTINCT store_fname FROM ir_attachment WHERE store_fname IS NOT NULL")
        names = sorted(row[0] for row in self.env.cr.fetchall())
        written = missing = 0
        for name in names:
            if written >= limit:
                break
            path = self._full_path(name)
            if os.path.exists(path):
                continue
            try:
                data = store.get(name)
            except CloudStoreError as error:
                _logger.warning("Cloud filestore: download job stopped at %s: %s", name, error)
                break
            if data is None:
                missing += 1
                _logger.warning("Cloud filestore: %s is not in the bucket, skipped", name)
                continue
            try:
                os.makedirs(os.path.dirname(path), exist_ok=True)
                # Mode "x" fails when the file appeared meanwhile: never overwrite.
                with open(path, "xb") as local_file:
                    local_file.write(data)
            except FileExistsError:
                continue
            except OSError as error:
                _logger.warning("Cloud filestore: local file %s not written: %s", name, error)
                break
            written += 1
        _logger.info(
            "Cloud filestore download job: %d written, %d missing in the bucket, %d names checked",
            written,
            missing,
            len(names),
        )
        return written

    def _cloud_filestore_alert(self, method, *args):
        """Call an alert method; a failing alert never fails the user's save."""
        try:
            getattr(self.env["oteny.cloud.filestore.alert"], method)(*args)
        except Exception as error:  # noqa: BLE001
            _logger.warning("Cloud filestore: alert %s failed: %s", method, type(error).__name__)
