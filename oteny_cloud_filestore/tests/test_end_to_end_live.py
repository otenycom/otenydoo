import os
import unittest
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import config

from ..models import ir_attachment as ir_attachment_module
from ..tools.cloud_store import CloudStore
from .common import live_run_prefix, live_store_config, patch_read_cache


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestEndToEndLive(TransactionCase):
    """The whole module against the live test bucket, with the real CloudStore.

    Skipped when no test INI is configured. Every object is written under a
    prefix of its own and deleted again at the end of the test.
    """

    def setUp(self):
        super().setUp()
        live = live_store_config()
        if live is None:
            raise unittest.SkipTest("OTENY_CLOUD_FILESTORE_TEST_CONFIG is not set")
        self.live = live
        self.key_prefix = live_run_prefix(live)
        self.created_names = set()
        # The store cache is module-level: start clean and leave it clean.
        ir_attachment_module._STORE_CACHE.clear()
        self.addCleanup(ir_attachment_module._STORE_CACHE.clear)

        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_cloud_filestore.endpoint_url", live["endpoint_url"])
        params.set_param("oteny_cloud_filestore.region", live["region"])
        params.set_param("oteny_cloud_filestore.bucket", live["bucket"])
        params.set_param("oteny_cloud_filestore.key_prefix", self.key_prefix)
        params.set_param("oteny_cloud_filestore.read_access_key_id", live["read_access_key_id"])
        params.set_param("oteny_cloud_filestore.read_secret_access_key", live["read_secret_access_key"])
        params.set_param("database.is_neutralized", "")
        patch_read_cache(self)

        # A store with the write key, only to look into the bucket and clean up.
        self.writer = CloudStore(
            endpoint_url=live["endpoint_url"],
            region=live["region"],
            bucket=live["bucket"],
            access_key_id=live["write_access_key_id"],
            secret_access_key=live["write_secret_access_key"],
            key_prefix=self.key_prefix,
        )
        self.addCleanup(self._delete_created_objects)

        self.write_options = {
            "cloud_filestore_write_access_key_id": live["write_access_key_id"],
            "cloud_filestore_write_secret_access_key": live["write_secret_access_key"],
        }
        patcher = patch.dict(config.options, self.write_options)
        patcher.start()
        self.addCleanup(patcher.stop)

    def _delete_created_objects(self):
        for name in self.created_names:
            self.writer.delete(name)

    def _create_attachment(self):
        # Random content, so the name (the content hash) is new on every run.
        content = os.urandom(64)
        attachment = self.env["ir.attachment"].create({"name": "live.bin", "raw": content})
        self.created_names.add(attachment.store_fname)
        return attachment, content

    def test_production_roundtrip(self):
        self.assertEqual(self.env["ir.attachment"]._cloud_filestore_mode(), "production")
        attachment, content = self._create_attachment()
        name = attachment.store_fname

        self.assertEqual(self.writer.head(name), len(content), "the object is in the bucket")
        self.assertFalse(os.path.exists(attachment._full_path(name)), "the local file is gone")
        attachment.invalidate_recordset(["raw"])
        self.assertEqual(attachment.raw, content)

    def test_clone_reads_with_read_key(self):
        attachment, content = self._create_attachment()

        # Switch to a clone: the write options leave odoo.conf.
        for option in self.write_options:
            config.options.pop(option, None)
        self.assertEqual(self.env["ir.attachment"]._cloud_filestore_mode(), "clone")
        attachment.invalidate_recordset(["raw"])
        self.assertEqual(attachment.raw, content, "a clone reads the object with the read key")

        # A new file on a clone stays local and never reaches the bucket.
        local_attachment = self.env["ir.attachment"].create({"name": "clone.bin", "raw": os.urandom(64)})
        local_name = local_attachment.store_fname
        self.assertTrue(os.path.exists(local_attachment._full_path(local_name)))
        self.assertIsNone(self.writer.head(local_name))
        self.addCleanup(self._remove_local_file, local_attachment._full_path(local_name))

    def _remove_local_file(self, path):
        try:
            os.unlink(path)
        except OSError:
            pass
