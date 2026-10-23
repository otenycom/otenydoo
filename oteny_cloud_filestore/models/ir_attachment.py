import bisect
import configparser
import functools
import hashlib
import logging
import math
import os
import re
import threading
import time
from uuid import uuid4

import psycopg2
from werkzeug.exceptions import NotFound
from werkzeug.wsgi import ClosingIterator

from odoo import SUPERUSER_ID, api, fields, models
from odoo.addons.base.models.ir_attachment import IrAttachment as BaseIrAttachment
from odoo.addons.base.models.ir_cron import MIN_RUNS_PER_JOB
from odoo.exceptions import UserError
from odoo.http import Stream
from odoo.tools import config, split_every

from ..tools.cloud_store import (
    DELETE_MANY_MAX,
    CloudStore,
    CloudStoreAccessError,
    CloudStoreError,
    CloudStoreUnavailable,
)

_logger = logging.getLogger(__name__)

PARAM_PREFIX = "oteny_cloud_filestore."
# The section of odoo.conf that holds the write key. Odoo reads only [options] and logs an
# "unknown option" warning for every key it does not know there; it never looks at other sections.
WRITE_KEY_SECTION = "oteny_cloud_filestore"

DEFAULT_HOLD_DAYS = 365
# Odoo.sh keeps backups for 3 to 4 months. A bucket object must outlive every
# backup that still points to it, so a shorter hold time is never used.
MIN_HOLD_DAYS = 120
SECONDS_PER_DAY = 86400
# The budget of one cron run of the upload job, well below the time limit of
# an Odoo.sh cron run: three timeouts in a row mark the cron as failed. The
# next call continues where this one stopped (see _UPLOAD_RESUME_AFTER).
UPLOAD_TIME_BUDGET_SECONDS = 240
# Odoo's cron runner calls the job again at once while it reports work left
# (ir.cron._commit_progress): 10 calls (MIN_RUNS_PER_JOB), and more only while
# the run is under 10 s (MIN_TIME_PER_JOB). One call gets its share of the run
# budget, so a run of 10 calls stays near it. The budget is checked before
# each file, so a call can pass it by one upload.
UPLOAD_CALL_BUDGET_SECONDS = UPLOAD_TIME_BUDGET_SECONDS / MIN_RUNS_PER_JOB

# One CloudStore per full set of settings, so a read does not build a boto3
# client every time, and a settings change builds a new one. The keys of
# this dict hold secrets: never log or print them.
_STORE_CACHE = {}

# The last local name that the upload job reached, per database. The next
# call continues after it, so a file kept local on purpose (shared with a
# local-only file, refused, not confirmed) costs one check per sweep, not one
# per call; else such files could use up the budget of every call and the
# cron would run again at once without end. A sweep walks the sorted names
# once; at its end the point is cleared. It lives in process memory, not in
# a system parameter, because each write of a parameter clears the caches of
# every worker. A lost point (a restart, another worker) only means that the
# walk starts at the first name again: extra heads, never a wrong result.
_UPLOAD_RESUME_AFTER = {}

# The size limit of the read cache when the system parameter is not usable (D21).
DEFAULT_CACHE_LIMIT_MB = 100
# Read counts are written at most once per this many seconds per process and database.
CACHE_FLUSH_SECONDS = 60
# The read counts of this process that are not written yet, per database:
# {file name: reads}. The download routes run on a read-only cursor, so a
# read never writes its count itself; the counts are hints, and a lost buffer
# (a restart) only loses some reads. The mark of a new cache copy is state,
# not a hint: it is written at once (_cloud_filestore_cache_fill).
_CACHE_READS = {}
# When this process last wrote the counts of a database (time.monotonic()).
_CACHE_FLUSHED_AT = {}
# The HTTP workers of one process are threads that share the buffer.
_CACHE_LOCK = threading.Lock()
# The last bad cache_limit_mb value that was logged: the read path asks for the limit on every read.
_CACHE_LIMIT_WARNED = [None]


class CloudFilestoreStream(Stream):
    """A Stream whose response says where Odoo read the bytes: "bucket" or "local".

    The header X-Cloud-Filestore shows in the network panel of a browser. It
    names only the place, never a key or an object name.
    """

    location = None

    def get_response(self, *args, **kwargs):
        response = super().get_response(*args, **kwargs)
        if self.location:
            response.headers["X-Cloud-Filestore"] = self.location
        return response


class CloudObjectStream(CloudFilestoreStream):
    """A Stream that sends an open bucket object in chunks, never as one block.

    The worker holds one chunk at a time, and the first bytes leave before the
    object is complete. Odoo's headers come from `Stream.get_response`, which
    runs with an empty placeholder body; this class then replaces the body by
    the chunk generator and sets the real Content-Length. `read()` and `data`
    read the rest of the object, for callers that need all bytes (the image
    resize of ir.binary).
    """

    location = "bucket"
    chunk_size = 65536
    store_fname = None  # the bucket name of the object, for the log

    def __init__(self, cloud_object, **kwargs):
        super().__init__(**kwargs)
        self._cloud_object = cloud_object
        self._bytes = None
        self._placeholder = False

    @property
    def data(self):
        if self._placeholder:
            return b""
        if self._bytes is None:
            try:
                self._bytes = b"".join(self._cloud_object.iter_chunks(self.chunk_size))
            finally:
                self._cloud_object.close()
        return self._bytes

    @data.setter
    def data(self, value):
        # ir.binary replaces the data by the resized image. The object is not
        # needed any more, and the stream turns into a plain data stream.
        self._bytes = value
        self._cloud_object.close()

    def _body(self):
        """Yield the object; a failure closes it and breaks the connection on purpose.

        The headers are already sent, so the only honest signal left is a
        body shorter than Content-Length: the browser then shows a failed
        download, never a complete-looking file. The server logs the broken
        connection too, at ERROR level ("Error on request"), next to this
        warning.
        """
        try:
            yield from self._cloud_object.iter_chunks(self.chunk_size)
        except CloudStoreError as error:
            _logger.warning(
                "Cloud filestore: download of %s (%s) broke off: %s", self.download_name, self.store_fname, error
            )
            raise
        finally:
            self._cloud_object.close()

    def get_response(self, *args, **kwargs):
        if self._bytes is not None:  # `data` was read already: send it as Odoo does
            return super().get_response(*args, **kwargs)
        # A range request gets the full file with 200: the placeholder body is empty,
        # so werkzeug sees no range to serve.
        self._placeholder = True
        try:
            response = super().get_response(*args, **kwargs)
        finally:
            self._placeholder = False
        if response.status_code != 200:
            # 304 (and 412): there is no body to send, so the object is never read.
            self._cloud_object.close()
            return response
        # ClosingIterator closes the object also when the generator never starts:
        # werkzeug sends no body for a HEAD request.
        response.response = ClosingIterator(self._body(), self._cloud_object.close)
        response.direct_passthrough = True
        response.headers["Content-Length"] = str(self.size)
        return response


