"""``ImageStorePort`` on MinIO (S3 API): callers send keys, never bytes (IDP-03 ch. 4.3).

Only the buckets in ``STORAGE__ALLOWED_BUCKETS`` are read. A file over the size limit, one Pillow can't open, or a
decompression bomb is refused with ``AIG-422-INPUT-001``; a missing key too. Each page is resized so its long side is
at most ``MODEL__MAX_IMAGE_SIDE_PX`` and sent as JPEG. ``stack`` puts a card's front above its back with a white
strip between (from the web extraction app's ``stack_sides``), so the model reads both sides in one request.

boto3 and Pillow block, so the work runs in a worker thread (ASY-01).
"""

import asyncio
import io
from collections.abc import Sequence
from typing import TYPE_CHECKING

import structlog
from botocore.exceptions import BotoCoreError, ClientError
from PIL import Image as PilImage
from PIL import UnidentifiedImageError

from ai_gateway.ai.domain.exceptions import InputInvalidError
from ai_gateway.ai.domain.models import Image, ImageRef
from ai_gateway.core.errors import DependencyUnavailableError

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

log = structlog.get_logger(__name__)

_JPEG_QUALITY = 90
_SIDE_GAP_PX = 24
_MAX_PIXELS = 60_000_000  # Pillow refuses larger images as decompression bombs (FIL-05)
_MISSING = {"NoSuchKey", "404", "NotFound"}

PilImage.MAX_IMAGE_PIXELS = _MAX_PIXELS


class S3ImageStore:
    def __init__(self, client: "S3Client", *, allowed_buckets: Sequence[str], max_bytes: int, max_side_px: int) -> None:
        self._client = client
        self._allowed = frozenset(allowed_buckets)
        self._max_bytes = max_bytes
        self._max_side_px = max_side_px

    async def load(self, refs: Sequence[ImageRef], *, stack: bool) -> Sequence[Image]:
        for ref in refs:
            if ref.bucket not in self._allowed:
                raise InputInvalidError(f"Images are not read from the bucket '{ref.bucket}'.")
        return await asyncio.to_thread(self._load, refs, stack)

    def _load(self, refs: Sequence[ImageRef], stack: bool) -> list[Image]:
        pages = [self._open(ref) for ref in refs]
        if stack and len(pages) > 1:
            pages = [_stack(pages[:2])]
        return [self._encode(page) for page in pages]

    def _open(self, ref: ImageRef) -> PilImage.Image:
        try:
            head = self._client.head_object(Bucket=ref.bucket, Key=ref.key)
            if head["ContentLength"] > self._max_bytes:
                raise InputInvalidError("A page image is larger than the gateway accepts.")
            data = self._client.get_object(Bucket=ref.bucket, Key=ref.key)["Body"].read()
        except ClientError as exc:
            if exc.response.get("Error", {}).get("Code") in _MISSING:
                raise InputInvalidError("A page image was not found.") from exc
            log.error("image_storage_error", error=type(exc).__name__)
            raise DependencyUnavailableError("image storage") from exc
        except BotoCoreError as exc:
            log.error("image_storage_unreachable", error=type(exc).__name__)
            raise DependencyUnavailableError("image storage") from exc
        try:
            image = PilImage.open(io.BytesIO(data))
            image.load()
        except (UnidentifiedImageError, PilImage.DecompressionBombError, OSError) as exc:
            raise InputInvalidError("A page image is not an image the gateway can read.") from exc
        return image.convert("RGB")

    def _encode(self, page: PilImage.Image) -> Image:
        page.thumbnail((self._max_side_px, self._max_side_px), PilImage.Resampling.LANCZOS)
        buffer = io.BytesIO()
        page.save(buffer, format="JPEG", quality=_JPEG_QUALITY)
        return Image(data=buffer.getvalue(), media_type="image/jpeg")


def _stack(sides: Sequence[PilImage.Image]) -> PilImage.Image:
    """Front above back, at the same width, with a white strip between."""
    width = max(side.width for side in sides)
    same = [
        side
        if side.width == width
        else side.resize((width, round(side.height * width / side.width)), PilImage.Resampling.LANCZOS)
        for side in sides
    ]
    stacked = PilImage.new("RGB", (width, same[0].height + _SIDE_GAP_PX + same[1].height), "white")
    stacked.paste(same[0], (0, 0))
    stacked.paste(same[1], (0, same[0].height + _SIDE_GAP_PX))
    return stacked
