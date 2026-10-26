"""MinIO through the S3 API with boto3 (standard ch. 17; IDP-00 ch. 7.7). Java's team runs the server.

Files stream between disk and MinIO in chunks (``download_file`` / ``upload_file`` use multipart transfers), so
a large file never sits whole in memory (FIL-07). The size is checked before downloading (FIL-05).
"""

import hashlib
from pathlib import Path
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError

from conversion.conversion.domain.exceptions import InvalidRequestError, LimitExceededError
from conversion.conversion.domain.ports import UploadedObject
from conversion.conversion.domain.values import ObjectRef

if TYPE_CHECKING:  # boto3-stubs is a dev dependency: type hints only, not in the images
    from mypy_boto3_s3 import S3Client

_CHUNK = 1024 * 1024
_MISSING = {"404", "NoSuchKey", "NotFound"}


class S3ObjectStorage:
    def __init__(self, client: "S3Client", *, max_input_size: int) -> None:
        self._client = client
        self._max_input_size = max_input_size

    def download(self, ref: ObjectRef, destination: Path) -> None:
        try:
            head = self._client.head_object(Bucket=ref.bucket, Key=ref.key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:  # deleted, or a guest file that expired
                raise InvalidRequestError("An input file no longer exists in storage.") from exc
            raise  # anything else is the storage's problem: the task retries
        if head["ContentLength"] > self._max_input_size:
            raise LimitExceededError(f"The file is larger than {self._max_input_size:,} bytes.")
        self._client.download_file(ref.bucket, ref.key, str(destination))

    def upload(self, source: Path, ref: ObjectRef, content_type: str) -> UploadedObject:
        digest = hashlib.sha256()
        with source.open("rb") as stream:
            while chunk := stream.read(_CHUNK):
                digest.update(chunk)
        self._client.upload_file(str(source), ref.bucket, ref.key, ExtraArgs={"ContentType": content_type})
        return UploadedObject(size=source.stat().st_size, sha256=digest.hexdigest())
