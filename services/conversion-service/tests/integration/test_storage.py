"""MinIO through the S3 API: files go up and come back unchanged, and missing or oversized inputs are refused."""

import hashlib
from pathlib import Path

import pytest
from mypy_boto3_s3 import S3Client

from conversion.bootstrap import build_s3_client
from conversion.conversion.domain.exceptions import InvalidRequestError, LimitExceededError
from conversion.conversion.domain.values import ObjectRef
from conversion.conversion.infrastructure.storage import S3ObjectStorage
from conversion.core.config import StorageSettings


@pytest.fixture
def storage(storage_settings: StorageSettings) -> S3ObjectStorage:
    return S3ObjectStorage(build_s3_client(storage_settings), max_input_size=1024)


def test_uploads_a_file_with_its_hash_and_downloads_it_unchanged(
    storage: S3ObjectStorage, s3: S3Client, tmp_path: Path
) -> None:
    source, back = tmp_path / "in.pdf", tmp_path / "back.pdf"
    source.write_bytes(b"%PDF-1.7 test")
    ref = ObjectRef(bucket="idp-outputs", key="acc/job/item/document.pdf")

    uploaded = storage.upload(source, ref, "application/pdf")
    storage.download(ref, back)

    assert (uploaded.size, uploaded.sha256) == (13, hashlib.sha256(b"%PDF-1.7 test").hexdigest())
    assert back.read_bytes() == b"%PDF-1.7 test"
    assert s3.head_object(Bucket=ref.bucket, Key=ref.key)["ContentType"] == "application/pdf"


def test_reports_an_invalid_request_when_the_input_no_longer_exists(storage: S3ObjectStorage, tmp_path: Path) -> None:
    with pytest.raises(InvalidRequestError, match="no longer exists"):
        storage.download(ObjectRef(bucket="idp-originals", key="gone/file"), tmp_path / "x")


def test_refuses_an_input_over_the_size_limit_before_downloading_it(
    storage: S3ObjectStorage, s3: S3Client, tmp_path: Path
) -> None:
    s3.put_object(Bucket="idp-originals", Key="big/file", Body=b"x" * 2048)
    destination = tmp_path / "x"

    with pytest.raises(LimitExceededError):
        storage.download(ObjectRef(bucket="idp-originals", key="big/file"), destination)

    assert not destination.exists()
