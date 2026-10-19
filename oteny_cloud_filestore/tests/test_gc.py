import os
import shutil
import tempfile
import time
from unittest.mock import patch

import psycopg2

from odoo.tests import TransactionCase, tagged


from .common import FakeCloudStore, patch_read_cache

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
        patch_read_cache(self)

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

    def _delete_job(self):
        # Like the gc, the job reads with raw SQL; in production it runs in a transaction of its own.
        self.env.flush_all()
        return self.attachment._cloud_filestore_delete_expired()

    def test_gc_makes_no_bucket_call_and_keeps_markers(self):
        young = self._unlink_aged(self._save(b"young"), 1)
        old = self._unlink_aged(self._save(b"old"), 400)
        # A confirmed upload removed the local file; put one back, as a clone or a failed upload leaves it.
        os.makedirs(os.path.dirname(self.attachment._full_path(old)), exist_ok=True)
        with open(self.attachment._full_path(old), "wb") as local_file:
            local_file.write(b"old")
        self._gc()
        self.assertEqual(self.fake.deletes, [])
        self.assertEqual(self.fake.delete_many_calls, [])
        self.assertIn(old, self.fake.objects)
        # The local file of an unreferenced name goes; its marker waits for the delete job.
        self.assertFalse(os.path.exists(self.attachment._full_path(old)))
        for fname in (young, old):
            self.assertTrue(os.path.exists(self._marker(fname)))

    def test_gc_removes_marker_of_referenced_name(self):
        first = self._save(b"same content")
        second = self._save(b"same content")
        fname = first.store_fname
        first.unlink()
        self._age_marker(fname, 400)
        self._gc()
        self.assertFalse(os.path.exists(self._marker(fname)))
        self.assertTrue(second.exists())

    def test_delete_job_deletes_old_and_keeps_young(self):
        old = self._unlink_aged(self._save(b"old"), 366)
        young = self._unlink_aged(self._save(b"young"), 30)
        self._gc()
        self.assertEqual(self._delete_job(), 1)
        # One request for the whole batch.
        self.assertEqual(self.fake.delete_many_calls, [[old]])
        self.assertNotIn(old, self.fake.objects)
        self.assertFalse(os.path.exists(self._marker(old)))
        self.assertIn(young, self.fake.objects)
        self.assertTrue(os.path.exists(self._marker(young)))

    def test_delete_job_skips_name_saved_again_after_the_gc(self):
        first = self._save(b"same content")
        fname = self._unlink_aged(first, 400)
        self._gc()
        # The name was unreferenced at the gc; then the same content is saved. The save refreshes the
        # marker, so age it again: this is the race where the job sees an old marker of a live name.
        second = self._save(b"same content")
        self.assertEqual(second.store_fname, fname)
        self._age_marker(fname, 400)
        self.assertEqual(self._delete_job(), 0)
        self.assertEqual(self.fake.delete_many_calls, [])
        self.assertIn(fname, self.fake.objects)
        self.assertFalse(os.path.exists(self._marker(fname)))

    def test_delete_job_keeps_refreshed_marker(self):
        fname = self._unlink_aged(self._save(b"marked twice"), 400)
        self._gc()
        # A later delete of the same name restarts the hold time.
        self.attachment._file_delete(fname)
        self.assertEqual(self._delete_job(), 0)
        self.assertEqual(self.fake.delete_many_calls, [])
        self.assertTrue(os.path.exists(self._marker(fname)))

    def test_delete_job_names_not_deleted_keep_their_marker(self):
        kept = self._unlink_aged(self._save(b"refused"), 400)
        gone = self._unlink_aged(self._save(b"accepted"), 400)
        self._gc()
        self.fake.fail_delete_names.add(kept)
        with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING"):
            self.assertEqual(self._delete_job(), 1)
        self.assertTrue(os.path.exists(self._marker(kept)))
        self.assertIn(kept, self.fake.objects)
        self.assertFalse(os.path.exists(self._marker(gone)))

    def test_delete_job_store_outage_keeps_markers_and_alerts(self):
        names = [self._unlink_aged(self._save(b"first"), 400), self._unlink_aged(self._save(b"second"), 400)]
        self._gc()
        alert = self.startPatcher(patch.object(type(self.attachment), "_cloud_filestore_alert"))
        self.fake.down = True
        with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING"):
            self.assertEqual(self._delete_job(), 0)
        self.assertEqual(alert.call_count, 1)
        for fname in names:
            self.assertTrue(os.path.exists(self._marker(fname)))
            self.assertIn(fname, self.fake.objects)
        self.fake.down = False
        self.assertEqual(self._delete_job(), 2)
        self.assertEqual(sorted(self.fake.deletes), sorted(names))

    def test_delete_job_batches_of_1000_names(self):
        self._age_new_markers(1001)
        self.assertEqual(self._delete_job(), 1001)
        self.assertEqual([len(call) for call in self.fake.delete_many_calls], [1000, 1])

    def _age_new_markers(self, count):
        names = ["ab/%040d" % number for number in range(count)]
        for fname in names:
            path = self._marker(fname)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            open(path, "ab").close()
            self._age_marker(fname, 400)
        return names

    def _run_as_cron(self, time_left=100.0):
        """Run the job as Odoo's cron runner does; return the order of statements and the progress calls."""
        cron = self.env.ref("oteny_cloud_filestore.ir_cron_cloud_filestore_delete_expired")
        events = []
        real_execute = self.env.cr.execute

        def record_execute(query, *args, **kwargs):
            if "LOCK ir_attachment" in query:
                events.append("lock")
            return real_execute(query, *args, **kwargs)

        with (
            patch.object(self.env.cr, "commit", side_effect=lambda: events.append("commit")),
            patch.object(self.env.cr, "execute", side_effect=record_execute),
            patch.object(
                type(self.env["ir.cron"]), "_commit_progress", return_value=time_left
            ) as progress,
        ):
            progress.side_effect = lambda *args, **kwargs: events.append("progress") or time_left
            self.env.flush_all()
            deleted = self.attachment.with_context(cron_id=cron.id)._cloud_filestore_delete_expired()
        return deleted, events, progress

    def test_delete_job_cron_commits_before_each_lock_and_reports_progress(self):
        self._age_new_markers(1001)
        deleted, events, progress = self._run_as_cron()
        self.assertEqual(deleted, 1001)
        # Every batch starts a fresh transaction, so the LOCK is its first statement; the progress
        # commit releases the lock.
        self.assertEqual(events, ["commit", "lock", "progress", "commit", "lock", "progress"])
        self.assertEqual(
            [(call.args[0], call.kwargs["remaining"]) for call in progress.call_args_list],
            [(1000, 1), (1, 0)],
        )

    def test_delete_job_cron_stops_when_time_is_out(self):
        self._age_new_markers(1001)
        deleted, _events, progress = self._run_as_cron(time_left=0.0)
        self.assertEqual(deleted, 1000)
        self.assertEqual(progress.call_count, 1)

    def test_delete_job_cron_reports_nothing_left_after_an_outage(self):
        self._age_new_markers(1001)
        self.fake.down = True
        with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING"):
            deleted, _events, progress = self._run_as_cron()
        self.assertEqual(deleted, 0)
        progress.assert_called_once_with(0, remaining=0)

    def test_delete_job_removes_cache_row_of_deleted_name(self):
        attachment = self._save(b"cached once")
        fname = attachment.store_fname
        cache = self.env["oteny.cloud.filestore.cache"]
        cache._cloud_filestore_cache_mark(fname, 11)
        self._unlink_aged(attachment, 400)
        kept = self._unlink_aged(self._save(b"cached young"), 30)
        cache._cloud_filestore_cache_mark(kept, 12)
        self._gc()
        self.assertEqual(self._delete_job(), 1)
        self.assertFalse(cache.search([("name", "=", fname)]))
        self.assertTrue(cache.search([("name", "=", kept), ("in_bucket", "=", True)]))

    def test_delete_job_stops_when_the_table_stays_locked(self):
        fname = self._unlink_aged(self._save(b"locked"), 400)
        self._gc()
        self.env.flush_all()
        real_execute = self.env.cr.execute

        def locked(query, *args, **kwargs):
            if "LOCK ir_attachment" in query:
                raise psycopg2.errors.LockNotAvailable()
            return real_execute(query, *args, **kwargs)

        with (
            patch.object(self.env.cr, "execute", side_effect=locked),
            patch.object(self.env.cr, "rollback"),
            self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING"),
        ):
            self.assertEqual(self.attachment._cloud_filestore_delete_expired(), 0)
        self.assertEqual(self.fake.delete_many_calls, [])
        self.assertTrue(os.path.exists(self._marker(fname)))

    def test_clone_and_off_mode_delete_nothing(self):
        fname = self._unlink_aged(self._save(b"clone file"), 400)
        for mode in ("clone", "off"):
            self.mode = mode
            self.assertEqual(self._delete_job(), 0)
        self.assertEqual(self.fake.delete_many_calls, [])
        self.assertTrue(os.path.exists(self._marker(fname)))

    def test_mark_resets_marker_mtime(self):
        attachment = self._save(b"marked twice")
        fname = attachment.store_fname
        self.attachment._file_delete(fname)
        self._age_marker(fname, 400)
        self.attachment._file_delete(fname)
        self.assertLess(time.time() - os.path.getmtime(self._marker(fname)), DAY)
        self._gc()
        self.assertEqual(self._delete_job(), 0)
        self.assertEqual(self.fake.deletes, [])

    def test_clone_gc_never_deletes_in_bucket(self):
        attachment = self._save(b"clone file")
        self._unlink_aged(attachment, 400)
        self.mode = "clone"
        self._gc()
        self.assertEqual(self.fake.deletes, [])

    def test_delete_job_invalid_hold_days_falls_back_to_365(self):
        young = self._save(b"young file")
        old = self._save(b"old file")
        young_name = self._unlink_aged(young, 30)
        old_name = self._unlink_aged(old, 400)
        self._gc()
        for bad in ("abc", "0", "-5", "1.5"):
            self.params.set_param("oteny_cloud_filestore.hold_days", bad)
            with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING"):
                self._delete_job()
            self.assertNotIn(young_name, self.fake.deletes)
        self.assertEqual(self.fake.deletes, [old_name])

    def test_delete_job_hold_days_below_minimum_uses_120(self):
        """Odoo.sh keeps backups 3 to 4 months: an object is never deleted earlier."""
        within_floor = self._save(b"100 days old")
        past_floor = self._save(b"130 days old")
        kept_name = self._unlink_aged(within_floor, 100)
        deleted_name = self._unlink_aged(past_floor, 130)
        self._gc()
        self.params.set_param("oteny_cloud_filestore.hold_days", "30")
        with self.assertLogs("odoo.addons.oteny_cloud_filestore.models.ir_attachment", "WARNING") as logs:
            self.assertEqual(self.attachment._cloud_filestore_hold_days(), 120)
            self._delete_job()
        self.assertIn("below the minimum", logs.output[0])
        self.assertEqual(self.fake.deletes, [deleted_name])
        self.assertIn(kept_name, self.fake.objects)
