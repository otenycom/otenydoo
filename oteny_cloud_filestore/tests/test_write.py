import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import config

from ..tools.cloud_store import CloudStoreError
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

    def test_stale_cached_state_posts_one_alert(self):
        # The parameter cache and the user's snapshot can still show "no outage".
        alert = type(self.env["oteny.cloud.filestore.alert"])
        self.startPatcher(patch.object(alert, "_outage_since", return_value=""))
        self.fake.down = True
        self._save(b"file one")
        self._save(b"file two")
        self.assertTrue(self.params.get_param("oteny_cloud_filestore.outage_since"))
        self.assertEqual(len(self._alert_messages()), 1)
        # Same on recovery: a stale "outage" hint must not post a second message.
        self.fake.down = False
        self.startPatcher(patch.object(alert, "_outage_since", return_value="stale"))
        self._save(b"file three")
        self._save(b"file four")
        self.assertEqual(len(self._alert_messages()), 2)

    def test_failing_alert_never_fails_the_save(self):
        alert = type(self.env["oteny.cloud.filestore.alert"])
        self.startPatcher(patch.object(alert, "_bucket_failed", side_effect=RuntimeError("boom")))
        self.startPatcher(patch.object(alert, "_bucket_ok", side_effect=RuntimeError("boom")))
        self.fake.down = True
        attachment = self._save(b"down")
        self.assertTrue(self._local_exists(attachment))
        self.fake.down = False
        attachment = self._save(b"up")
        self.assertEqual(self.fake.objects[attachment.store_fname], b"up")

    def test_local_copy_removal_error_does_not_fail_the_save(self):
        self.startPatcher(patch("os.unlink", side_effect=PermissionError("denied")))
        attachment = self._save(b"user file")
        self.assertEqual(self.fake.objects[attachment.store_fname], b"user file")

    def test_alert_never_contains_a_key(self):
        read_key, write_key = "READKEYVALUE12345", "WRITEKEYVALUE67890"
        self.params.set_param("oteny_cloud_filestore.read_access_key_id", read_key)
        self.params.set_param("oteny_cloud_filestore.read_secret_access_key", read_key + "secret")
        self.startPatcher(
            patch.dict(
                config.options,
                {
                    "cloud_filestore_write_access_key_id": write_key,
                    "cloud_filestore_write_secret_access_key": write_key + "secret",
                },
            )
        )
        self.startPatcher(patch.object(self.fake, "put", side_effect=CloudStoreError("AccessDenied")))
        with self.assertLogs("odoo.addons.oteny_cloud_filestore", level="DEBUG") as logs:
            self._save(b"file")
        texts = [m.body for m in self._alert_messages()] + logs.output
        self.assertTrue(self._alert_messages())
        for text in texts:
            for secret in (read_key, write_key):
                self.assertNotIn(secret, str(text))
