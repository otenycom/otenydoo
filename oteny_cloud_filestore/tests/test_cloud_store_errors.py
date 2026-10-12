import io
from unittest.mock import MagicMock

from botocore.exceptions import (
    ClientError,
    EndpointConnectionError,
    IncompleteReadError,
    NoCredentialsError,
    ProxyConnectionError,
    ReadTimeoutError,
    SSLError,
)

from odoo.tests import BaseCase, tagged

from ..tools.cloud_store import CloudStore, CloudStoreError, CloudStoreUnavailable


def _client_error(status, code, operation="Op"):
    return ClientError(
        {"Error": {"Code": code, "Message": "x"}, "ResponseMetadata": {"HTTPStatusCode": status}},
        operation,
    )


@tagged("oteny_cloud_filestore", "post_install", "-at_install")
class TestCloudStoreErrors(BaseCase):
    """Error mapping of CloudStore, with a fake boto3 client (no network)."""

    def _store(self, **failures):
        """A store whose client methods raise the given exceptions."""
        store = CloudStore("http://127.0.0.1:9", "garage", "bucket", "k", "s")
        store._client = MagicMock()
        for method, error in failures.items():
            getattr(store._client, method).side_effect = error
        return store

    def test_missing_key_is_none_for_get_and_head(self):
        for code, status in (("NoSuchKey", 404), ("NotFound", 404), ("404", 404)):
            store = self._store(
                get_object=_client_error(status, code), head_object=_client_error(status, code)
            )
            self.assertIsNone(store.get("ab/abc"), code)
            self.assertIsNone(store.head("ab/abc"), code)

    def test_missing_bucket_is_an_error_everywhere(self):
        error = _client_error(404, "NoSuchBucket")
        store = self._store(
            put_object=error, get_object=error, head_object=error, delete_object=error
        )
        for call in (
            lambda: store.put("ab/abc", b"x"),
            lambda: store.get("ab/abc"),
            lambda: store.head("ab/abc"),
            lambda: store.delete("ab/abc"),
        ):
            with self.assertRaises(CloudStoreError) as caught:
                call()
            self.assertNotIsInstance(caught.exception, CloudStoreUnavailable)

    def test_put_404_is_an_error_and_delete_missing_key_is_silent(self):
        store = self._store(
            put_object=_client_error(404, "NoSuchKey"),
            delete_object=_client_error(404, "NoSuchKey"),
        )
        with self.assertRaises(CloudStoreError):
            store.put("ab/abc", b"x")
        store.delete("ab/abc")

    def test_server_error_is_unavailable(self):
        store = self._store(head_object=_client_error(503, "ServiceUnavailable"))
        with self.assertRaises(CloudStoreUnavailable):
            store.head("ab/abc")

    def test_refusal_is_error_not_unavailable(self):
        store = self._store(put_object=_client_error(403, "AccessDenied"))
        with self.assertRaises(CloudStoreError) as caught:
            store.put("ab/abc", b"x")
        self.assertNotIsInstance(caught.exception, CloudStoreUnavailable)

    def test_connection_family_is_unavailable(self):
        errors = (
            EndpointConnectionError(endpoint_url="http://x"),
            ReadTimeoutError(endpoint_url="http://x"),
            SSLError(endpoint_url="http://x", error="bad"),
            ProxyConnectionError(proxy_url="http://x"),
        )
        for error in errors:
            store = self._store(head_object=error, put_object=error)
            with self.assertRaises(CloudStoreUnavailable, msg=type(error).__name__):
                store.head("ab/abc")
            with self.assertRaises(CloudStoreUnavailable, msg=type(error).__name__):
                store.put("ab/abc", b"x")

    def test_body_read_failure_is_unavailable(self):
        body = MagicMock()
        body.read.side_effect = IncompleteReadError(actual_bytes=1, expected_bytes=5)
        store = self._store()
        store._client.get_object.return_value = {"Body": body}
        with self.assertRaises(CloudStoreUnavailable):
            store.get("ab/abc")

    def test_get_returns_body(self):
        store = self._store()
        store._client.get_object.return_value = {"Body": io.BytesIO(b"hello")}
        self.assertEqual(store.get("ab/abc"), b"hello")

    def test_other_botocore_error_is_error(self):
        store = self._store(put_object=NoCredentialsError())
        with self.assertRaises(CloudStoreError) as caught:
            store.put("ab/abc", b"x")
        self.assertNotIsInstance(caught.exception, CloudStoreUnavailable)