def _cloud_filestore_store_unusable(error):
    """True when the error says the whole store cannot be used.

    An outage (unreachable, HTTP 5xx) and a store-wide refusal (key, rights,
    bucket) both mean that every file fails, so they alert the admins. Any
    other CloudStoreError is a refusal of one file: it is only logged.
    """
    return isinstance(error, (CloudStoreUnavailable, CloudStoreAccessError))


# The attribute that marks Odoo's base `_file_read` as wrapped by this module.
WRAPPED_MARKER = "_oteny_cloud_filestore_wrapped"


def _cloud_filestore_param(env, key, default=""):
    return env["ir.config_parameter"].sudo().get_param(PARAM_PREFIX + key, default) or default


@functools.cache
def _cloud_filestore_read_write_key(rcfile):
    """Read the write key from the [oteny_cloud_filestore] section of `rcfile` once per process.

    Like the options of [options], a changed key takes effect after a restart.
    """
    parser = configparser.RawConfigParser()
    parser.read([rcfile])
    if not parser.has_section(WRITE_KEY_SECTION):
        return None, None
    section = parser[WRITE_KEY_SECTION]
    return section.get("write_access_key_id") or None, section.get("write_secret_access_key") or None


def _cloud_filestore_write_key():
    """Return (access key id, secret) of the write key in odoo.conf, or (None, None)."""
    rcfile = config.get("config")
    if not rcfile:
        return None, None
    return _cloud_filestore_read_write_key(rcfile)


def cloud_filestore_mode(env):
    """Return "off", "production" or "clone".

    "off": no endpoint or bucket is set, so Odoo behaves as without the
    module. "production": the write key is in odoo.conf and the database
    is not neutralized. Every other case is "clone": a copy of the
    database must never put or delete objects in the bucket, even if the
    write key was copied into its odoo.conf.

    A module-level function, because the wrapped base `_file_read` needs it
    in a registry where this module is not loaded yet (or not installed:
    the parameters do not exist there, so the mode is "off").
    """
    if not (_cloud_filestore_param(env, "endpoint_url") and _cloud_filestore_param(env, "bucket")):
        return "off"
    access_key_id, secret_access_key = _cloud_filestore_write_key()
    has_write_key = bool(access_key_id and secret_access_key)
    is_neutralized = bool(env["ir.config_parameter"].sudo().get_param("database.is_neutralized"))
    if has_write_key and not is_neutralized:
        return "production"
    return "clone"


def cloud_filestore_store(env):
    """Return the cached CloudStore for the current mode; None when off."""
    mode = cloud_filestore_mode(env)
    if mode == "off":
        return None
    if mode == "production":
        access_key_id, secret_access_key = _cloud_filestore_write_key()
    else:
        access_key_id = _cloud_filestore_param(env, "read_access_key_id")
        secret_access_key = _cloud_filestore_param(env, "read_secret_access_key")
    if not (access_key_id and secret_access_key):
        # Empty keys would let boto3 fall back to ambient credentials
        # (environment, ~/.aws, instance profile): never use those.
        _logger.warning("Cloud filestore: no complete access key for mode %s, store disabled", mode)
        return None
    settings = (
        _cloud_filestore_param(env, "endpoint_url"),
        _cloud_filestore_param(env, "region"),
        _cloud_filestore_param(env, "bucket"),
        _cloud_filestore_param(env, "addressing_style", "path"),
        _cloud_filestore_param(env, "key_prefix"),
        access_key_id,
        secret_access_key,
    )
    store = _STORE_CACHE.get(settings)
    if store is None:
        try:
            store = CloudStore(
                endpoint_url=settings[0],
                region=settings[1],
                bucket=settings[2],
                addressing_style=settings[3],
                key_prefix=settings[4],
                access_key_id=access_key_id,
                secret_access_key=secret_access_key,
            )
        except Exception as error:  # noqa: BLE001
            # A bad setting (an endpoint without scheme, an unknown region)
            # makes boto3 raise here. It must never fail a save or a read:
            # without a store, production keeps files local and alerts, and
            # a clone reads empty bytes. Only the type is logged, so no
            # setting value reaches the log.
            _logger.warning(
                "Cloud filestore: store not built for mode %s, store disabled: %s",
                mode,
                type(error).__name__,
            )
            return None
        _STORE_CACHE[settings] = store
    return store


def _original_file_read():
    """Odoo's own `_file_read` of the base class, also after it was wrapped."""
    method = BaseIrAttachment._file_read
    return getattr(method, "__wrapped__", method)


