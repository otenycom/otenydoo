import itertools
import os
import shutil
import tempfile
from unittest.mock import MagicMock, patch

from odoo.tests import TransactionCase, tagged
from odoo.tools import config

from ..models import ir_attachment as ir_attachment_module
from ..tools import cloud_store
from ..tools.cloud_store import CloudStoreError, CloudStoreUnavailable
from .common import FakeCloudStore, patch_read_cache

LOGGER = "odoo.addons.oteny_cloud_filestore.models.ir_attachment"


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreJobs(TransactionCase):
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
        # The jobs walk the whole filestore: use an empty one of this test only.
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
        # The upload job keeps its resume point in process memory: each test starts a new sweep.
        self.startPatcher(patch.dict(ir_attachment_module._UPLOAD_RESUME_AFTER, clear=True))
        # The job also checks the server-wide load; these tests are about the files only.
        self.startPatcher(
            patch.dict(config.options, {"server_wide_modules": ["base", "web", "oteny_cloud_filestore"]})
        )

    def _save_local(self, raw, **vals):
        """Save a file while the bucket is down, so it stays in the local filestore."""
        self.fake.down = True
        if vals.get("cloud_local_only"):
            # A local-only file never meets the bucket, so nothing is logged.
            attachment = self.attachment.create({"name": "user.bin", "raw": raw, **vals})
        else:
            with self.assertLogs(LOGGER, "WARNING"):
                attachment = self.attachment.create({"name": "user.bin", "raw": raw, **vals})
        self.fake.down = False
        return attachment

    def _local_exists(self, fname):
        return os.path.exists(self.attachment._full_path(fname))

    def test_upload_job_keeps_local_file_of_local_only_twin(self):
        # The user file stays local while the bucket is down; a local-only file has the same bytes.
        user_file = self._save_local(b"shared bytes")
        twin = self._save_local(b"shared bytes", cloud_local_only=True)
        self.assertEqual(user_file.store_fname, twin.store_fname)
        # The user file goes to the bucket; the local file stays for the local-only row.
        self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 1)
        self.assertEqual(self.fake.puts, [user_file.store_fname])
        self.assertTrue(self._local_exists(twin.store_fname))
        self.assertEqual(twin.raw, b"shared bytes")
        # A second run asks the bucket again, finds the object and makes no put.
        with self.assertLogs(LOGGER, "INFO") as logs:
            self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 0)
        self.assertEqual(self.fake.puts, [user_file.store_fname])
        self.assertTrue(self._local_exists(twin.store_fname))
        self.assertIn("0 uploaded, 1 already in the bucket", "\n".join(logs.output))

    def test_upload_pending_moves_local_files(self):
        first = self._save_local(b"file one")
        second = self._save_local(b"file two")
        self.assertTrue(self.params.get_param("oteny_cloud_filestore.outage_since"))
        self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 2)
        self.assertEqual(self.fake.objects[first.store_fname], b"file one")
        self.assertEqual(self.fake.objects[second.store_fname], b"file two")
        self.assertFalse(self._local_exists(first.store_fname))
        self.assertFalse(self._local_exists(second.store_fname))
        self.assertFalse(self.params.get_param("oteny_cloud_filestore.outage_since"))

    def test_upload_job_skips_existing_object(self):
        attachment = self._save_local(b"already there")
        self.fake.objects[attachment.store_fname] = b"already there"
        # An object that is there already is not an upload, but its local copy goes.
        with self.assertLogs(LOGGER, "INFO") as logs:
            self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 0)
        self.assertIn("0 uploaded, 1 already in the bucket", "\n".join(logs.output))
        self.assertEqual(self.fake.puts, [])
        self.assertFalse(self._local_exists(attachment.store_fname))

    def test_upload_skips_local_only(self):
        kept = self._save_local(b"module file", cloud_local_only=True)
        pending = self._save_local(b"user file")
        self.attachment._cloud_filestore_upload_pending()
        self.assertNotIn(kept.store_fname, self.fake.objects)
        self.assertTrue(self._local_exists(kept.store_fname))
        self.assertIn(pending.store_fname, self.fake.objects)

    def test_upload_respects_limit(self):
        for raw in (b"one", b"two", b"three"):
            self._save_local(raw)
        self.assertEqual(self.attachment._cloud_filestore_upload_pending(limit=2), 2)
        self.assertEqual(len(self.fake.objects), 2)
        self.assertEqual(self.attachment._cloud_filestore_upload_pending(limit=2), 1)
        self.assertEqual(len(self.fake.objects), 3)

    def test_upload_not_confirmed_keeps_local(self):
        attachment = self._save_local(b"half written")
        with patch.object(self.fake, "head", return_value=3):
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 0)
        self.assertTrue(self._local_exists(attachment.store_fname))

    def test_upload_store_error_stops_and_alerts(self):
        self._save_local(b"first")
        self._save_local(b"second")
        alert = self.startPatcher(patch.object(type(self.attachment), "_cloud_filestore_alert"))
        with patch.object(self.fake, "put", side_effect=CloudStoreUnavailable("down")) as put:
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 0)
        self.assertEqual(put.call_count, 1)
        self.assertEqual([call.args[0] for call in alert.call_args_list], ["_bucket_failed"])

    def test_upload_failure_after_success_does_not_report_ok(self):
        self._save_local(b"first")
        self._save_local(b"second")
        alert = self.startPatcher(patch.object(type(self.attachment), "_cloud_filestore_alert"))
        real_put = self.fake.put
        calls = []

        def put_once(name, data):
            calls.append(name)
            if len(calls) > 1:
                raise CloudStoreUnavailable("down")
            real_put(name, data)

        with patch.object(self.fake, "put", side_effect=put_once):
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 1)
        self.assertEqual([call.args[0] for call in alert.call_args_list], ["_bucket_failed"])

    def test_upload_ignores_leftover_temp_file(self):
        attachment = self._save_local(b"real file")
        leftover = os.path.join(os.path.dirname(self.attachment._full_path(attachment.store_fname)), ".cfs-leftover")
        with open(leftover, "wb") as temp_file:
            temp_file.write(b"partial")
        self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 1)
        self.assertEqual(list(self.fake.objects), [attachment.store_fname])
        self.assertTrue(os.path.exists(leftover))

    def test_download_interrupted_write_leaves_nothing(self):
        attachment = self.attachment.create({"name": "user.bin", "raw": b"bucket only"})
        fname = attachment.store_fname
        with patch("os.fsync", side_effect=OSError("disk full")):
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self.attachment._cloud_filestore_download_all(), 0)
        self.assertFalse(self._local_exists(fname))
        self.assertEqual(os.listdir(os.path.dirname(self.attachment._full_path(fname))), [])

    def test_download_skips_bytes_that_do_not_match_the_name(self):
        attachment = self.attachment.create({"name": "user.bin", "raw": b"bucket only"})
        fname = attachment.store_fname
        self.fake.objects[fname] = b"corrupt bytes"
        with self.assertLogs(LOGGER, "WARNING"):
            self.assertEqual(self.attachment._cloud_filestore_download_all(), 0)
        self.assertFalse(self._local_exists(fname))

    def test_download_all_restores_local_files(self):
        attachment = self.attachment.create({"name": "user.bin", "raw": b"bucket only"})
        fname = attachment.store_fname
        self.assertEqual(self.fake.objects[fname], b"bucket only")
        self.assertFalse(self._local_exists(fname))
        self.assertGreaterEqual(self.attachment._cloud_filestore_download_all(), 1)
        with open(self.attachment._full_path(fname), "rb") as local_file:
            self.assertEqual(local_file.read(), b"bucket only")

    def test_download_never_overwrites_local(self):
        attachment = self._save_local(b"local copy")
        self.fake.objects[attachment.store_fname] = b"other bytes"
        self.attachment._cloud_filestore_download_all()
        with open(self.attachment._full_path(attachment.store_fname), "rb") as local_file:
            self.assertEqual(local_file.read(), b"local copy")

    def test_jobs_do_nothing_in_wrong_mode(self):
        pending = self._save_local(b"pending file")
        bucket_only = self.attachment.create({"name": "other.bin", "raw": b"bucket only"})
        puts_before = list(self.fake.puts)
        self.mode = "clone"
        self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 0)
        self.assertEqual(self.fake.puts, puts_before)
        self.assertTrue(self._local_exists(pending.store_fname))
        self.mode = "off"
        self.assertEqual(self.attachment._cloud_filestore_download_all(), 0)
        self.assertFalse(self._local_exists(bucket_only.store_fname))

    def test_upload_includes_rows_with_null_flag(self):
        """Rows from before the install hold NULL in cloud_local_only: they are user files."""
        attachment = self._save_local(b"old file")
        self.env.flush_all()
        self.env.cr.execute("UPDATE ir_attachment SET cloud_local_only = NULL WHERE id = %s", [attachment.id])
        self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 1)
        self.assertIn(attachment.store_fname, self.fake.objects)
        self.assertFalse(self._local_exists(attachment.store_fname))

    def test_upload_access_error_stops_and_alerts(self):
        self._save_local(b"first")
        self._save_local(b"second")
        alert = self.startPatcher(patch.object(type(self.attachment), "_cloud_filestore_alert"))
        refusal = cloud_store.CloudStoreAccessError("put_object: store refused (403 AccessDenied)")
        with patch.object(self.fake, "put", side_effect=refusal) as put:
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 0)
        self.assertEqual(put.call_count, 1)
        self.assertEqual([call.args[0] for call in alert.call_args_list], ["_bucket_failed"])

    def test_upload_skips_refused_file_and_continues(self):
        refused = self._save_local(b"too large")
        others = self._save_local(b"first") | self._save_local(b"second")
        alert = self.startPatcher(patch.object(type(self.attachment), "_cloud_filestore_alert"))
        real_put = self.fake.put

        def refuse_one(name, data):
            if name == refused.store_fname:
                raise CloudStoreError("put_object: store refused (400 EntityTooLarge)")
            real_put(name, data)

        with patch.object(self.fake, "put", side_effect=refuse_one):
            with self.assertLogs(LOGGER, "WARNING") as logs:
                self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 2)
        self.assertIn("refused", "\n".join(logs.output))
        self.assertEqual(sorted(self.fake.objects), sorted(others.mapped("store_fname")))
        self.assertTrue(self._local_exists(refused.store_fname))
        self.assertNotIn("_bucket_failed", [call.args[0] for call in alert.call_args_list])

    def test_upload_stops_after_time_budget(self):
        for raw in (b"one", b"two", b"three"):
            self._save_local(raw)
        # The clock reads 0 at the start and at the first check, then is past the budget.
        clock = MagicMock()
        clock.monotonic.side_effect = itertools.chain([0, 0], itertools.repeat(10_000))
        with patch.object(ir_attachment_module, "time", clock):
            self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 1)
        self.assertEqual(len(self.fake.objects), 1)
        self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 2)

    def _progress_patch(self):
        """Record the progress reports that the upload job gives to the cron runner."""
        return patch.object(type(self.env["ir.cron"]), "_commit_progress", return_value=0.0)

    def _as_cron(self):
        """The attachment model as Odoo's cron runner calls it."""
        cron = self.env.ref("oteny_cloud_filestore.ir_cron_cloud_filestore_upload")
        return self.attachment.with_context(cron_id=cron.id)

    def test_upload_reports_progress_to_the_cron(self):
        """With work left, the cron runner calls the job again at once; else it waits the interval."""
        for raw in (b"one", b"two", b"three"):
            self._save_local(raw)
        # A call has a tenth of the run budget, so the second file waits for the next call.
        clock = MagicMock()
        clock.monotonic.side_effect = itertools.chain([0, 0], itertools.repeat(30))
        with self._progress_patch() as progress, patch.object(ir_attachment_module, "time", clock):
            self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 1)
        progress.assert_called_once_with(1, remaining=2)
        with self._progress_patch() as progress:
            self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(limit=1), 1)
        progress.assert_called_once_with(1, remaining=1)
        # A run by hand reports nothing; the caller commits, as the Reverse Migration steps do.
        with self._progress_patch() as progress:
            self.assertEqual(self.attachment._cloud_filestore_upload_pending(), 1)
        progress.assert_not_called()
        with self._progress_patch() as progress:
            self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 0)
        progress.assert_called_once_with(0, remaining=0)

    def test_upload_progress_ignores_files_that_stay_local(self):
        """A file kept local on purpose must not make the cron run again and again."""
        shared = self._save_local(b"shared bytes")
        self._save_local(b"shared bytes", cloud_local_only=True)
        refused = self._save_local(b"too large")
        self._save_local(b"normal")
        real_put = self.fake.put

        def refuse_one(name, data):
            if name == refused.store_fname:
                raise CloudStoreError("put_object: store refused (400 EntityTooLarge)")
            real_put(name, data)

        with self._progress_patch() as progress, patch.object(self.fake, "put", side_effect=refuse_one):
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 2)
        progress.assert_called_once_with(2, remaining=0)
        self.assertTrue(self._local_exists(shared.store_fname))
        self.assertTrue(self._local_exists(refused.store_fname))

    def test_upload_resumes_after_files_that_stay_local(self):
        """A file kept local before the stop point must not cost every later call of the sweep."""
        names = sorted(self._save_local(raw).store_fname for raw in (b"one", b"two", b"three"))
        # The roles follow the sort order of the names, not the order of creation.
        refused_name, first_name, last_name = names
        real_put = self.fake.put

        def refuse_one(name, data):
            if name == refused_name:
                raise CloudStoreError("put_object: store refused (400 EntityTooLarge)")
            real_put(name, data)

        put = self.startPatcher(patch.object(self.fake, "put", side_effect=refuse_one))
        head = self.startPatcher(patch.object(self.fake, "head", wraps=self.fake.head))
        # The clock reads 0 at the start and at the first two checks, then is past the budget.
        clock = MagicMock()
        clock.monotonic.side_effect = itertools.chain([0, 0, 0], itertools.repeat(10_000))
        with self._progress_patch() as progress, patch.object(ir_attachment_module, "time", clock):
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 1)
        progress.assert_called_once_with(1, remaining=1)
        self.assertEqual([call.args[0] for call in put.call_args_list], [refused_name, first_name])
        put.reset_mock()
        head.reset_mock()
        # The next call continues after the last name reached: no head and no put for the refused file.
        with self._progress_patch() as progress:
            self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 1)
        progress.assert_called_once_with(1, remaining=0)
        self.assertEqual([call.args[0] for call in put.call_args_list], [last_name])
        self.assertNotIn(refused_name, [call.args[0] for call in head.call_args_list])
        # The sweep is over, so the next one tries the refused file again, once.
        put.reset_mock()
        with self._progress_patch() as progress:
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 0)
        progress.assert_called_once_with(0, remaining=0)
        self.assertEqual([call.args[0] for call in put.call_args_list], [refused_name])
        self.assertTrue(self._local_exists(refused_name))

    def test_upload_outage_reports_no_remaining(self):
        """During an outage the cron waits its interval instead of calling the bucket again at once."""
        self._save_local(b"first")
        self._save_local(b"second")
        self.startPatcher(patch.object(type(self.attachment), "_cloud_filestore_alert"))
        with self._progress_patch() as progress, patch.object(
            self.fake, "put", side_effect=CloudStoreUnavailable("down")
        ):
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(self._as_cron()._cloud_filestore_upload_pending(), 0)
        progress.assert_called_once_with(0, remaining=0)

    def test_download_file_has_odoo_file_mode(self):
        """A restored file must have the rights of a file that Odoo writes itself."""
        self.mode = "clone"
        odoo_file = self.attachment.create({"name": "local.bin", "raw": b"written by odoo"})
        self.mode = "production"
        restored = self.attachment.create({"name": "user.bin", "raw": b"bucket only"})
        self.assertFalse(self._local_exists(restored.store_fname))
        # Names of other attachments of the test database are not in the fake bucket.
        with self.assertLogs(LOGGER, "WARNING"):
            self.assertGreaterEqual(self.attachment._cloud_filestore_download_all(), 1)
        expected = os.stat(self.attachment._full_path(odoo_file.store_fname)).st_mode
        self.assertEqual(os.stat(self.attachment._full_path(restored.store_fname)).st_mode, expected)

    def test_download_local_error_logs_a_stop(self):
        self.attachment.create({"name": "user.bin", "raw": b"bucket only"})
        self.attachment.create({"name": "user.bin", "raw": b"bucket only too"})
        with patch("os.fsync", side_effect=OSError("disk full")) as fsync:
            with self.assertLogs(LOGGER, "WARNING") as logs:
                self.assertEqual(self.attachment._cloud_filestore_download_all(), 0)
        self.assertEqual(fsync.call_count, 1)
        self.assertIn("download job stopped at", "\n".join(logs.output))
