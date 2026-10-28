"""MinIO through the S3 API with boto3 (standard ch. 17; IDP-00 ch. 7.7). Java's team runs the server.

The size is checked with a HEAD request before anything is downloaded (FIL-05). Documents are read into memory
because the pipeline works on bytes; the size limit keeps that bounded.
"""

import io
from typing import TYPE_CHECKING

from botocore.exceptions import ClientError
from PIL import Image

from extraction.pipeline.domain.exceptions import InputMissingError, TooLargeError
from extraction.pipeline.domain.jobs import ObjectRef
from idp_ocr_engines.model import PageImage

if TYPE_CHECKING:  # boto3-stubs is a dev dependency: type hints only, not in the images
    from mypy_boto3_s3 import S3Client

_MISSING = {"404", "NoSuchKey", "NotFound"}
_PNG = "image/png"


class S3ObjectStorage:
    def __init__(self, client: "S3Client", *, max_input_size: int) -> None:
        self._client = client
        self._max_input_size = max_input_size

    def read_bytes(self, ref: ObjectRef) -> bytes:
        try:
            head = self._client.head_object(Bucket=ref.bucket, Key=ref.key)
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:  # deleted, or a guest file that expired
                raise InputMissingError from exc
            raise  # anything else is the storage's problem: the task retries
        if head["ContentLength"] > self._max_input_size:
            raise TooLargeError(f"The file is larger than {self._max_input_size:,} bytes.")
        return self._client.get_object(Bucket=ref.bucket, Key=ref.key)["Body"].read()

    def write_bytes(self, ref: ObjectRef, data: bytes, content_type: str) -> None:
        self._client.put_object(Bucket=ref.bucket, Key=ref.key, Body=data, ContentType=content_type)

    def save_page(self, ref: ObjectRef, page: PageImage) -> None:
        """``PageImageStorePort``: the page as a PNG, lossless, for reviewers and export reports."""
        buffer = io.BytesIO()
        Image.frombytes("RGB", (page.width, page.height), page.rgb).save(buffer, format="PNG", optimize=True)
        self.write_bytes(ref, buffer.getvalue(), _PNG)
