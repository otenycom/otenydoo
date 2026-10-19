import boto3
from botocore.config import Config
from botocore.exceptions import (
    BotoCoreError,
    ClientError,
    ConnectionError,
    IncompleteReadError,
    ReadTimeoutError,
    ResponseStreamingError,
)


class CloudObject:
    """An open object of the bucket: its size and its body in chunks."""

    def __init__(self, body, size):
        self.size = size
        self._body = body
        self._closed = False

    def iter_chunks(self, chunk_size=65536):
        """Yield the body; a failure while reading maps to our exceptions, like `get`."""
        try:
            yield from self._body.iter_chunks(chunk_size)
        except _UNAVAILABLE_ERRORS as error:
            raise CloudStoreUnavailable("get_object: store unreachable") from error
        except BotoCoreError as error:
            raise CloudStoreError(f"get_object: {type(error).__name__}") from error

    def close(self):
        # Safe to call twice: a stream closes the object from more than one place.
        if not self._closed:
            self._closed = True
            self._body.close()


# The S3 limit of one delete_objects request.
DELETE_MANY_MAX = 1000


class CloudStoreError(Exception):
    """The store refused a request or gave an unexpected answer."""


class CloudStoreUnavailable(CloudStoreError):
    """The store cannot be reached: connection error, timeout or HTTP 5xx.

    Callers treat this as an outage and keep the file local, unlike a refusal
    (for example a key without the right), which is a configuration fault.
    """


class CloudStoreAccessError(CloudStoreError):
    """The store refuses the key or the bucket for every file.

    Examples: a key without the right, a revoked key, a missing bucket. This
    is a fault of the whole store, not of one file, so callers treat it like
    an outage. The text of this error never holds a key.
    """


# The whole connection family (SSL, proxy, endpoint, connect timeout, ...)
# plus the read-side failures: all mean "cannot reach the store right now".
_UNAVAILABLE_ERRORS = (
    ConnectionError,
    ReadTimeoutError,
    ResponseStreamingError,
    IncompleteReadError,
)
# Codes that mean "this key does not exist". NoSuchBucket is deliberately
# absent: a mistyped bucket must never look like an empty store.
_MISSING_KEY_CODES = {"NoSuchKey", "NotFound", "404"}
# Codes that mean "the key or the bucket is refused", whatever the HTTP status.
_ACCESS_ERROR_CODES = {
    "AccessDenied",
    "InvalidAccessKeyId",
    "SignatureDoesNotMatch",
    "NoSuchBucket",
    "AllAccessDisabled",
    "InvalidBucketName",
}


class CloudStore:
    """Small S3 client: four calls on file contents, addressed by store_fname.

    The object key in the bucket is `key_prefix + name`. The client never
    logs a key, and short timeouts keep a dead bucket from stalling a request.
    """

    def __init__(
        self,
        endpoint_url,
        region,
        bucket,
        access_key_id,
        secret_access_key,
        addressing_style="path",
        key_prefix="",
    ):
        self.bucket = bucket
        self.key_prefix = key_prefix
        # A client of its own session: the default boto3 session is not
        # thread-safe, and Odoo serves requests from several threads.
        self._client = boto3.session.Session().client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                # Two attempts in total; botocore's max_attempts would count retries only.
                retries={"total_max_attempts": 2},
                s3={"addressing_style": addressing_style},
                signature_version="s3v4",
                # Some S3-compatible stores refuse the flexible checksums that
                # botocore sends by default; use them only where S3 needs them.
                request_checksum_calculation="when_required",
                response_checksum_validation="when_required",
            ),
        )

    def _key(self, name):
        return self.key_prefix + name

    def _call(self, method, name, missing_ok=False, **kwargs):
        """Run one client call and map every failure to our exceptions.

        With missing_ok, a missing key gives None instead of an error. Only
        get, head and delete may pass it: a put to a missing key or bucket
        must fail, or the caller would think the file is stored. A call on
        no single key (delete_objects) passes name=None.
        """
        if name is not None:
            kwargs["Key"] = self._key(name)
        try:
            return getattr(self._client, method)(Bucket=self.bucket, **kwargs)
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0
            code = str(error.response.get("Error", {}).get("Code", ""))
            if status >= 500:
                raise CloudStoreUnavailable(f"{method}: store answered {status}") from error
            if missing_ok and code in _MISSING_KEY_CODES:
                return None
            if status in (401, 403) or code in _ACCESS_ERROR_CODES:
                raise CloudStoreAccessError(f"{method}: store refused ({status} {code})") from error
            raise CloudStoreError(f"{method}: store refused ({status} {code})") from error
        except _UNAVAILABLE_ERRORS as error:
            raise CloudStoreUnavailable(f"{method}: store unreachable") from error
        except BotoCoreError as error:
            raise CloudStoreError(f"{method}: {type(error).__name__}") from error

    def put(self, name, data):
        self._call("put_object", name, Body=data)

    def get(self, name):
        response = self._call("get_object", name, missing_ok=True)
        if response is None:
            return None
        try:
            return response["Body"].read()
        except _UNAVAILABLE_ERRORS as error:
            raise CloudStoreUnavailable("get_object: store unreachable") from error
        except BotoCoreError as error:
            raise CloudStoreError(f"get_object: {type(error).__name__}") from error

    def open(self, name):
        """Open an object to read in chunks; None when the key is missing.

        `get` holds the whole object in memory. A download of a large file
        uses this instead, so the worker holds one chunk at a time.
        """
        response = self._call("get_object", name, missing_ok=True)
        if response is None:
            return None
        return CloudObject(response["Body"], response["ContentLength"])

    def head(self, name):
        response = self._call("head_object", name, missing_ok=True)
        return None if response is None else response["ContentLength"]

    def delete(self, name):
        # A missing key is silent; a missing bucket still raises.
        self._call("delete_object", name, missing_ok=True)

    def delete_many(self, names):
        """Delete up to 1000 objects in one request; return the names that were NOT deleted.

        The bucket answers a batch with one status, and lists the keys it
        failed on under `Errors`. A name that is missing is not an error.
        """
        if len(names) > DELETE_MANY_MAX:
            raise ValueError(f"delete_many takes at most {DELETE_MANY_MAX} names")
        if not names:
            return []
        response = self._call(
            "delete_objects",
            None,
            Delete={"Objects": [{"Key": self._key(name)} for name in names], "Quiet": True},
        )
        prefix_length = len(self.key_prefix)
        return [error["Key"][prefix_length:] for error in response.get("Errors", [])]
