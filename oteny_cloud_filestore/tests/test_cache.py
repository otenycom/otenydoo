import datetime
import itertools
import os
import time
from unittest.mock import ANY, MagicMock, patch

import psycopg2

from odoo import fields
from odoo.tests import HttpCase, TransactionCase, tagged
from odoo.tools import config

from ..models import cache as cache_module
from ..models import ir_attachment as ir_attachment_module
from .common import patch_read_cache
from .test_read import CloudFilestoreReadMixin

LOGGER = "odoo.addons.oteny_cloud_filestore.models.ir_attachment"
# 0.001 MB is 1048 bytes, so a file of more than 262 bytes may not enter the cache.
SMALL_LIMIT_MB = "0.001"
SMALL = b"a small file that may enter the cache"
LARGE = b"x" * 1000


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreCache(CloudFilestoreReadMixin, TransactionCase):
    """The read cache keeps local copies of bucket files and counts their reads (D21)."""

    def setUp(self):
        super().setUp()
        self._patch_cloud_filestore()
        self.cache_write = patch_read_cache(self, SMALL_LIMIT_MB)
        self.startPatcher(patch.object(type(self.env["ir.attachment"]), "_cloud_filestore_alert"))
        self.cache = self.env["oteny.cloud.filestore.cache"]
        self.startPatcher(patch.dict(ir_attachment_module._UPLOAD_RESUME_AFTER, clear=True))
        self.startPatcher(
            patch.dict(config.options, {"server_wide_modules": ["base", "web", "oteny_cloud_filestore"]})
        )

    def _row(self, name):
        return self.cache.search([("name", "=", name)])

    def _local(self, attachment):
        return os.path.exists(attachment._full_path(attachment.store_fname))

    def _buffer(self):
        return ir_attachment_module._CACHE_READS.get(self.env.cr.dbname, {})

    def test_limit_and_may_cache(self):
        attachment = self.env["ir.attachment"]
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_cloud_filestore.cache_limit_mb", "8")
        self.assertEqual(attachment._cloud_filestore_cache_limit(), 8 * 1024 * 1024)
        self.assertTrue(attachment._cloud_filestore_may_cache(2 * 1024 * 1024))
        self.assertFalse(attachment._cloud_filestore_may_cache(2 * 1024 * 1024 + 1))
        params.set_param("oteny_cloud_filestore.cache_limit_mb", "0")
        self.assertFalse(attachment._cloud_filestore_may_cache(1))
        for bad in ("lots", "-5", "inf"):
            params.set_param("oteny_cloud_filestore.cache_limit_mb", bad)
            with self.assertLogs(LOGGER, "WARNING"):
                self.assertEqual(attachment._cloud_filestore_cache_limit(), 100 * 1024 * 1024)

    def test_write_through_keeps_small_and_removes_large(self):
        small = self.env["ir.attachment"].create({"name": "small.bin", "raw": SMALL})
        large = self.env["ir.attachment"].create({"name": "large.bin", "raw": LARGE})
        self.assertIn(small.store_fname, self.fake.objects)
        self.assertIn(large.store_fname, self.fake.objects)
        self.assertTrue(self._local(small), "a small file stays as a cache copy")
        self.assertFalse(self._local(large), "a large file is removed after the upload")
        row = self._row(small.store_fname)
        self.assertTrue(row.in_bucket)
        self.assertEqual(row.size, len(SMALL))
        self.assertEqual(row.reads, 1, "the insertion counts as the first read (M2)")
        self.assertTrue(row.last_read and row.reads_date)
        self.assertFalse(self._row(large.store_fname))

    def test_write_through_keeps_local_only_twin_without_mark(self):
        local_only = self.env["ir.attachment"].create(
            {"name": "bundle.bin", "raw": SMALL, "cloud_local_only": True}
        )
        user_file = self.env["ir.attachment"].create({"name": "user.bin", "raw": SMALL})
        self.assertEqual(user_file.store_fname, local_only.store_fname)
        self.assertTrue(self._local(user_file))
        self.assertFalse(self._row(user_file.store_fname))

    def test_upload_job_marks_and_skips_marked_names(self):
        self.fake.down = True
        with self.assertLogs(LOGGER, "WARNING"):
            small = self.env["ir.attachment"].create({"name": "small.bin", "raw": SMALL})
            large = self.env["ir.attachment"].create({"name": "large.bin", "raw": LARGE})
        self.fake.down = False
        self.assertFalse(self._row(small.store_fname))
        self.assertEqual(self.env["ir.attachment"]._cloud_filestore_upload_pending(), 2)
        self.assertTrue(self._local(small))
        self.assertFalse(self._local(large))
        self.assertTrue(self._row(small.store_fname).in_bucket)
        # The next sweep never asks the bucket for the cache copy again.
        with patch.object(self.fake, "head", wraps=self.fake.head) as head:
            self.assertEqual(self.env["ir.attachment"]._cloud_filestore_upload_pending(), 0)
        self.assertNotIn(small.store_fname, [call.args[0] for call in head.call_args_list])

    def test_bucket_read_fills_and_marks_at_once(self):
        attachment = self._bucket_only_attachment_of(SMALL)
        self.mode = "clone"  # a clone fills the cache too
        self.assertEqual(attachment._file_read(attachment.store_fname), SMALL)
        self.assertTrue(self._local(attachment), "the read left a cache copy")
        self.cache_write.assert_called_once_with(
            ANY, "_cloud_filestore_cache_mark", attachment.store_fname, len(SMALL)
        )
        row = self._row(attachment.store_fname)
        self.assertTrue(row.in_bucket, "the mark is written with the fill, not with the read counts")
        self.assertEqual(row.size, len(SMALL))
        self.assertEqual(row.reads, 1)
        # The read itself waits in the buffer; the flush adds it to the row.
        counts = self._buffer()
        self.assertEqual(counts[attachment.store_fname], 1)
        self.cache._cloud_filestore_cache_flush(counts)
        self.assertAlmostEqual(self._row(attachment.store_fname).reads, 2, places=3)
        # The next read is local and counts without a new fill.
        with patch.object(self.fake, "get", wraps=self.fake.get) as get:
            self.assertEqual(attachment._file_read(attachment.store_fname), SMALL)
        get.assert_not_called()
        self.assertEqual(self.cache_write.call_count, 1)
        self.assertEqual(self._buffer()[attachment.store_fname], 2)

    def test_fill_without_mark_removes_the_copy(self):
        """An unmarked local file means "must stay": a cache copy whose mark failed must not stay."""
        attachment = self._bucket_only_attachment_of(SMALL)
        self.cache_write.side_effect = lambda *args: False
        self.assertEqual(attachment._file_read(attachment.store_fname), SMALL)
        self.assertFalse(self._local(attachment))
        self.assertFalse(self._row(attachment.store_fname))

    def test_partial_or_large_read_never_fills(self):
        small = self._bucket_only_attachment_of(SMALL)
        large = self._bucket_only_attachment_of(LARGE)
        self.assertEqual(small._file_read(small.store_fname, size=5), SMALL[:5])
        self.assertEqual(large._file_read(large.store_fname), LARGE)
        self.assertFalse(self._local(small))
        self.assertFalse(self._local(large))
        self.cache_write.assert_not_called()
        self.assertEqual(self._buffer()[small.store_fname], 1)

    def test_fill_disk_error_still_returns_the_data(self):
        attachment = self._bucket_only_attachment_of(SMALL)
        model = type(attachment)
        disk_full = patch.object(model, "_cloud_filestore_write_new_file", side_effect=OSError("disk full"))
        with disk_full, self.assertLogs(LOGGER, "WARNING") as logs:
            self.assertEqual(attachment._file_read(attachment.store_fname), SMALL)
        self.assertIn("cache copy", logs.output[0])
        self.cache_write.assert_not_called()
        self.assertEqual(self._buffer()[attachment.store_fname], 1)

    def test_flush_fades_the_count(self):
        two_days_ago = fields.Datetime.now() - datetime.timedelta(days=2)
        self.cache.create(
            {"name": "ab/ab12", "size": 10, "reads": 4, "reads_date": two_days_ago, "in_bucket": True}
        )
        self.cache._cloud_filestore_cache_flush({"ab/ab12": 1})
        row = self._row("ab/ab12")
        self.assertAlmostEqual(row.reads, 2.0, places=3)
        self.assertTrue(row.in_bucket)
        self.assertGreater(row.reads_date, two_days_ago)

    def test_local_read_without_row_creates_no_row(self):
        self.mode = "clone"  # a clone's own file stays local, without a mark
        attachment = self.env["ir.attachment"].create({"name": "own.bin", "raw": SMALL})
        self.assertTrue(self._local(attachment))
        self.assertEqual(attachment._file_read(attachment.store_fname), SMALL)
        counts = self._buffer()
        self.assertEqual(counts[attachment.store_fname], 1)
        self.cache._cloud_filestore_cache_flush(counts)
        self.assertFalse(self._row(attachment.store_fname))

    def test_no_count_when_off(self):
        self.mode = "off"
        attachment = self.env["ir.attachment"].create({"name": "plain.bin", "raw": SMALL})
        attachment._file_read(attachment.store_fname)
        self.assertNotIn(attachment.store_fname, self._buffer())

    def test_counts_are_written_at_most_once_a_minute(self):
        self.mode = "clone"
        attachment = self.env["ir.attachment"].create({"name": "own.bin", "raw": SMALL})
        dbname = self.env.cr.dbname
        attachment._file_read(attachment.store_fname)
        self.cache_write.assert_not_called()
        # A minute later the next read hands the whole buffer over and empties it.
        ir_attachment_module._CACHE_FLUSHED_AT[dbname] -= ir_attachment_module.CACHE_FLUSH_SECONDS
        attachment._file_read(attachment.store_fname)
        self.cache_write.assert_called_once_with(
            ANY, "_cloud_filestore_cache_flush", {attachment.store_fname: 2}
        )
        self.assertNotIn(dbname, ir_attachment_module._CACHE_READS)

    def test_failed_cache_write_only_logs(self):
        """The counts are hints: a write that fails drops them and never fails the read."""
        # The real method (the test patches it on the registry's model class), with a cursor that cannot open.
        write = ir_attachment_module.IrAttachment._cloud_filestore_cache_in_own_transaction
        down = psycopg2.OperationalError("down")
        no_cursor = patch.object(type(self.env.registry), "cursor", side_effect=down)
        with no_cursor, self.assertLogs(LOGGER, "WARNING") as logs:
            done = write(self.env["ir.attachment"], "_cloud_filestore_cache_flush", {"ab/ab12": 1})
        self.assertFalse(done)
        self.assertIn("cache write _cloud_filestore_cache_flush dropped", logs.output[0])

    def test_cache_write_never_waits_for_the_request_transaction(self):
        """The request's open transaction holds the name: the separate write gives up after the lock timeout.

        Without the timeout this is a deadlock that Postgres cannot see: the
        request waits for its own write, which waits for the request.
        """
        name = "ab/locked-by-the-request"
        self.cache._cloud_filestore_cache_mark(name, 5)  # uncommitted insert in the test transaction
        write = ir_attachment_module.IrAttachment._cloud_filestore_cache_in_own_transaction
        started = time.monotonic()
        # Odoo logs the cancelled statement itself ("bad query").
        with self.assertLogs("odoo.sql_db", "ERROR"), self.assertLogs(LOGGER, "WARNING") as logs:
            self.assertFalse(write(self.env["ir.attachment"], "_cloud_filestore_cache_mark", name, 5))
        self.assertLess(time.monotonic() - started, 10)
        self.assertIn("lock timeout", logs.output[0])

    def test_unconfirmed_save_clears_the_mark(self):
        """A mark means "the bucket holds it": a save that the bucket does not confirm waits for upload."""
        attachment = self.env["ir.attachment"].create({"name": "small.bin", "raw": SMALL})
        self.assertTrue(self._row(attachment.store_fname).in_bucket)
        self.fake.down = True
        with self.assertLogs(LOGGER, "WARNING"):
            again = self.env["ir.attachment"].create({"name": "again.bin", "raw": SMALL})
        self.assertEqual(again.store_fname, attachment.store_fname)
        self.assertFalse(self._row(attachment.store_fname).in_bucket)
        self.assertTrue(self._local(attachment))
        # The upload job sees it as waiting again, and marks it once the bucket confirms it.
        self.fake.down = False
        self.env["ir.attachment"]._cloud_filestore_upload_pending()
        self.assertTrue(self._row(attachment.store_fname).in_bucket)

    def test_failed_mark_never_fails_the_save(self):
        model = type(self.cache)
        missing_table = psycopg2.errors.UndefinedTable("gone")
        broken = patch.object(model, "_cloud_filestore_cache_mark", side_effect=missing_table)
        with broken, self.assertLogs(LOGGER, "WARNING") as logs:
            attachment = self.env["ir.attachment"].create({"name": "small.bin", "raw": SMALL})
        self.assertIn("cache mark", logs.output[0])
        self.assertTrue(self._local(attachment), "the copy stays unmarked and waits for the upload job")
        self.assertFalse(self._row(attachment.store_fname))

    def test_reverse_migration_clears_the_marks(self):
        """After the reverse migration every file stays local for good: none may be evicted (D2)."""
        cached = self.env["ir.attachment"].create({"name": "small.bin", "raw": SMALL})
        self.assertTrue(self._row(cached.store_fname).in_bucket)
        # The other files of the test database are in no bucket: the job logs them.
        with self.assertLogs(LOGGER, "WARNING"):
            self.env["ir.attachment"]._cloud_filestore_download_all()
        self.assertFalse(self._row(cached.store_fname).in_bucket)
        self.assertTrue(self._local(cached))

    def _temp_files(self):
        """The temporary files of a fill that are left in the local filestore; there must be none."""
        filestore = self.env["ir.attachment"]._filestore()
        return [
            name for _folder, _dirs, files in os.walk(filestore) for name in files if name.startswith(".cfs-")
        ]

    def test_fill_checks_the_sha1(self):
        """A damaged object never becomes a cache copy: the eviction only compares sizes (I2)."""
        attachment = self._bucket_only_attachment_of(SMALL)
        damaged = SMALL.replace(b"a small", b"A small")  # the same size, other bytes
        self.fake.objects[attachment.store_fname] = damaged
        with self.assertLogs(LOGGER, "WARNING") as logs:
            self.assertEqual(attachment._file_read(attachment.store_fname), damaged)
        self.assertIn("do not match its name", logs.output[0])
        self.assertFalse(self._local(attachment))
        self.assertFalse(self._temp_files())
        self.cache_write.assert_not_called()
        self.assertFalse(self._row(attachment.store_fname))

    def test_fill_from_chunks_stops_cleanly_on_a_bucket_error(self):
        """A download that breaks off leaves no partial file and no mark, and reaches the caller (I2)."""
        attachment = self._bucket_only_attachment_of(SMALL)

        def broken_chunks():
            yield SMALL[:5]
            raise ir_attachment_module.CloudStoreUnavailable("broke off")

        with self.assertRaises(ir_attachment_module.CloudStoreUnavailable):
            self.env["ir.attachment"]._cloud_filestore_cache_fill(
                attachment.store_fname, broken_chunks(), len(SMALL)
            )
        self.assertFalse(self._local(attachment))
        self.assertFalse(self._temp_files())
        self.cache_write.assert_not_called()
        # Good chunks give the copy and its mark.
        chunks = iter([SMALL[:5], SMALL[5:]])
        self.assertTrue(
            self.env["ir.attachment"]._cloud_filestore_cache_fill(attachment.store_fname, chunks, len(SMALL))
        )
        self.assertTrue(self._local(attachment))
        self.assertTrue(self._row(attachment.store_fname).in_bucket)
        self.assertFalse(self._temp_files())

    def test_local_only_name_is_never_marked(self):
        """A name that a local-only file uses stays local for good: no path may mark it (I3)."""
        # Write-through, then a module installs the same content: the local-only save clears the mark.
        user_file = self.env["ir.attachment"].create({"name": "user.bin", "raw": SMALL})
        self.assertTrue(self._row(user_file.store_fname).in_bucket)
        local_only = self.env["ir.attachment"].create(
            {"name": "icon.bin", "raw": SMALL, "cloud_local_only": True}
        )
        self.assertEqual(local_only.store_fname, user_file.store_fname)
        self.assertFalse(self._row(user_file.store_fname).in_bucket)
        # The mark helper refuses it too, whoever calls it.
        self.cache._cloud_filestore_cache_mark(user_file.store_fname, len(SMALL))
        self.assertFalse(self._row(user_file.store_fname).in_bucket)
        # A read from the bucket never fills it: the file of a local-only row must not come from the bucket.
        os.unlink(local_only._full_path(local_only.store_fname))
        self.cache_write.reset_mock()
        self.assertEqual(local_only._file_read(local_only.store_fname), SMALL)
        self.assertFalse(self._local(local_only))
        self.cache_write.assert_not_called()

    def _bucket_only_attachment_of(self, raw):
        """A file that is only in the bucket: saved while the cache keeps nothing."""
        params = self.env["ir.config_parameter"].sudo()
        params.set_param("oteny_cloud_filestore.cache_limit_mb", "0")
        attachment = self._bucket_only_attachment(raw)
        params.set_param("oteny_cloud_filestore.cache_limit_mb", SMALL_LIMIT_MB)
        return attachment


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreCacheEviction(CloudFilestoreReadMixin, TransactionCase):
    """The eviction cron removes the cache copies with the lowest keep value, but only copies the bucket holds (D21)."""

    LIMIT = 1024 * 1024  # 1 MB; the sizes of the D21 example are scaled to a hundredth of it

    def setUp(self):
        super().setUp()
        self._patch_cloud_filestore()
        patch_read_cache(self, "1")
        self.cache = self.env["oteny.cloud.filestore.cache"]
        self.attachment = self.env["ir.attachment"]
        self.startPatcher(patch.object(cache_module, "_EVICTING", set()))

    def _add(self, name, size, reads, days_ago=0, minutes_ago=60, in_bucket=True, local=True, bucket=True):
        """A cache row with its local file and its bucket object."""
        now = fields.Datetime.now()
        self.cache.create(
            {
                "name": name,
                "size": size,
                "reads": reads,
                "reads_date": now - datetime.timedelta(days=days_ago),
                "last_read": now - datetime.timedelta(minutes=minutes_ago),
                "in_bucket": in_bucket,
            }
        )
        if local:
            path = self.attachment._full_path(name)
            os.makedirs(os.path.dirname(path), exist_ok=True)
            with open(path, "wb") as local_file:
                local_file.write(b"x" * size)
        if bucket:
            self.fake.objects[name] = b"x" * size

    def _local(self, name):
        return os.path.exists(self.attachment._full_path(name))

    def _row(self, name):
        return self.cache.search([("name", "=", name)])

    def test_lowest_keep_value_goes_first(self):
        """The D21 example at 1 MB: the 10 MB PDF (here 100 KB) is the first to go, the icon the last."""
        self._add("ab/big", 100000, 3)
        self._add("ab/mid", 2000, 1)
        self._add("ab/icon", 200, 1, days_ago=3)
        # A popular file that makes the cache full; its keep value is the highest.
        self._add("ab/popular", 946500, 1000)
        with patch.object(self.fake, "head", wraps=self.fake.head) as head:
            self.assertEqual(self.cache._cloud_filestore_cache_evict(), 4)
        self.assertEqual([call.args[0] for call in head.call_args_list], ["ab/big", "ab/mid", "ab/icon", "ab/popular"])
        self.assertFalse(self.cache.search([]))

    def test_eviction_stops_at_90_percent(self):
        for index in range(12):  # 1.2 MB in a cache of 1 MB; 3 files give 0.9 MB, under the 90% of 943718 bytes
            self._add("ab/file%02d" % index, 100000, index + 1)
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 3)
        # The lowest read counts went; the others stay.
        self.assertEqual(sorted(self.cache.search([]).mapped("name"))[0], "ab/file03")
        self.assertFalse(self._local("ab/file00") or self._local("ab/file01") or self._local("ab/file02"))
        self.assertTrue(self._local("ab/file03"))
        # A cache below its limit is left alone.
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 0)

    def test_recently_read_row_stays(self):
        self._add("ab/recent", 600000, 0, minutes_ago=1)
        self._add("ab/old", 600000, 5)
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 1)
        self.assertTrue(self._local("ab/recent"))
        self.assertTrue(self._row("ab/recent").in_bucket)
        self.assertFalse(self._local("ab/old"))

    def test_missing_or_different_object_clears_the_mark_and_keeps_the_file(self):
        self._add("ab/gone", 1100000, 0, bucket=False)
        self._add("ab/other", 1100000, 1)
        self.fake.objects["ab/other"] = b"another size"
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 0)
        for name in ("ab/gone", "ab/other"):
            self.assertTrue(self._local(name), "the local file may be the only copy")
            self.assertFalse(self._row(name).in_bucket, "the upload job picks it up again")
        self.assertEqual(self.fake.deletes, [])

    def test_bucket_outage_evicts_nothing(self):
        self._add("ab/one", 600000, 0)
        self._add("ab/two", 600000, 1)
        self.fake.down = True
        with self.assertLogs(cache_module.__name__, "WARNING"):
            self.assertEqual(self.cache._cloud_filestore_cache_evict(), 0)
        self.assertTrue(self._local("ab/one") and self._local("ab/two"))
        self.assertTrue(self._row("ab/one").in_bucket and self._row("ab/two").in_bucket)

    def test_rows_of_vanished_files_are_deleted(self):
        self._add("ab/vanished", 10, 1, local=False)
        self._add("ab/kept", 10, 1)
        self._add("ab/unmarked", 10, 1, in_bucket=False, local=False)
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 0)
        self.assertFalse(self._row("ab/vanished"))
        self.assertTrue(self._row("ab/kept"))
        self.assertTrue(self._row("ab/unmarked"), "a row without the mark is not a cache copy; the cron leaves it")

    def test_local_only_name_is_never_evicted(self):
        """A mark that a local-only file shares (marked before a module installed it) is cleared (I3)."""
        data = b"x" * 1100000
        shared = self.attachment.create(
            {"name": "icon.bin", "raw": data, "cloud_local_only": True}
        ).store_fname
        self._add(shared, len(data), 0)
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 0)
        self.assertTrue(self._local(shared))
        self.assertFalse(self._row(shared).in_bucket)

    def test_unmarked_files_are_never_evicted(self):
        self._add("ab/own", 1200000, 0, in_bucket=False)
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 0)
        self.assertTrue(self._local("ab/own"))

    def test_mode_off_does_nothing(self):
        self._add("ab/one", 1200000, 0)
        self.mode = "off"
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 0)
        self.assertTrue(self._local("ab/one"))
        self.assertTrue(self._row("ab/one"))

    def test_limit_zero_evicts_the_leftover_copies(self):
        """A limit of 0 turns the cache off: the copies that are left go, except a file read a moment ago."""
        patch_read_cache(self, "0")
        self._add("ab/old", 10, 1)
        self._add("ab/recent", 10, 1, minutes_ago=1)
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 1)
        self.assertFalse(self._local("ab/old"))
        self.assertTrue(self._local("ab/recent"))

    def test_cron_run_commits_in_chunks_and_reports_progress(self):
        """Each call handles one chunk and reports the candidates left, so Odoo calls the job again."""
        for index in range(12):  # 1.2 MB: 3 files must go
            self._add("ab/file%02d" % index, 100000, index + 1)
        cron = self.env.ref("oteny_cloud_filestore.ir_cron_cloud_filestore_evict")
        as_cron = self.cache.with_context(cron_id=cron.id)
        progress_patch = patch.object(type(self.env["ir.cron"]), "_commit_progress", return_value=0.0)
        with progress_patch as progress:
            self.assertEqual(as_cron._cloud_filestore_cache_evict(limit=2), 2)
        progress.assert_called_once_with(2, remaining=1)  # the third file is still needed
        with progress_patch as progress:
            self.assertEqual(as_cron._cloud_filestore_cache_evict(limit=2), 1)
        progress.assert_called_once_with(1, remaining=0)
        # A run by hand reports nothing; the caller commits.
        with progress_patch as progress:
            self.assertEqual(self.cache._cloud_filestore_cache_evict(limit=2), 0)
        progress.assert_not_called()

    def test_call_stops_at_the_time_budget(self):
        for index in range(12):
            self._add("ab/file%02d" % index, 100000, index + 1)
        clock = MagicMock()
        clock.monotonic.side_effect = itertools.chain([0, 0], itertools.repeat(10_000))
        with patch.object(cache_module, "time", clock):
            self.assertEqual(self.cache._cloud_filestore_cache_evict(), 1)
        self.assertEqual(self.cache._cloud_filestore_cache_evict(), 2)

    def test_cron_calls_the_eviction(self):
        cron = self.env.ref("oteny_cloud_filestore.ir_cron_cloud_filestore_evict")
        self.assertEqual(cron.model_id.model, "oteny.cloud.filestore.cache")
        self.assertIn("_cloud_filestore_cache_evict", cron.code)
        self.assertEqual((cron.interval_number, cron.interval_type), (1, "hours"))


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreCacheHttp(CloudFilestoreReadMixin, HttpCase):
    """A small bucket-only file is downloaded into the cache once, then sent from local disk (D18)."""

    def setUp(self):
        super().setUp()
        self._patch_cloud_filestore()
        # Odoo's own stream finds a local file through config.filestore(): point it at the test folder.
        filestore = self.env["ir.attachment"]._filestore()
        self.startPatcher(patch.object(config, "filestore", return_value=filestore))
        self.authenticate("admin", "admin")

    def test_second_download_comes_from_local_disk(self):
        attachment = self._bucket_only_attachment(b"read once from the bucket")
        self.env["ir.config_parameter"].sudo().set_param("oteny_cloud_filestore.cache_limit_mb", "1")
        with patch.object(self.fake, "get", wraps=self.fake.get) as get:
            first = self.url_open(f"/web/content/{attachment.id}?download=true")
            second = self.url_open(f"/web/content/{attachment.id}?download=true")
        get.assert_not_called()
        self.assertEqual(len(self.fake.opened), 1, "the second download never asks the bucket")
        self.assertTrue(self.fake.opened[0].closed)
        self.assertEqual(first.status_code, 200)
        self.assertEqual(first.content, b"read once from the bucket")
        self.assertEqual(first.headers["X-Cloud-Filestore"], "bucket")
        self.assertEqual(second.content, b"read once from the bucket")
        self.assertEqual(second.headers["X-Cloud-Filestore"], "local")
        self.assertEqual(ir_attachment_module._CACHE_READS[self.env.cr.dbname][attachment.store_fname], 2)

    def test_fill_goes_to_disk_in_chunks(self):
        """The cache fill of a download never holds the whole file: it writes chunk by chunk (I2)."""
        raw = b"C" * 200000  # four chunks of 65536 bytes or less; a quarter of 1 MB is 262144 bytes
        attachment = self._bucket_only_attachment(raw)
        self.env["ir.config_parameter"].sudo().set_param("oteny_cloud_filestore.cache_limit_mb", "1")
        model = type(attachment)
        writes = []
        real_write = model._cloud_filestore_write_new_file

        def record_write(attachment_model, path, data, check_sha1=False):
            writes.append(type(data))
            return real_write(attachment_model, path, data, check_sha1=check_sha1)

        with patch.object(self.fake, "get", wraps=self.fake.get) as get, patch.object(
            model, "_cloud_filestore_write_new_file", record_write
        ):
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        get.assert_not_called()
        self.assertNotIn(bytes, writes, "the fill got the open object's chunks, not one block of bytes")
        self.assertEqual(self.fake.opened[0].chunks_read, 4)
        self.assertEqual(response.content, raw)
        self.assertTrue(os.path.exists(attachment._full_path(attachment.store_fname)))

    def test_damaged_object_is_sent_but_never_cached(self):
        """Wrong bytes give no cache copy; the download falls back to the chunked stream (I2)."""
        attachment = self._bucket_only_attachment(b"the right bytes")
        self.env["ir.config_parameter"].sudo().set_param("oteny_cloud_filestore.cache_limit_mb", "1")
        self.fake.objects[attachment.store_fname] = b"the wrong bytes"
        with self.assertLogs(LOGGER, "WARNING"):
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"the wrong bytes")
        self.assertFalse(os.path.exists(attachment._full_path(attachment.store_fname)))
        self.assertEqual(len(self.fake.opened), 2, "the fallback opens the object again")
        self.assertTrue(all(fake_object.closed for fake_object in self.fake.opened))

    def test_download_that_breaks_off_during_the_fill_is_404(self):
        attachment = self._bucket_only_attachment(b"B" * 200000)
        self.env["ir.config_parameter"].sudo().set_param("oteny_cloud_filestore.cache_limit_mb", "1")
        self.fake.fail_after_chunks = 1
        with patch.object(type(attachment), "_cloud_filestore_alert") as alert, self.assertLogs(
            LOGGER, "WARNING"
        ):
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(alert.call_args.args[0], "_bucket_failed")
        self.assertFalse(os.path.exists(attachment._full_path(attachment.store_fname)))
        filestore = self.env["ir.attachment"]._filestore()
        self.assertFalse(
            [
                name
                for _folder, _dirs, files in os.walk(filestore)
                for name in files
                if name.startswith(".cfs-")
            ]
        )

    def test_large_file_is_still_streamed_in_chunks(self):
        # A quarter of 1 MB is 262144 bytes.
        raw = b"L" * 300000
        attachment = self._bucket_only_attachment(raw)
        self.env["ir.config_parameter"].sudo().set_param("oteny_cloud_filestore.cache_limit_mb", "1")
        with patch.object(self.fake, "get", wraps=self.fake.get) as get:
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        get.assert_not_called()
        self.assertEqual(response.content, raw)
        self.assertEqual(response.headers["X-Cloud-Filestore"], "bucket")
        self.assertGreater(self.fake.opened[-1].chunks_read, 1)
        self.assertFalse(os.path.exists(attachment._full_path(attachment.store_fname)))

    def test_cacheable_file_missing_in_bucket_is_404(self):
        attachment = self._bucket_only_attachment(b"lost in the bucket")
        self.env["ir.config_parameter"].sudo().set_param("oteny_cloud_filestore.cache_limit_mb", "1")
        self.fake.objects.clear()
        with self.assertLogs("odoo.addons.oteny_cloud_filestore", level="WARNING"):
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        self.assertEqual(response.status_code, 404)
