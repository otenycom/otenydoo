import hashlib
import os
import time
import unittest
from uuid import uuid4

from odoo.tests import BaseCase, tagged

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from ..tools.cloud_store import CloudStore, CloudStoreError, CloudStoreUnavailable
from .common import live_run_prefix, live_store_config


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudStoreLive(BaseCase):
    """Talks to the live test bucket; skipped when no test INI is configured."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.config = live_store_config()
        if cls.config is None:
            raise unittest.SkipTest("OTENY_CLOUD_FILESTORE_TEST_CONFIG is not set")
        # Each run writes only under its own prefix, inside the folder the
        # test keys are limited to (the INI's optional key_prefix).
        cls.key_prefix = live_run_prefix(cls.config)
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

    def test_delete_many_deletes_both_objects(self):
        self._put("ab/many1", b"one")
        self._put("ab/many2", b"two")
        self.assertEqual(self.writer.delete_many(["ab/many1", "ab/many2", "ab/never-written"]), [])
        self.assertIsNone(self.writer.head("ab/many1"))
        self.assertIsNone(self.writer.head("ab/many2"))
        with self.assertRaises(ValueError):
            self.writer.delete_many(["ab/%d" % number for number in range(1001)])

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

    def test_read_key_cannot_list(self):
        # D27: the key every clone carries reads a file whose name it knows,
        # but it cannot list the bucket, so a leaked database cannot
        # enumerate or bulk-download the files. The module never lists, so
        # this calls the S3 API directly.
        self._put("ef/efg", b"listed?")
        client = boto3.session.Session().client(
            "s3",
            endpoint_url=self.config["endpoint_url"],
            region_name=self.config["region"],
            aws_access_key_id=self.config["read_access_key_id"],
            aws_secret_access_key=self.config["read_secret_access_key"],
            config=Config(s3={"addressing_style": "path"}, signature_version="s3v4"),
        )
        with self.assertRaises(ClientError) as caught:
            client.list_objects_v2(Bucket=self.config["bucket"], Prefix=self.key_prefix)
        self.assertEqual(caught.exception.response["ResponseMetadata"]["HTTPStatusCode"], 403)

    def test_write_key_stays_in_its_folder(self):
        # A test key limited to one folder (for example dev/) must be refused
        # outside it, so it can never touch the production folder (main/).
        folder = self.config.get("key_prefix", "")
        if not folder:
            self.skipTest("the test INI sets no key_prefix folder")
        outside = CloudStore(
            endpoint_url=self.config["endpoint_url"],
            region=self.config["region"],
            bucket=self.config["bucket"],
            access_key_id=self.config["write_access_key_id"],
            secret_access_key=self.config["write_secret_access_key"],
            key_prefix=f"outside-{folder.strip('/')}-test-{uuid4().hex}/",
        )
        with self.assertRaises(CloudStoreError) as caught:
            outside.put("ab/abc", b"x")
        self.assertNotIsInstance(caught.exception, CloudStoreUnavailable)

    def test_missing_bucket_put_raises(self):
        store = CloudStore(
            endpoint_url=self.config["endpoint_url"],
            region=self.config["region"],
            bucket=f"no-such-bucket-{uuid4().hex[:12]}",
            access_key_id=self.config["write_access_key_id"],
            secret_access_key=self.config["write_secret_access_key"],
        )
        with self.assertRaises(CloudStoreError) as caught:
            store.put("ab/abc", b"x")
        self.assertNotIsInstance(caught.exception, CloudStoreUnavailable)

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

    def test_large_object_streams_in_small_chunks(self):
        """About 30 MB (the largest production file is 29 MB) arrive in chunks of at most 64 KiB."""
        data = os.urandom(30 * 1024 * 1024)
        self._put("ab/large", data)
        opened = self.reader.open("ab/large")
        self.assertEqual(opened.size, len(data))
        digest = hashlib.sha1()
        biggest = 0
        try:
            for chunk in opened.iter_chunks():
                biggest = max(biggest, len(chunk))
                digest.update(chunk)
        finally:
            opened.close()
        self.assertLessEqual(biggest, 65536)
        self.assertEqual(digest.hexdigest(), hashlib.sha1(data).hexdigest())
        self.assertIsNone(self.reader.open("ab/missing"))
