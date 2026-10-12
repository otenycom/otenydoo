import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged

from .common import FakeCloudStore


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreWrite(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.attachment = cls.env["ir.attachment"]
        cls.channel = cls.env["discuss.channel"].create({"name": "Cloud filestore alerts"})
        cls.params = cls.env["ir.config_parameter"].sudo()
        cls.params.set_param("oteny_cloud_filestore.alert_channel_id", str(cls.channel.id))
        cls.params.set_param("oteny_cloud_filestore.outage_since", "")

    def setUp(self):
        super().setUp()
        # The separate cursor of the alert shares the test transaction.
        self.env.flush_all()
        self.registry_enter_test_mode(register_cleanup=True)
        # Never touch the real filestore of the test database.
        filestore = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, filestore, ignore_errors=True)
        model = type(self.attachment)
        self.startPatcher(patch.object(model, "_filestore", return_value=filestore))
        self.fake = FakeCloudStore()
        self.mode = "production"
        self.startPatcher(patch.object(model, "_cloud_filestore_mode", side_effect=lambda: self.mode))
        self.startPatcher(patch.object(model, "_cloud_filestore_store", return_value=self.fake))
        self.startPatcher(patch.object(model, "_storage", return_value="file"))

    def _save(self, raw, **context):
        return self.attachment.with_context(**context).create({"name": "user.bin", "raw": raw})

    def _local_exists(self, attachment):
        return os.path.exists(attachment._full_path(attachment.store_fname))

    def _alert_messages(self):
        self.channel.invalidate_recordset()
        return self.channel.message_ids.filtered(lambda m: m.message_type == "comment")

    def test_write_uploads_and_removes_local(self):
        attachment = self._save(b"user file")
        self.assertEqual(self.fake.objects[attachment.store_fname], b"user file")
        self.assertFalse(self._local_exists(attachment))

    def test_write_falls_back_when_store_down(self):
        self.fake.down = True
        attachment = self._save(b"file one")
        self.assertTrue(self._local_exists(attachment))
        self.assertTrue(self.params.get_param("oteny_cloud_filestore.outage_since"))
        self.assertEqual(len(self._alert_messages()), 1)
        self._save(b"file two")
        self.assertEqual(len(self._alert_messages()), 1)

    def test_write_recovers_and_posts_ok(self):
        self.fake.down = True
        self._save(b"file one")
        self.fake.down = False
        attachment = self._save(b"file two")
        self.assertFalse(self.params.get_param("oteny_cloud_filestore.outage_since"))
        self.assertEqual(len(self._alert_messages()), 2)
        self.assertFalse(self._local_exists(attachment))

    def test_incomplete_key_keeps_local_and_alerts(self):
        self.startPatcher(patch.object(type(self.attachment), "_cloud_filestore_store", return_value=None))
        attachment = self._save(b"no key")
        self.assertTrue(self._local_exists(attachment))
        self.assertEqual(len(self._alert_messages()), 1)

    def test_no_alert_channel_only_logs(self):
        self.params.set_param("oteny_cloud_filestore.alert_channel_id", "")
        self.fake.down = True
        attachment = self._save(b"file")
        self.assertTrue(self._local_exists(attachment))
        self.assertTrue(self.params.get_param("oteny_cloud_filestore.outage_since"))

    def test_clone_never_puts(self):
        self.mode = "clone"
        attachment = self._save(b"clone file")
        self.assertEqual(self.fake.puts, [])
        self.assertTrue(self._local_exists(attachment))

    def test_local_only_never_puts(self):
        attachment = self._save(b"module file", install_mode=True)
        self.assertEqual(self.fake.puts, [])
        self.assertTrue(self._local_exists(attachment))

    def test_flagged_attachment_content_change_is_not_put(self):
        attachment = self._save(b"module file", install_mode=True)
        attachment.raw = b"changed content"
        self.assertEqual(self.fake.puts, [])
        self.assertTrue(self._local_exists(attachment))
