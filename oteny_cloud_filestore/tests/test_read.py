import os
import shutil
import tempfile
from unittest.mock import patch

from odoo.tests import HttpCase, TransactionCase, tagged

from .common import FakeCloudStore


class CloudFilestoreReadMixin:
    """Patch the mode, the store and the filestore directory of ir.attachment.

    The patches sit on the model class, so a test in another thread of the same
    process (the HTTP worker) sees them too.
    """

    def _patch_cloud_filestore(self, mode="production"):
        filestore = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, filestore, ignore_errors=True)
        model = type(self.env["ir.attachment"])
        self.fake = FakeCloudStore()
        self.mode = mode
        for name, patcher in (
            ("_filestore", patch.object(model, "_filestore", return_value=filestore)),
            ("_cloud_filestore_mode", patch.object(model, "_cloud_filestore_mode", side_effect=lambda: self.mode)),
            ("_cloud_filestore_store", patch.object(model, "_cloud_filestore_store", return_value=self.fake)),
            ("_storage", patch.object(model, "_storage", return_value="file")),
        ):
            patcher.start()
            self.addCleanup(patcher.stop)

    def _bucket_only_attachment(self, raw=b"bucket content"):
        """Save in production mode: the file goes to the fake and the local copy is removed."""
        attachment = self.env["ir.attachment"].create({"name": "user.bin", "raw": raw, "mimetype": "text/plain"})
        self.assertFalse(os.path.exists(attachment._full_path(attachment.store_fname)))
        return attachment


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreRead(CloudFilestoreReadMixin, TransactionCase):
    def setUp(self):
        super().setUp()
        self._patch_cloud_filestore()

    def test_read_from_bucket_when_not_local(self):
        attachment = self._bucket_only_attachment()
        self.assertEqual(attachment.raw, b"bucket content")

    def test_read_prefers_local(self):
        attachment = self._bucket_only_attachment()
        path = attachment._full_path(attachment.store_fname)
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "wb") as local_file:
            local_file.write(b"local content")
        self.fake.objects[attachment.store_fname] = b"other content"
        with patch.object(self.fake, "get", wraps=self.fake.get) as get:
            self.assertEqual(attachment.raw, b"local content")
        get.assert_not_called()

    def test_read_size_limit(self):
        attachment = self._bucket_only_attachment(b"abcdef")
        self.assertEqual(attachment._file_read(attachment.store_fname, size=3), b"abc")

    def test_read_missing_returns_empty(self):
        attachment = self._bucket_only_attachment()
        self.fake.objects.clear()
        self.assertEqual(attachment._file_read(attachment.store_fname), b"")

    def test_read_failure_returns_empty_and_alerts(self):
        attachment = self._bucket_only_attachment()
        self.fake.down = True
        with patch.object(type(attachment), "_cloud_filestore_alert") as alert, self.assertLogs(
            "odoo.addons.oteny_cloud_filestore.models.ir_attachment", level="WARNING"
        ):
            self.assertEqual(attachment._file_read(attachment.store_fname), b"")
        alert.assert_called_once()
        self.assertEqual(alert.call_args.args[0], "_bucket_failed")

    def test_read_failure_in_clone_only_logs(self):
        attachment = self._bucket_only_attachment()
        self.mode = "clone"
        self.fake.down = True
        with patch.object(type(attachment), "_cloud_filestore_alert") as alert, self.assertLogs(
            "odoo.addons.oteny_cloud_filestore.models.ir_attachment", level="WARNING"
        ):
            self.assertEqual(attachment._file_read(attachment.store_fname), b"")
        alert.assert_not_called()


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudFilestoreHttp(CloudFilestoreReadMixin, HttpCase):
    def test_http_stream_from_bucket(self):
        self._patch_cloud_filestore()
        attachment = self._bucket_only_attachment(b"streamed from the bucket")
        self.authenticate("admin", "admin")
        with patch.object(self.fake, "get", wraps=self.fake.get) as get:
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        get.assert_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"streamed from the bucket")

    def test_http_stream_bucket_failure_is_404(self):
        """An empty body must never be sent with the real etag: a cache would keep it."""
        self._patch_cloud_filestore()
        attachment = self._bucket_only_attachment(b"streamed from the bucket")
        self.authenticate("admin", "admin")
        logger = "odoo.addons.oteny_cloud_filestore.models.ir_attachment"
        self.fake.objects.clear()
        response = self.url_open(f"/web/content/{attachment.id}?download=true")
        self.assertEqual(response.status_code, 404)
        self.fake.objects[attachment.store_fname] = b"streamed from the bucket"
        self.fake.down = True
        with self.assertLogs(logger, level="WARNING"):
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        self.assertEqual(response.status_code, 404)

    def test_http_stream_genuinely_empty_file_is_200(self):
        self._patch_cloud_filestore()
        # Odoo keeps no store_fname for empty content, so build the case by hand.
        attachment = self._bucket_only_attachment(b"x")
        # write() ignores file_size, so set it in SQL; the HTTP worker has its own cache.
        self.env.cr.execute("UPDATE ir_attachment SET file_size = 0 WHERE id = %s", [attachment.id])
        self.fake.objects[attachment.store_fname] = b""
        self.authenticate("admin", "admin")
        response = self.url_open(f"/web/content/{attachment.id}?download=true")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, b"")
