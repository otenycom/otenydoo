import base64
import hashlib
import time
from unittest.mock import patch

import requests

from odoo.tests import HttpCase, tagged

from ..models.ir_attachment import CloudObjectStream
from .test_read import CloudFilestoreReadMixin

CHUNK = CloudObjectStream.chunk_size
# A one-pixel PNG, to see that the image resize still works on a bucket-only file.
PNG = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
LOGGER = "odoo.addons.oteny_cloud_filestore"


def wait_for(condition, seconds=5):
    """The HTTP worker finishes a request after the client has its answer: wait for its side effect."""
    deadline = time.monotonic() + seconds
    while not condition() and time.monotonic() < deadline:
        time.sleep(0.02)
    return condition()


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudObjectStream(CloudFilestoreReadMixin, HttpCase):
    """A bucket-only file goes out in chunks: the worker never holds all of it."""

    def setUp(self):
        super().setUp()
        self._patch_cloud_filestore()
        self.authenticate("admin", "admin")

    def test_download_in_chunks(self):
        raw = b"".join(bytes([n]) * CHUNK for n in (1, 2, 3)) + b"tail"
        attachment = self._bucket_only_attachment(raw)
        with patch.object(self.fake, "get", wraps=self.fake.get) as get:
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        get.assert_not_called()
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, raw)
        self.assertEqual(response.headers["Content-Length"], str(len(raw)))
        self.assertEqual(response.headers["X-Cloud-Filestore"], "bucket")
        # The headers Odoo sets for any download stay.
        self.assertIn("attachment", response.headers["Content-Disposition"])
        self.assertEqual(response.headers["X-Content-Type-Options"], "nosniff")
        self.assertEqual(response.headers["Content-Security-Policy"], "default-src 'none'")
        self.assertEqual(response.headers["ETag"], f'"{attachment.checksum}"')
        self.assertTrue(self.fake.opened[-1].closed)
        self.assertEqual(self.fake.opened[-1].chunks_read, 4)

    def test_range_request_gets_the_full_file(self):
        raw = b"abcdefghij" * 10
        attachment = self._bucket_only_attachment(raw)
        response = self.url_open(f"/web/content/{attachment.id}?download=true", headers={"Range": "bytes=0-9"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.content, raw)

    def test_store_down_or_object_missing_is_404(self):
        attachment = self._bucket_only_attachment(b"streamed from the bucket")
        self.fake.down = True
        with patch.object(type(attachment), "_cloud_filestore_alert"), self.assertLogs(LOGGER, level="WARNING"):
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        self.assertEqual(response.status_code, 404)
        self.assertNotIn("X-Cloud-Filestore", response.headers)
        self.fake.down = False
        self.fake.objects.clear()
        with patch.object(type(attachment), "_cloud_filestore_alert") as alert, self.assertLogs(
            LOGGER, level="WARNING"
        ):
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        self.assertEqual(response.status_code, 404)
        alert.assert_not_called()  # a lost object is not an outage

    def test_store_down_alerts_in_production(self):
        attachment = self._bucket_only_attachment(b"streamed from the bucket")
        self.fake.down = True
        with patch.object(type(attachment), "_cloud_filestore_alert") as alert, self.assertLogs(
            LOGGER, level="WARNING"
        ):
            response = self.url_open(f"/web/content/{attachment.id}?download=true")
        self.assertEqual(response.status_code, 404)
        self.assertEqual(alert.call_args.args[0], "_bucket_failed")

    def test_body_that_fails_is_never_a_complete_200(self):
        raw = b"x" * (3 * CHUNK)
        attachment = self._bucket_only_attachment(raw)
        self.fake.fail_after_chunks = 1
        received = None
        # The server logs the broken connection too: werkzeug "Error on request".
        with self.assertLogs("werkzeug", level="ERROR") as server_logs, self.assertLogs(
            LOGGER, level="WARNING"
        ) as logs:
            try:
                response = self.url_open(f"/web/content/{attachment.id}?download=true")
                received = response.content
            except requests.exceptions.RequestException:
                pass  # the connection broke: the browser sees a failed download
            wait_for(lambda: server_logs.records)  # werkzeug logs last
        self.assertTrue(any("broke off" in line and attachment.store_fname in line for line in logs.output))
        self.assertTrue(any("Error on request" in line for line in server_logs.output))
        if received is not None:
            self.assertLess(len(received), len(raw))
        self.assertTrue(self.fake.opened[-1].closed)

    def test_head_request_closes_the_object(self):
        """werkzeug sends no body for HEAD, so the body generator never starts."""
        raw = b"z" * (2 * CHUNK)
        attachment = self._bucket_only_attachment(raw)
        response = self.url_open(f"/web/content/{attachment.id}?download=true", method="HEAD")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["Content-Length"], str(len(raw)))
        fake_object = self.fake.opened[-1]
        self.assertEqual(fake_object.chunks_read, 0)
        self.assertTrue(wait_for(lambda: fake_object.closed))

    def test_not_modified_never_reads_the_body(self):
        attachment = self._bucket_only_attachment(b"x" * (2 * CHUNK))
        response = self.url_open(
            f"/web/content/{attachment.id}?download=true", headers={"If-None-Match": f'"{attachment.checksum}"'}
        )
        self.assertEqual(response.status_code, 304)
        fake_object = self.fake.opened[-1]
        self.assertEqual(fake_object.chunks_read, 0)
        self.assertTrue(fake_object.closed)

    def test_image_resize_reads_the_whole_object(self):
        attachment = self.env["ir.attachment"].create({"name": "pixel.png", "raw": PNG, "mimetype": "image/png"})
        response = self.url_open(f"/web/image/{attachment.id}?width=64")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.headers["Content-Type"].startswith("image/"))
        self.assertTrue(response.content.startswith(b"\x89PNG"))
        self.assertTrue(self.fake.opened[-1].closed)

    def test_read_and_data_return_all_bytes(self):
        raw = b"y" * (2 * CHUNK + 5)
        attachment = self._bucket_only_attachment(raw)
        stream = attachment._to_http_stream()
        self.assertEqual(stream.size, len(raw))
        self.assertEqual(stream.read(), raw)
        self.assertEqual(stream.data, raw)
        self.assertEqual(hashlib.sha1(stream.data).hexdigest(), attachment.checksum)
