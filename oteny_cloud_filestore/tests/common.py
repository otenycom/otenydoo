import configparser
import os
from unittest.mock import MagicMock, patch
from uuid import uuid4

from odoo.tests import HttpCase

from ..models import ir_attachment as ir_attachment_module
from ..tools.cloud_store import CloudStoreUnavailable

CONFIG_VARIABLE = "OTENY_CLOUD_FILESTORE_TEST_CONFIG"


class FakeCloudObject:
    """In-memory stand-in for CloudObject; records how much was read."""

    def __init__(self, data, fail_after_chunks=None):
        self.size = len(data)
        self._data = data
        self._fail_after_chunks = fail_after_chunks
        self.chunks_read = 0
        self.closed = False

    def iter_chunks(self, chunk_size=65536):
        for start in range(0, len(self._data), chunk_size):
            if self._fail_after_chunks is not None and self.chunks_read >= self._fail_after_chunks:
                raise CloudStoreUnavailable("fake body failed")
            self.chunks_read += 1
            yield self._data[start : start + chunk_size]

    def close(self):
        self.closed = True


class FakeCloudStore:
    """In-memory stand-in for CloudStore, with the same four methods.

    Tests set `down` to simulate an outage, and read `puts` and `deletes`
    to see which names the code under test sent to the store.
    """

    def __init__(self):
        self.objects: dict[str, bytes] = {}
        self.down = False
        self.puts: list[str] = []
        self.deletes: list[str] = []
        self.fail_after_chunks = None
        self.opened: list = []
        self.delete_many_calls: list[list[str]] = []
        # Names that delete_many reports as not deleted, like the bucket's `Errors`.
        self.fail_delete_names: set[str] = set()

    def _check_up(self):
        if self.down:
            raise CloudStoreUnavailable("fake store is down")

    def put(self, name, data):
        self._check_up()
        self.puts.append(name)
        self.objects[name] = data

    def get(self, name):
        self._check_up()
        return self.objects.get(name)

    def open(self, name):
        """Return a FakeCloudObject, or None for a missing key.

        `fail_after_chunks` makes the body raise CloudStoreUnavailable after
        that many chunks; `opened` records the objects, to see what was read.
        """
        self._check_up()
        data = self.objects.get(name)
        if data is None:
            return None
        fake_object = FakeCloudObject(data, self.fail_after_chunks)
        self.opened.append(fake_object)
        return fake_object

    def head(self, name):
        self._check_up()
        data = self.objects.get(name)
        return None if data is None else len(data)

    def delete(self, name):
        self._check_up()
        self.deletes.append(name)
        self.objects.pop(name, None)

    def delete_many(self, names):
        """Delete the names in one call; return the names listed in `fail_delete_names` (kept)."""
        self._check_up()
        if len(names) > 1000:
            raise ValueError("delete_many takes at most 1000 names")
        self.delete_many_calls.append(list(names))
        failed = []
        for name in names:
            if name in self.fail_delete_names:
                failed.append(name)
                continue
            self.deletes.append(name)
            self.objects.pop(name, None)
        return failed


def patch_read_cache(test_case, limit_mb="0"):
    """Set the read cache limit for one test and keep the cache writes in the test transaction.

    The default limit 0 keeps no cache copies, so a confirmed upload removes
    the local file as before the cache existed. The read path writes the
    cache table in a cursor of its own and commits; here the write uses the
    test's own cursor instead, so nothing is committed and the test sees the
    rows. An HTTP test only records the write: a read-only request of a test
    cannot write, neither through a cursor of its own nor through the test
    cursor (Odoo then retries the whole request). The returned mock records
    each call (its first argument is the model). The buffer of read counts
    starts empty.
    """
    is_http = isinstance(test_case, HttpCase)

    def write_in_test_cursor(attachment_model, method, *args):
        if not is_http:
            getattr(test_case.env["oteny.cloud.filestore.cache"].sudo(), method)(*args)
        return True

    recorder = MagicMock(side_effect=write_in_test_cursor)

    def in_own_transaction(attachment_model, method, *args):
        # A plain function, so the model arrives as `self`; the mock records the call.
        return recorder(attachment_model, method, *args)

    test_case.env["ir.config_parameter"].sudo().set_param("oteny_cloud_filestore.cache_limit_mb", limit_mb)
    model = type(test_case.env["ir.attachment"])
    test_case.startPatcher(patch.dict(ir_attachment_module._CACHE_READS, clear=True))
    test_case.startPatcher(patch.dict(ir_attachment_module._CACHE_FLUSHED_AT, clear=True))
    test_case.startPatcher(
        patch.object(model, "_cloud_filestore_cache_in_own_transaction", in_own_transaction)
    )
    return recorder


def live_store_config():
    """Read the live test store settings from the INI file named by the
    environment variable; None when the variable or the file is missing."""
    path = os.environ.get(CONFIG_VARIABLE)
    if not path:
        return None
    path = os.path.expanduser(path)
    if not os.path.isfile(path):
        return None
    parser = configparser.ConfigParser()
    parser.read(path)
    if not parser.has_section("s3"):
        return None
    return dict(parser["s3"])


def live_run_prefix(config):
    """The prefix one live test run writes under.

    The optional `key_prefix` of the INI is the folder the test keys may
    write in (for example `dev/` of a bucket whose `main/` holds production
    files); each run adds its own `test-<uuid>/` below it, so runs never meet.
    """
    return f"{config.get('key_prefix', '')}test-{uuid4().hex}/"
