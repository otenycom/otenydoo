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

from ..tools import cloud_store
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

    def test_delete_many_request_and_errors_answer(self):
        store = self._store()
        store.key_prefix = "dev/"
        store._client.delete_objects.return_value = {"Errors": [{"Key": "dev/ab/bad", "Code": "AccessDenied"}]}
        self.assertEqual(store.delete_many(["ab/good", "ab/bad"]), ["ab/bad"])
        store._client.delete_objects.assert_called_once_with(
            Bucket="bucket",
            Delete={"Objects": [{"Key": "dev/ab/good"}, {"Key": "dev/ab/bad"}], "Quiet": True},
        )
        # A clean answer has no `Errors`; an empty list makes no call; a 5xx is an outage.
        store._client.delete_objects.reset_mock(return_value=True)
        store._client.delete_objects.return_value = {}
        self.assertEqual(store.delete_many(["ab/good"]), [])
        store._client.delete_objects.reset_mock()
        self.assertEqual(store.delete_many([]), [])
        store._client.delete_objects.assert_not_called()
        down = self._store(delete_objects=_client_error(503, "ServiceUnavailable"))
        with self.assertRaises(CloudStoreUnavailable):
            down.delete_many(["ab/good"])

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

    def test_store_wide_refusals_are_access_errors(self):
        """A key, rights or bucket fault concerns every file: it must alert."""
        cases = (
            (403, "AccessDenied"),
            (403, "InvalidAccessKeyId"),
            (403, "SignatureDoesNotMatch"),
            (404, "NoSuchBucket"),
            (401, "Unauthorized"),
            (400, "InvalidBucketName"),
            (400, "AllAccessDisabled"),
        )
        for status, code in cases:
            error = _client_error(status, code)
            store = self._store(put_object=error, get_object=error, head_object=error, delete_object=error)
            for call in (
                lambda: store.put("ab/abc", b"x"),
                lambda: store.get("ab/abc"),
                lambda: store.head("ab/abc"),
                lambda: store.delete("ab/abc"),
            ):
                with self.assertRaises(cloud_store.CloudStoreAccessError, msg=code) as caught:
                    call()
                self.assertNotIsInstance(caught.exception, CloudStoreUnavailable)

    def test_missing_key_stays_none_next_to_access_errors(self):
        store = self._store(
            get_object=_client_error(404, "NoSuchKey"), head_object=_client_error(404, "NoSuchKey")
        )
        self.assertIsNone(store.get("ab/abc"))
        self.assertIsNone(store.head("ab/abc"))

    def test_per_object_refusal_is_a_plain_error(self):
        store = self._store(put_object=_client_error(400, "EntityTooLarge"))
        with self.assertRaises(CloudStoreError) as caught:
            store.put("ab/abc", b"x")
        self.assertNotIsInstance(caught.exception, (cloud_store.CloudStoreAccessError, CloudStoreUnavailable))

    def test_open_gives_size_and_none_for_a_missing_key(self):
        store = self._store()
        # A stand-in for botocore's StreamingBody.
        body = MagicMock()
        body.iter_chunks.return_value = iter([b"ab", b"c"])
        store._client.get_object.return_value = {"ContentLength": 3, "Body": body}
        opened = store.open("ab/abc")
        self.assertEqual(opened.size, 3)
        self.assertEqual(list(opened.iter_chunks(2)), [b"ab", b"c"])
        opened.close()
        body.close.assert_called_once()
        store = self._store(get_object=_client_error(404, "NoSuchKey"))
        self.assertIsNone(store.open("ab/abc"))

    def test_open_body_failure_maps_like_get(self):
        for raised, expected in (
            (ReadTimeoutError(endpoint_url="x"), CloudStoreUnavailable),
            (IncompleteReadError(actual_bytes=1, expected_bytes=2), CloudStoreUnavailable),
        ):
            store = self._store()
            body = MagicMock()
            body.iter_chunks.side_effect = raised
            store._client.get_object.return_value = {"ContentLength": 3, "Body": body}
            with self.assertRaises(expected):
                list(store.open("ab/abc").iter_chunks())
