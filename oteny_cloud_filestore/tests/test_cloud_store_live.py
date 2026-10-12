import time
import unittest
from uuid import uuid4

from odoo.tests import BaseCase, tagged

from ..tools.cloud_store import CloudStore, CloudStoreError, CloudStoreUnavailable
from .common import live_store_config


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudStoreLive(BaseCase):
    """Talks to the live test bucket; skipped when no test INI is configured."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = live_store_config()
        if cls.config is None:
            raise unittest.SkipTest("OTENY_CLOUD_FILESTORE_TEST_CONFIG is not set")
        # Each run writes only under its own prefix.
        cls.key_prefix = f"test-{uuid4().hex}/"
        cls.put_names = set()
        cls.writer = cls._store("write")
        cls.reader = cls._store("read")

    @classmethod
    def _store(cls, kind):
        return CloudStore(
            endpoint_url=cls.config["endpoint_url"],
            region=cls.config["region"],
            bucket=cls.config["bucket"],
            access_key_id=cls.config[f"{kind}_access_key_id"],
            secret_access_key=cls.config[f"{kind}_secret_access_key"],
            key_prefix=cls.key_prefix,
        )

    @classmethod
    def tearDownClass(cls):
        if getattr(cls, "config", None) is not None:
            for name in cls.put_names:
                cls.writer.delete(name)
        super().tearDownClass()

    def _put(self, name, data):
        self.put_names.add(name)
        self.writer.put(name, data)

    def test_write_key_roundtrip(self):
        self._put("ab/abc", b"hello")
        self.assertEqual(self.writer.head("ab/abc"), 5)
        self.assertEqual(self.writer.get("ab/abc"), b"hello")
        self.writer.delete("ab/abc")
        self.assertIsNone(self.writer.get("ab/abc"))
        self.assertIsNone(self.writer.head("ab/abc"))

    def test_delete_missing_is_silent(self):
        self.writer.delete("ab/never-written")

    def test_read_key_reads_but_cannot_write(self):
        self._put("cd/cde", b"data")
        self.assertEqual(self.reader.get("cd/cde"), b"data")
        self.assertEqual(self.reader.head("cd/cde"), 4)
        self.put_names.add("cd/refused")
        with self.assertRaises(CloudStoreError) as put_error:
            self.reader.put("cd/refused", b"x")
        self.assertNotIsInstance(put_error.exception, CloudStoreUnavailable)
        with self.assertRaises(CloudStoreError) as delete_error:
            self.reader.delete("cd/cde")
        self.assertNotIsInstance(delete_error.exception, CloudStoreUnavailable)

    def test_unreachable_endpoint_is_unavailable(self):
        store = CloudStore(
            endpoint_url="http://127.0.0.1:9",
            region="garage",
            bucket="none",
            access_key_id="x",
            secret_access_key="x",
        )
        started = time.monotonic()
        with self.assertRaises(CloudStoreUnavailable):
            store.head("ab/abc")
        self.assertLess(time.monotonic() - started, 15)
