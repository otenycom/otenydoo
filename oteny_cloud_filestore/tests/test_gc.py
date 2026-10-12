import os
import shutil
import tempfile
import time
from unittest.mock import patch

from odoo.tests import TransactionCase, tagged


from ..tools.cloud_store import CloudStoreUnavailable
from .common import FakeCloudStore

DAY = 86400


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreGc(TransactionCase):
    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.attachment = cls.env["ir.attachment"]
        cls.params = cls.env["ir.config_parameter"].sudo()
        cls.params.set_param("oteny_cloud_filestore.hold_days", "365")

    def setUp(self):
        super().setUp()
        # The checklist walk must only see the markers of this test.
        filestore = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, filestore, ignore_errors=True)
        model = type(self.attachment)
        self.startPatcher(patch.object(model, "_filestore", return_value=filestore))
        self.fake = FakeCloudStore()
        self.mode = "production"
        # The ORM scans model attributes for ondelete hooks and calls a Mock with the recordset.
        self.startPatcher(patch.object(model, "_cloud_filestore_mode", side_effect=lambda *args: self.mode))
        self.startPatcher(patch.object(model, "_cloud_filestore_store", return_value=self.fake))
        self.startPatcher(patch.object(model, "_storage", return_value="file"))

    def _save(self, raw):
        return self.attachment.create({"name": "user.bin", "raw": raw})

    def _marker(self, fname):
        return os.path.join(self.attachment._full_path("checklist"), fname)

    def _age_marker(self, fname, days):
        old = time.time() - days * DAY
        os.utime(self._marker(fname), (old, old))

    def _gc(self):
        # The gc query is raw SQL, as in production where the gc runs in its own transaction.
        self.env.flush_all()
        self.attachment._gc_file_store_unsafe()

    def _unlink_aged(self, attachment, days):
        fname = attachment.store_fname
        attachment.unlink()
        self._age_marker(fname, days)
        return fname

    def test_gc_keeps_young_object(self):
        attachment = self._save(b"young")
        fname = attachment.store_fname
        attachment.unlink()
        self._gc()
        self.assertEqual(self.fake.deletes, [])
        self.assertIn(fname, self.fake.objects)
        self.assertTrue(os.path.exists(self._marker(fname)))

    def test_gc_deletes_after_hold_time(self):
        attachment = self._save(b"old")
        fname = self._unlink_aged(attachment, 366)
        self._gc()
        self.assertEqual(self.fake.deletes, [fname])
        self.assertNotIn(fname, self.fake.objects)
        self.assertFalse(os.path.exists(self._marker(fname)))

    def test_gc_keeps_object_of_reused_name(self):
        first = self._save(b"same content")
        second = self._save(b"same content")
        fname = first.store_fname
        self.assertEqual(fname, second.store_fname)
        first.unlink()
        self._age_marker(fname, 400)
        self._gc()
        self.assertEqual(self.fake.deletes, [])
        self.assertIn(fname, self.fake.objects)
        self.assertFalse(os.path.exists(self._marker(fname)))

    def test_mark_resets_marker_mtime(self):
        attachment = self._save(b"marked twice")
        fname = attachment.store_fname
        self.attachment._file_delete(fname)
        self._age_marker(fname, 400)
        self.attachment._file_delete(fname)
        self.assertLess(time.time() - os.path.getmtime(self._marker(fname)), DAY)
        self._gc()
        self.assertEqual(self.fake.deletes, [])

    def test_clone_gc_never_deletes_in_bucket(self):
        attachment = self._save(b"clone file")
        fname = self._unlink_aged(attachment, 400)
        self.mode = "clone"
        self._gc()
        self.assertEqual(self.fake.deletes, [])

    def test_gc_invalid_hold_days_falls_back_to_365(self):
        young = self._save(b"young file")
        old = self._save(b"old file")
        young_name = self._unlink_aged(young, 30)
        old_name = self._unlink_aged(old, 400)
        for bad in ("abc", "0", "-5", "1.5"):
            self.params.set_param("oteny_cloud_filestore.hold_days", bad)
            with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING"):
                self._gc()
            self.assertNotIn(young_name, self.fake.deletes)
        self.assertEqual(self.fake.deletes, [old_name])

    def test_gc_store_error_keeps_marker_and_stops(self):
        first = self._save(b"first")
        second = self._save(b"second")
        names = [self._unlink_aged(first, 400), self._unlink_aged(second, 400)]
        alert = self.startPatcher(patch.object(type(self.attachment), "_cloud_filestore_alert"))
        with patch.object(self.fake, "delete", side_effect=CloudStoreUnavailable("down")) as delete:
            with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING"):
                self._gc()
        # The first failure stops the bucket deletes of this run and posts one alert.
        self.assertEqual(delete.call_count, 1)
        self.assertEqual(alert.call_count, 1)
        for fname in names:
            self.assertTrue(os.path.exists(self._marker(fname)))
            self.assertIn(fname, self.fake.objects)
        self._gc()
        self.assertEqual(sorted(self.fake.deletes), sorted(names))