def cloud_filestore_read(attachment_model, fname, size=None):
    """Read like `_file_read`; return (data, store_unusable).

    The local file comes first, through Odoo's own method. Else, in
    production or clone mode, the object comes from the bucket. A bucket
    error never raises: the data is empty bytes, as Odoo returns for a
    missing local file. `store_unusable` is True when the read failed
    because the store cannot be used (outage or store-wide refusal). Empty
    data with False means the object is gone or the store refused this one
    file. In production, an unusable store alerts the admins; a missing
    object or a per-file refusal only logs, because neither is an outage.

    `attachment_model` is an `ir.attachment` recordset of any registry. When
    this module is loaded in that registry, the mode, the store and the alert
    come from the model's methods, so an override (or a test patch) applies.
    Before that (a module update loads the modules one by one), they come
    from the module-level functions, and a failure is only logged: the alert
    model does not exist yet.

    The read cache (D21) also needs the module loaded: every read counts, and
    a full read of a small enough file from the bucket leaves a cache copy in
    the local filestore, so the next read is local. The batch jobs call the
    store themselves and never pass here, so they never fill the cache.
    """
    count_read = getattr(attachment_model, "_cloud_filestore_cache_count", None)
    if os.path.exists(attachment_model._full_path(fname)):
        data = _original_file_read()(attachment_model, fname, size=size)
        if count_read is not None:
            count_read(fname)
        return data, False
    if hasattr(attachment_model, "_cloud_filestore_mode"):
        mode = attachment_model._cloud_filestore_mode()
    else:
        mode = cloud_filestore_mode(attachment_model.env)
    if mode == "off":
        return _original_file_read()(attachment_model, fname, size=size), False
    if hasattr(attachment_model, "_cloud_filestore_store"):
        store = attachment_model._cloud_filestore_store()
    else:
        store = cloud_filestore_store(attachment_model.env)
    if store is None:
        return b"", True
    try:
        data = store.get(fname)
    except CloudStoreError as error:
        _logger.warning("Cloud filestore: read of %s failed: %s", fname, error)
        unusable = _cloud_filestore_store_unusable(error)
        alert = getattr(attachment_model, "_cloud_filestore_alert", None)
        if unusable and mode == "production" and alert is not None:
            alert("_bucket_failed", str(error))
        return b"", unusable
    if data is None:
        if any(attachment.file_size for attachment in attachment_model if attachment.store_fname == fname):
            # A file with content that is neither local nor in the bucket
            # most likely means a wrong bucket or key_prefix: make it
            # visible in the log. It is not an outage, so no alert: an
            # alert here would open and close with every good save.
            _logger.warning("Cloud filestore: %s is not local and not in the bucket", fname)
        return b"", False
    if count_read is not None:
        if size is None:
            # The caller gets the bytes anyway, so the fill writes them from memory; the
            # download route fills from the open object instead (_cloud_filestore_cached_stream).
            attachment_model._cloud_filestore_cache_fill(fname, [data], len(data))
        count_read(fname)
    return (data[:size] if size is not None else data), False


