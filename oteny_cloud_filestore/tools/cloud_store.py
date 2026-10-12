import boto3
from botocore.config import Config
from botocore.exceptions import (
    ClientError,
    ConnectionClosedError,
    ConnectTimeoutError,
    EndpointConnectionError,
    ReadTimeoutError,
)


class CloudStoreError(Exception):
    """The store refused a request or gave an unexpected answer."""


class CloudStoreUnavailable(CloudStoreError):
    """The store cannot be reached: connection error, timeout or HTTP 5xx.

    Callers treat this as an outage and keep the file local, unlike a refusal
    (for example a key without the right), which is a configuration fault.
    """


_UNAVAILABLE_ERRORS = (
    EndpointConnectionError,
    ConnectTimeoutError,
    ReadTimeoutError,
    ConnectionClosedError,
)
_MISSING_CODES = {"404", "NoSuchKey", "NotFound"}


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
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            region_name=region,
            aws_access_key_id=access_key_id,
            aws_secret_access_key=secret_access_key,
            config=Config(
                connect_timeout=3,
                read_timeout=10,
                retries={"max_attempts": 2},
                s3={"addressing_style": addressing_style},
                signature_version="s3v4",
            ),
        )

    def _key(self, name):
        return self.key_prefix + name

    @staticmethod
    def _is_missing(error):
        code = str(error.response.get("Error", {}).get("Code", ""))
        status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode")
        return status == 404 or code in _MISSING_CODES

    def _call(self, method, **kwargs):
        """Run one client call and map every failure to our two exceptions.

        A missing object is not an error here: the caller gets None.
        """
        try:
            return getattr(self._client, method)(
                Bucket=self.bucket, Key=self._key(kwargs.pop("name")), **kwargs
            )
        except _UNAVAILABLE_ERRORS as error:
            raise CloudStoreUnavailable(f"{method}: store unreachable") from error
        except ClientError as error:
            status = error.response.get("ResponseMetadata", {}).get("HTTPStatusCode") or 0
            if status >= 500:
                raise CloudStoreUnavailable(f"{method}: store answered {status}") from error
            if self._is_missing(error):
                return None
            code = error.response.get("Error", {}).get("Code")
            raise CloudStoreError(f"{method}: store refused ({status} {code})") from error

    def put(self, name, data):
        self._call("put_object", name=name, Body=data)

    def get(self, name):
        response = self._call("get_object", name=name)
        if response is None:
            return None
        try:
            return response["Body"].read()
        except _UNAVAILABLE_ERRORS as error:
            raise CloudStoreUnavailable("get_object: store unreachable") from error

    def head(self, name):
        response = self._call("head_object", name=name)
        return None if response is None else response["ContentLength"]

    def delete(self, name):
        # S3 answers 204 for a missing key, so no special case is needed.
        self._call("delete_object", name=name)
