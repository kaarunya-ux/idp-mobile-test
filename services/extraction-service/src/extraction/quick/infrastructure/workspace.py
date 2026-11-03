"""``WorkspacePort`` on MinIO: one call's pages and crops under ``<accountId>/quick/<id>/`` in the derived bucket.

They exist only while the call runs: the use case deletes the folder after the answer, whatever it was.
"""

from typing import TYPE_CHECKING

import structlog
from botocore.exceptions import BotoCoreError, ClientError

from extraction.core.ids import new_id
from extraction.quick.domain.models import QuickCaller

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

log = structlog.get_logger(__name__)


class S3Workspace:
    def __init__(self, client: "S3Client", *, bucket: str) -> None:
        self._client = client
        self._bucket = bucket

    def open(self, caller: QuickCaller) -> str:
        return f"{caller.account_id}/quick/{new_id()}"

    def discard(self, folder: str) -> None:
        try:
            listed = self._client.list_objects_v2(Bucket=self._bucket, Prefix=f"{folder}/")
            for item in listed.get("Contents", []):
                self._client.delete_object(Bucket=self._bucket, Key=item["Key"])
        except (BotoCoreError, ClientError) as exc:
            log.warning("quick_discard_failed", error=type(exc).__name__)