def wrap_base_file_read():
    """Make Odoo's base `_file_read` read from the bucket; once per process.

    The registry builds the `ir.attachment` model class of every database
    with the base class in its MRO, so this one wrapper reaches every
    registry, also while a module update loads the modules before this one.
    A second call (the import of the module by a registry load after the
    server-wide import) changes nothing.
    """
    original = BaseIrAttachment._file_read
    if getattr(original, WRAPPED_MARKER, False):
        return

    @functools.wraps(original)
    def _file_read(self, fname, size=None):
        return cloud_filestore_read(self, fname, size=size)[0]

    setattr(_file_read, WRAPPED_MARKER, True)
    BaseIrAttachment._file_read = _file_read


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
        return _cloud_filestore_param(self.env, key, default)

    @api.model
    def _cloud_filestore_mode(self):
        """Return "off", "production" or "clone": see `cloud_filestore_mode`."""
        return cloud_filestore_mode(self.env)

    @api.model
    def _cloud_filestore_store(self):
        """Return the cached CloudStore for the current mode; None when off."""
        return cloud_filestore_store(self.env)

    def _register_hook(self):
        """Log a warning when a production database runs without the module server-wide.

        Only a server-wide module wraps the base `_file_read` before the first
        module of a registry loads. Without it, a module update reads a file
        that is only in the bucket as empty bytes for every module that loads
        before this one. The hook only logs: every worker sets up its
        registries, so a Discuss alert from here would repeat per worker and
        per restart. The upload job posts the one alert.
        """
        super()._register_hook()
        if self._cloud_filestore_is_server_wide() or self._cloud_filestore_mode() != "production":
            return
        _logger.warning(
            "Cloud filestore: oteny_cloud_filestore is not a server-wide module "
            "(--load / server_wide_modules), so a module update can read files "
            "that are only in the bucket as empty"
        )

    @api.model
    def _cloud_filestore_is_server_wide(self):
        return "oteny_cloud_filestore" in config["server_wide_modules"]

    @api.model
    def _cloud_filestore_check_server_wide(self):
        """Post the not-server-wide alert once, until the server-wide line is back.

        The system parameter `not_server_wide_alerted` holds the time of the
        alert; an absent parameter counts as empty. When the module is
        server-wide again, the parameter is cleared, so a later regression
        alerts again.
        """
        params = self.env["ir.config_parameter"].sudo()
        key = PARAM_PREFIX + "not_server_wide_alerted"
        alerted = params.get_param(key)
        if self._cloud_filestore_is_server_wide():
            if alerted:
                params.set_param(key, False)
            return
        if alerted:
            return
        self._cloud_filestore_alert("_not_server_wide")
        params.set_param(key, fields.Datetime.to_string(fields.Datetime.now()))

    def _set_attachment_data(self, asbytes):
        """A content change of local-only rows must not reach the bucket either."""
        if self and all(self.mapped("cloud_local_only")):
            self = self.with_context(cloud_filestore_local_only=True)
        return super()._set_attachment_data(asbytes)

    def _file_write(self, bin_value, checksum):
        """Write the file locally as Odoo does, then upload it in production.

        The user's save never fails because of the bucket: on a bucket error
        the file stays in the local filestore and the admins get one alert
        per outage. After a confirmed upload the local copy stays as a cache
        copy, or is removed when it is too large for the cache.
        """
        fname = super()._file_write(bin_value, checksum)
        if self._cloud_filestore_mode() != "production":
            return fname
        if self._cloud_filestore_is_local_only():
            # A local-only file never depends on the bucket: a user file with the same content
            # that was marked earlier must not leave its shared local copy to the eviction.
            self._cloud_filestore_cache_clear_mark(fname)
            return fname
        store = self._cloud_filestore_store()
        if store is None:
            # An incomplete key or a bad setting is a failure too, never an ambient-credential fallback.
            _logger.warning("Cloud filestore: no usable store, %s stays local", fname)
            self._cloud_filestore_alert("_bucket_failed", "no usable store settings")
            self._cloud_filestore_cache_clear_mark(fname)
            return fname
        try:
            # The name is the SHA-1 of the content, so an object with this name and
            # size is this file. Asking first spares the bucket a write: Google Cloud
            # Storage limits writes to about one per second per object name.
            if store.head(fname) != len(bin_value):
                store.put(fname, bin_value)
        except CloudStoreError as error:
            if _cloud_filestore_store_unusable(error):
                _logger.warning("Cloud filestore: upload of %s failed, file stays local: %s", fname, error)
                self._cloud_filestore_alert("_bucket_failed", str(error))
                self._cloud_filestore_cache_clear_mark(fname)
                return fname
            # The store refused this one file (for example 400 EntityTooLarge).
            # That is not an outage: no alert, so the next good save does not
            # end an outage that never was. The file stays local.
            _logger.warning("Cloud filestore: upload of %s refused, file stays local: %s", fname, error)
            self._cloud_filestore_cache_clear_mark(fname)
            return fname
        self._cloud_filestore_after_upload(fname, len(bin_value))
        self._cloud_filestore_alert("_bucket_ok")
        return fname

    def _cloud_filestore_after_upload(self, fname, size):
        """Keep or remove the local copy of a file that the bucket confirmed.

        A new file is often read soon, so a file that may enter the read
        cache stays as a cache copy with the "in bucket" mark (write-through,
        D21). A larger file is removed. A name that a local-only file shares
        stays without the mark: the local-only row needs it for good.
        """
        if self._cloud_filestore_local_only_uses(fname):
            return
        if self._cloud_filestore_may_cache(size):
            try:
                # A cache problem must never fail the save: the savepoint keeps the transaction usable.
                with self.env.cr.savepoint():
                    self.env["oteny.cloud.filestore.cache"].sudo()._cloud_filestore_cache_mark(fname, size)
            except Exception as error:  # noqa: BLE001
                # The local copy stays unmarked, so it waits for upload: the upload job marks it later.
                _logger.warning("Cloud filestore: cache mark of %s not written: %s", fname, error)
            return
        try:
            os.unlink(self._full_path(fname))
        except FileNotFoundError:
            pass
        except OSError as error:
            # The upload is confirmed; a local copy that stays is only wasted disk space.
            _logger.warning("Cloud filestore: local copy of %s not removed: %s", fname, error)

    @api.model
    def _cloud_filestore_cache_clear_mark(self, fname):
        """Clear the mark of a name that the bucket did not confirm now; never fails the save.

        A mark means "the bucket holds this file". When the content is saved
        again and the bucket does not confirm it (an outage, a refusal, or the
        object was deleted after its hold time), the local file must stay and
        wait for upload, so it must not look like a cache copy.
        """
        try:
            with self.env.cr.savepoint():
                self.env["oteny.cloud.filestore.cache"].sudo()._cloud_filestore_cache_unmark([fname])
        except Exception as error:  # noqa: BLE001
            _logger.warning("Cloud filestore: cache mark of %s not cleared: %s", fname, error)

    @api.model
    def _cloud_filestore_local_only_uses(self, fname):
        """True when a local-only attachment uses this file name.

        A user file and a local-only file with the same content share one
        name (the content hash). The local copy must stay for the local-only
        row, which never depends on the bucket. One SQL query, so rows that
        the ORM cache does not hold are seen too; pending writes are flushed
        first so a row created a moment ago is not hidden.
        """
        self.flush_model(["store_fname", "cloud_local_only"])
        self.env.cr.execute(
            "SELECT 1 FROM ir_attachment WHERE store_fname = %s AND cloud_local_only IS TRUE LIMIT 1",
            [fname],
        )
        return bool(self.env.cr.fetchone())

    def _cloud_filestore_read(self, fname, size=None):
        """Read like `_file_read`; return (data, store_unusable): see `cloud_filestore_read`."""
        return cloud_filestore_read(self, fname, size=size)

    @api.model
    def _cloud_filestore_cache_limit(self):
        """Return the size limit of the read cache in bytes; a bad value uses the default.

        The parameter is in MB and may have decimals. 0 keeps no cache copies.
        """
        value = self._cloud_filestore_param("cache_limit_mb", str(DEFAULT_CACHE_LIMIT_MB))
        try:
            megabytes = float(value)
        except ValueError:
            megabytes = -1
        if not (math.isfinite(megabytes) and megabytes >= 0):
            if _CACHE_LIMIT_WARNED[0] != value:  # once per bad value, not on every read
                _CACHE_LIMIT_WARNED[0] = value
                _logger.warning(
                    "Cloud filestore: cache_limit_mb %r is not a size in MB, using %s",
                    value,
                    DEFAULT_CACHE_LIMIT_MB,
                )
            megabytes = DEFAULT_CACHE_LIMIT_MB
        return int(megabytes * 1024 * 1024)

    @api.model
    def _cloud_filestore_may_cache(self, size):
        """True when a file of this size may enter the read cache.

        A file larger than a quarter of the limit never enters (D21), so a
        single file cannot empty the cache.
        """
        limit = self._cloud_filestore_cache_limit()
        return limit > 0 and size <= limit / 4

    @api.model
    def _cloud_filestore_cache_fill(self, fname, chunks, size):
        """Write a file read from the bucket into the local filestore as a cache copy, with its mark.

        `chunks` yields the bytes of the object, so a download fills the copy
        without holding the whole file. The bytes go to a temporary file and
        get the final name only when their SHA-1 is the name: a damaged object
        never becomes a cache copy, which the eviction (a size check) could
        not tell from a good one. Return True when this call made the copy.

        The mark is written at once, in a transaction of its own: an unmarked
        local file means "must stay", so a cache copy without its mark would
        never be evicted. When the mark cannot be written, the new copy is
        removed again. A disk error or wrong bytes only log: the read goes on.
        A bucket error while reading `chunks` removes the temporary file and
        reaches the caller. A name that a local-only file uses is never filled
        here: that file must not depend on the bucket.
        """
        # Odoo never stores empty content as a file, so empty bytes are no cache copy.
        if not size or not self._cloud_filestore_may_cache(size):
            return False
        if self._cloud_filestore_local_only_uses(fname):
            return False
        path = self._full_path(fname)
        try:
            # False: another worker wrote the same name (the same content) a moment ago; it marks it.
            if not self._cloud_filestore_write_new_file(path, chunks, check_sha1=True):
                return False
        except OSError as error:
            _logger.warning("Cloud filestore: cache copy of %s not written: %s", fname, error)
            return False
        except ValueError:
            _logger.warning("Cloud filestore: bytes of %s do not match its name, no cache copy", fname)
            return False
        if not self._cloud_filestore_cache_in_own_transaction("_cloud_filestore_cache_mark", fname, size):
            try:
                os.unlink(path)
            except OSError as error:
                _logger.warning("Cloud filestore: unmarked cache copy of %s not removed: %s", fname, error)
            return False
        return True

    @api.model
    def _cloud_filestore_cache_count(self, fname):
        """Count a read of the file in the buffer of this process; write the buffer at most once a minute."""
        if self._cloud_filestore_mode() == "off":
            return
        dbname = self.env.cr.dbname
        now = time.monotonic()
        with _CACHE_LOCK:
            buffer = _CACHE_READS.setdefault(dbname, {})
            buffer[fname] = buffer.get(fname, 0) + 1
            if now - _CACHE_FLUSHED_AT.setdefault(dbname, now) < CACHE_FLUSH_SECONDS:
                return
            _CACHE_FLUSHED_AT[dbname] = now
            counts = _CACHE_READS.pop(dbname)
        self._cloud_filestore_cache_in_own_transaction("_cloud_filestore_cache_flush", counts)

    @api.model
    def _cloud_filestore_cache_in_own_transaction(self, method, *args):
        """Call a write method of the cache model in a transaction of its own, as superuser; True when done.

        The read path may run on a read-only cursor, and a failure here must
        never fail the read: it is logged and the write is dropped. The
        request's own transaction is still open and may hold a lock on the
        same row (a save that marked the name): a wait for it would never end,
        because that transaction waits for this one. The lock timeout turns
        such a wait into a dropped write after one second.
        """
        try:
            with self.env.registry.cursor() as cr:
                cr.execute("SET LOCAL lock_timeout TO '1s'")
                env = api.Environment(cr, SUPERUSER_ID, {"cloud_filestore_quiet_sql": True})
                getattr(env["oteny.cloud.filestore.cache"], method)(*args)
        except Exception as error:  # noqa: BLE001
            _logger.warning("Cloud filestore: cache write %s dropped: %s", method, error)
            return False
        return True

    def copy_data(self, default=None):
        """Refuse to copy a file whose content cannot be read now.

        Odoo copies the content through `raw`, and a store that cannot be
        used reads as empty bytes. Without this check, a duplicated record or
        a forwarded mail would get a valid-looking empty file while the
        bucket is down. A file that is gone for good copies empty, as plain
        Odoo does, so a record with a lost file can still be duplicated.
        """
        if (default or {}).keys() & {"datas", "db_datas", "raw"} or self._cloud_filestore_mode() == "off":
            return super().copy_data(default=default)
        # The content is read once, here, and handed to Odoo as `raw`: a
        # second read to learn why the first was empty could succeed and
        # turn the copy into a valid-looking empty file.
        vals_list = []
        for attachment in self:
            attachment_default = default
            if attachment.store_fname and attachment.file_size:
                data, store_unusable = attachment._cloud_filestore_read(attachment.store_fname)
                if store_unusable:
                    raise UserError(self.env._("The file is temporarily unavailable; try again later."))
                attachment_default = {**(default or {}), "raw": data}
            vals_list += super(IrAttachment, attachment).copy_data(default=attachment_default)
        return vals_list

    def _cloud_filestore_open(self, fname):
        """Open the bucket object for a download; None when it cannot be opened.

        Like the read path: an unusable store alerts the admins in production,
        a lost object or a refusal of one file only logs.
        """
        store = self._cloud_filestore_store()
        if store is None:
            return None
        try:
            cloud_object = store.open(fname)
        except CloudStoreError as error:
            _logger.warning("Cloud filestore: open of %s failed: %s", fname, error)
            if _cloud_filestore_store_unusable(error) and self._cloud_filestore_mode() == "production":
                self._cloud_filestore_alert("_bucket_failed", str(error))
            return None
        if cloud_object is None and self.file_size:
            _logger.warning("Cloud filestore: %s is not local and not in the bucket", fname)
        return cloud_object

    def _to_http_stream(self):
        """Stream a file that exists only in the bucket through Odoo.

        The standard method stats a path in the local filestore and fails for
        a bucket-only file. The browser never sees the bucket (D18). A file
        that may enter the read cache is first downloaded in chunks as a cache copy
        and then sent by Odoo's own path stream, like every local file, so
        `x_sendfile` and range requests work for it. A larger file is sent by
        Odoo in chunks while the object downloads, so the worker never holds
        the whole file in memory.
        """
        self.ensure_one()
        in_use = self.store_fname and self._cloud_filestore_mode() != "off"
        if in_use and not os.path.exists(self._full_path(self.store_fname)):
            cloud_object = self._cloud_filestore_open(self.store_fname)
            size = cloud_object.size if cloud_object is not None else 0
            if size and self._cloud_filestore_may_cache(size):
                stream = self._cloud_filestore_cached_stream(cloud_object)
                if stream is not None:
                    return stream
                # No cache copy (a disk error, wrong bytes, no mark): send the object in chunks.
                cloud_object = self._cloud_filestore_open(self.store_fname)
            if cloud_object is None:
                if self.file_size:
                    # The bucket failed or lost the object. An empty 200 with the real
                    # etag could be cached and stay blank after the bucket recovers.
                    raise NotFound()
                return CloudFilestoreStream(
                    type="data",
                    data=b"",
                    mimetype=self.mimetype,
                    download_name=self.name,
                    etag=self.checksum,
                    public=self.public,
                    last_modified=self.write_date,
                    size=0,
                    location="bucket",
                )
            return CloudObjectStream(
                cloud_object,
                type="data",
                mimetype=self.mimetype,
                download_name=self.name,
                etag=self.checksum,
                public=self.public,
                last_modified=self.write_date,
                size=cloud_object.size,
                store_fname=self.store_fname,
            )
        stream = super()._to_http_stream()
        if in_use and stream.type == "path":
            self._cloud_filestore_cache_count(self.store_fname)
            return CloudFilestoreStream(**vars(stream), location="local")
        return stream

    def _cloud_filestore_cached_stream(self, cloud_object):
        """Download an open bucket object into the read cache, then let Odoo send the cache copy.

        The object goes to disk in chunks, so the worker never holds the whole
        file. The read is counted. Return None when no cache copy exists after
        the fill (a disk error, wrong bytes, no mark): the caller then sends
        the object in chunks; the header says the bytes came from the bucket
        in both cases. A bucket error while downloading gives 404, as for a
        lost object: an empty 200 with the real etag could be cached.
        """
        try:
            self._cloud_filestore_cache_fill(self.store_fname, cloud_object.iter_chunks(), cloud_object.size)
        except CloudStoreError as error:
            _logger.warning(
                "Cloud filestore: download of %s into the cache failed: %s", self.store_fname, error
            )
            if _cloud_filestore_store_unusable(error) and self._cloud_filestore_mode() == "production":
                self._cloud_filestore_alert("_bucket_failed", str(error))
            raise NotFound() from error
        finally:
            cloud_object.close()
        self._cloud_filestore_cache_count(self.store_fname)
        # The copy is there also when another worker filled the same name a moment ago.
        if os.path.exists(self._full_path(self.store_fname)):
            return CloudFilestoreStream(**vars(super()._to_http_stream()), location="bucket")
        return None

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
        if days < MIN_HOLD_DAYS:
            _logger.warning(
                "Cloud filestore: hold_days %s is below the minimum, using %s", days, MIN_HOLD_DAYS
            )
            return MIN_HOLD_DAYS
        return days

    def _gc_file_store_unsafe(self):
        """Garbage-collect the local filestore; in production this makes no bucket call.

        The checklist walk and the whitelist query are reproduced from Odoo,
        because Odoo's version would remove a marker once the file is gone.
        In production the marker of an unreferenced name stays (young or
        old): it is the master list of pending bucket deletes, which the
        separate job `_cloud_filestore_delete_expired` works off after the
        hold time, in short locked batches. Odoo holds its lock for this
        whole run, and a bucket delete takes milliseconds, not microseconds,
        so no delete may happen here. A restore sets every marker to the
        restore date, and a lost checklist means no bucket delete at all, so
        both failures are safe.
        """
        if self._cloud_filestore_mode() != "production":
            return super()._gc_file_store_unsafe()

        checklist = {}
        for dirpath, _dirnames, filenames in os.walk(self._full_path("checklist")):
            dirname = os.path.basename(dirpath)
            for filename in filenames:
                checklist["%s/%s" % (dirname, filename)] = os.path.join(dirpath, filename)

        local_removed = kept = 0
        for names in split_every(self.env.cr.IN_MAX, checklist):
            self.env.cr.execute("SELECT store_fname FROM ir_attachment WHERE store_fname IN %s", [names])
            whitelist = set(row[0] for row in self.env.cr.fetchall())

            for fname in names:
                if fname not in whitelist:
                    try:
                        os.unlink(self._full_path(fname))
                        local_removed += 1
                    except FileNotFoundError:
                        pass
                    except OSError as error:
                        _logger.warning("Cloud filestore: local file not removed: %s", error)
                    kept += 1
                    continue
                try:
                    os.unlink(checklist[fname])
                except OSError:
                    pass
        _logger.info(
            "Cloud filestore gc: %d checked, %d local removed, %d markers kept for the delete job",
            len(checklist),
            local_removed,
            kept,
        )

    @api.model
    def _cloud_filestore_delete_expired(self):
        """Delete the bucket objects whose marker is older than the hold time; return how many.

        Odoo's own garbage collection must not do this: it holds a lock on
        ir_attachment that stops every attachment save, sized for local
        removals. The lock is also what makes a delete safe. The name is the
        SHA-1 of the content, and a save of the same content writes a fresh
        marker, finds the object with `head`, puts nothing and removes the
        local copy: the bucket object is then the only copy of a live file.
        So the job keeps Odoo's protection (the same SHARE lock with the same
        10 s timeout, the same whitelist check) but holds it for one batch of
        up to 1000 names at a time. In a cron run each batch commits, which
        releases the lock; a lock that is not free in time ends the run, and
        the next day's run tries again. Outside a cron nothing is committed.
        """
        if self._cloud_filestore_mode() != "production":
            return 0
        store = self._cloud_filestore_store()
        if store is None:
            return 0
        hold_seconds = self._cloud_filestore_hold_days() * SECONDS_PER_DAY
        is_cron = bool(self.env.context.get("cron_id"))
        checklist_path = self._full_path("checklist")
        markers = {}
        for dirpath, _dirnames, filenames in os.walk(checklist_path):
            dirname = os.path.basename(dirpath)
            for filename in filenames:
                markers["%s/%s" % (dirname, filename)] = os.path.join(dirpath, filename)

        def is_expired(fname):
            try:
                return time.time() - os.path.getmtime(markers[fname]) > hold_seconds
            except OSError:
                return False

        candidates = sorted(fname for fname in markers if is_expired(fname))
        deleted = 0
        for start in range(0, len(candidates), DELETE_MANY_MAX):
            batch = candidates[start : start + DELETE_MANY_MAX]
            remaining = len(candidates) - start - len(batch)
            if is_cron:
                # The LOCK must be the first statement of its transaction (see Odoo's _gc_file_store):
                # else the snapshot may miss an attachment that a concurrent save has just created.
                self.env.cr.commit()
            self.env.cr.execute("SET LOCAL lock_timeout TO '10s'")
            try:
                self.env.cr.execute("LOCK ir_attachment IN SHARE MODE")
            except psycopg2.errors.LockNotAvailable:
                self.env.cr.rollback()
                _logger.warning("Cloud filestore delete job: ir_attachment is locked, try again later")
                break
            self.env.cr.execute(
                "SELECT store_fname FROM ir_attachment WHERE store_fname IN %s", [tuple(batch)]
            )
            whitelist = set(row[0] for row in self.env.cr.fetchall())
            to_delete = []
            for fname in batch:
                if fname in whitelist:
                    # Saved again since the garbage collection: the object is live, only the marker goes.
                    self._cloud_filestore_remove_marker(markers[fname])
                # A save of the same content refreshes the marker: re-check its age under the lock.
                elif is_expired(fname):
                    to_delete.append(fname)
            failed = False
            batch_deleted = 0
            if to_delete:
                try:
                    not_deleted = set(store.delete_many(to_delete))
                except CloudStoreError as error:
                    # After a timeout the server may have deleted the objects already; the cache rows keep
                    # their mark until the next run, which is harmless: nothing reads an unreferenced name.
                    failed = True
                    _logger.warning("Cloud filestore: bucket delete failed, markers kept: %s", error)
                    self._cloud_filestore_alert("_bucket_failed", str(error))
                else:
                    if not_deleted:
                        _logger.warning(
                            "Cloud filestore: the bucket did not delete %d objects, markers kept",
                            len(not_deleted),
                        )
                    gone = [fname for fname in to_delete if fname not in not_deleted]
                    for fname in gone:
                        self._cloud_filestore_remove_marker(markers[fname])
                    # The object is gone, so a cache row marked "in bucket" would be false.
                    self.env["oteny.cloud.filestore.cache"].sudo().search([("name", "in", gone)]).unlink()
                    batch_deleted = len(gone)
                    deleted += batch_deleted
            if is_cron:
                # After a failure no work is left for an immediate call; the commit releases the lock.
                time_left = self.env["ir.cron"]._commit_progress(
                    batch_deleted, remaining=0 if failed else remaining
                )
                # Out of time: `remaining` makes Odoo call the job again at once.
                if time_left <= 0:
                    break
            if failed:
                break
        _logger.info(
            "Cloud filestore delete job: %d objects deleted, %d markers older than the hold time",
            deleted,
            len(candidates),
        )
        return deleted

    @staticmethod
    def _cloud_filestore_remove_marker(path):
        try:
            os.unlink(path)
        except OSError:
            pass

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
    def _cloud_filestore_pending_names(self, names):
        """Return the names among `names` that a user file uses (not only local-only rows).

        A cache copy (a name with the "in bucket" mark) is local but in the
        bucket already, so it is not pending: else every sweep would ask the
        bucket for every cache copy again.
        """
        self.env["oteny.cloud.filestore.cache"].flush_model(["name", "in_bucket"])
        # IS NOT TRUE, because rows that existed before the install can hold NULL.
        self.env.cr.execute(
            "SELECT DISTINCT attachment.store_fname FROM ir_attachment attachment"
            " WHERE attachment.store_fname IN %s AND attachment.cloud_local_only IS NOT TRUE"
            " AND NOT EXISTS (SELECT 1 FROM oteny_cloud_filestore_cache cache"
            " WHERE cache.name = attachment.store_fname AND cache.in_bucket)",
            [tuple(names)],
        )
        return {row[0] for row in self.env.cr.fetchall()}

    @api.model
    def _cloud_filestore_upload_pending(self, limit=500):
        """Move local user files to the bucket; return the number of confirmed uploads.

        This one job covers the first migration of an existing database and
        the catch-up after a bucket outage. A file is pending when at least
        one attachment that is not local-only uses its name and it is not a
        cache copy. The local copy is removed (or kept as a cache copy) only
        after the bucket reports the same size, so a lost or short object
        never costs the only copy. A run stops at the first
        outage error, because the next file would most likely fail as well.
        A file that the bucket refuses is skipped, so it cannot block every
        later run. A call also stops after UPLOAD_CALL_BUDGET_SECONDS. In a
        cron run it reports its progress to the cron, which calls the job
        again at once while files are left; `limit` caps the uploads of one
        call.

        A sweep walks the sorted local names once, over as many calls as it
        needs: each call continues after the last name that the previous
        call reached (_UPLOAD_RESUME_AFTER). `remaining` counts the pending
        names of the sweep that no call has reached yet. A call that reaches
        the end of the names ends the sweep and reports 0; the next sweep
        starts at the first name, so a file that was kept local, or a new
        file that sorts before the point, is tried again then.

        An object that is in the bucket already with the same size is not
        put again and does not count as an upload, nor towards `limit`: a
        name that a user file shares with a local-only file keeps its local
        copy, so every sweep asks the bucket for it again (one head, no put).

        The job also posts the alert when the module is not server-wide.
        """
        if self._cloud_filestore_mode() != "production":
            return 0
        self._cloud_filestore_check_server_wide()
        store = self._cloud_filestore_store()
        if store is None:
            return 0
        started = time.monotonic()
        uploaded = already = not_confirmed = refused = 0
        failed = out_of_time = False
        stop_index = None
        names = self._cloud_filestore_local_names()
        dbname = self.env.cr.dbname
        resume_after = _UPLOAD_RESUME_AFTER.get(dbname)
        # The first name after the point; a name that is gone just moves the start on.
        first_index = bisect.bisect_right(names, resume_after) if resume_after is not None else 0
        for start in range(first_index, len(names), self.env.cr.IN_MAX):
            if failed or stop_index is not None:
                break
            batch = names[start : start + self.env.cr.IN_MAX]
            pending = self._cloud_filestore_pending_names(batch)
            for offset, name in enumerate(batch):
                if name not in pending:
                    continue
                if uploaded >= limit:
                    stop_index = start + offset
                    break
                if time.monotonic() - started > UPLOAD_CALL_BUDGET_SECONDS:
                    out_of_time = True
                    stop_index = start + offset
                    break
                path = self._full_path(name)
                try:
                    with open(path, "rb") as local_file:
                        data = local_file.read()
                except OSError as error:
                    _logger.warning("Cloud filestore: local file %s not read: %s", name, error)
                    continue
                is_put = False
                try:
                    # Ask first: an object that is there already needs no write.
                    size = store.head(name)
                    if size != len(data):
                        store.put(name, data)
                        is_put = True
                        size = store.head(name)
                except CloudStoreError as error:
                    if _cloud_filestore_store_unusable(error):
                        _logger.warning("Cloud filestore: upload job stopped at %s: %s", name, error)
                        self._cloud_filestore_alert("_bucket_failed", str(error))
                        failed = True
                        break
                    # A refusal of one file (for example too large) is not an outage.
                    refused += 1
                    _logger.warning(
                        "Cloud filestore: upload of %s refused, file stays local: %s", name, error
                    )
                    continue
                if size != len(data):
                    # None can also mean a missing bucket: the local file is the only safe copy.
                    not_confirmed += 1
                    _logger.warning("Cloud filestore: upload of %s not confirmed, file stays local", name)
                    continue
                self._cloud_filestore_after_upload(name, size)
                if is_put:
                    uploaded += 1
                else:
                    already += 1
        remaining = 0
        if failed:
            # The point stays as it is: the next run continues the sweep.
            pass
        elif stop_index is None:
            # The sweep reached the end of the names: the next one starts at the first name.
            _UPLOAD_RESUME_AFTER.pop(dbname, None)
        else:
            if stop_index > first_index:
                _UPLOAD_RESUME_AFTER[dbname] = names[stop_index - 1]
            # Only names no call of this sweep reached: files kept local on purpose never count.
            for start in range(stop_index, len(names), self.env.cr.IN_MAX):
                batch = names[start : start + self.env.cr.IN_MAX]
                remaining += len(self._cloud_filestore_pending_names(batch))
        _logger.info(
            "Cloud filestore upload job: %d uploaded, %d already in the bucket, %d not confirmed, "
            "%d refused, %d files checked, %d left for the next call%s%s",
            uploaded,
            already,
            not_confirmed,
            refused,
            len(names),
            remaining,
            ", stopped by a bucket error" if failed else "",
            ", stopped by the time budget" if out_of_time else "",
        )
        if (uploaded or already) and not failed:
            # After a failed run the outage stays open: the bucket is not working.
            self._cloud_filestore_alert("_bucket_ok")
        if self.env.context.get("cron_id"):
            # Work left makes Odoo call the job again at once; 0 lets the cron wait its interval.
            self.env["ir.cron"]._commit_progress(uploaded + already, remaining=remaining)
        return uploaded

    @api.model
    def _cloud_filestore_write_new_file(self, path, data, check_sha1=False):
        """Write a file complete or not at all; return False when the file exists already.

        The bytes go to a temporary file in the same folder first, and the
        final name appears only through a hard link, so a crash or a full
        disk never leaves a partial file under a content-hash name, and an
        existing file is never overwritten. The temporary file is created
        with mode 0o666 like Odoo's own open(), so the process umask gives
        the file the same rights as a file that Odoo writes (mkstemp would
        give 0o600, which a web server under another user cannot read).

        `data` is bytes or an iterable of chunks. With `check_sha1`, the
        SHA-1 of the bytes must be the last part of the name (a content
        hash), else nothing is written and ValueError is raised.
        """
        folder = os.path.dirname(path)
        os.makedirs(folder, exist_ok=True)
        temp_path = os.path.join(folder, f".cfs-{uuid4().hex}")
        file_descriptor = os.open(temp_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o666)
        digest = hashlib.sha1()
        try:
            with os.fdopen(file_descriptor, "wb") as temp_file:
                for chunk in [data] if isinstance(data, bytes) else data:
                    digest.update(chunk)
                    temp_file.write(chunk)
                temp_file.flush()
                os.fsync(temp_file.fileno())
            if check_sha1 and digest.hexdigest() != os.path.basename(path):
                raise ValueError(f"the bytes of {path} do not match its name")
            try:
                os.link(temp_path, path)
            except FileExistsError:
                return False
            return True
        finally:
            try:
                os.unlink(temp_path)
            except OSError:
                pass

    @api.model
    def _cloud_filestore_download_all(self, limit=500):
        """Bring files from the bucket back to the local filestore; return the number written.

        This is the reverse migration, started by hand. It never overwrites a
        local file, and it stops at the first bucket error. A run with a
        limit can be repeated: files that are local already are skipped.
        It clears the mark of every local file it found or wrote, so the
        cache never evicts a file that the database keeps for good (D2).
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
        stopped = False
        # The local files that this run found or wrote must stay: none of them is a cache copy any more.
        local_names = []
        for name in names:
            if written >= limit:
                break
            path = self._full_path(name)
            if os.path.exists(path):
                local_names.append(name)
                continue
            try:
                data = store.get(name)
            except CloudStoreError as error:
                _logger.warning("Cloud filestore: download job stopped at %s: %s", name, error)
                stopped = True
                break
            if data is None:
                missing += 1
                _logger.warning("Cloud filestore: %s is not in the bucket, skipped", name)
                continue
            if hashlib.sha1(data).hexdigest() != name.split("/")[-1]:
                # Names are content hashes: other bytes mean a damaged object.
                missing += 1
                _logger.warning("Cloud filestore: bytes of %s do not match its name, skipped", name)
                continue
            try:
                if not self._cloud_filestore_write_new_file(path, data):
                    local_names.append(name)
                    continue
            except OSError as error:
                # A local disk error (full disk, rights) ends the run as a bucket error does.
                _logger.warning(
                    "Cloud filestore: download job stopped at %s, local file not written: %s", name, error
                )
                stopped = True
                break
            local_names.append(name)
            written += 1
        self.env["oteny.cloud.filestore.cache"].sudo()._cloud_filestore_cache_unmark(local_names)
        _logger.info(
            "Cloud filestore download job: %d written, %d missing in the bucket, %d names checked%s",
            written,
            missing,
            len(names),
            ", stopped by an error" if stopped else "",
        )
        return written

    def _cloud_filestore_alert(self, method, *args):
        """Call an alert method; a failing alert never fails the user's save."""
        try:
            getattr(self.env["oteny.cloud.filestore.alert"], method)(*args)
        except Exception as error:  # noqa: BLE001
            _logger.warning("Cloud filestore: alert %s failed: %s", method, type(error).__name__)
