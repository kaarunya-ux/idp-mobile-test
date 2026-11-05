"""Page images held in memory for one synchronous request, and sent to ai-gateway inside the call (ADR 0012).

The synchronous lane keeps nothing in storage: a prepared page or a field's crop is "saved" here as JPEG under its
reference, and the gateway client sends those bytes in the request instead of a storage key. The request's folder is
dropped when it ends. Thread-safe: the AI step reads crops from several threads.
"""

import io
import threading

from PIL import Image

from extraction.pipeline.domain.jobs import ObjectRef
from idp_ocr_engines.model import PageImage

BUCKET = "memory"  # the references' bucket: never a real one, nothing is stored
_JPEG_QUALITY = 92  # the web extraction app's; ai-gateway re-encodes at 90 after resizing


class InlineImages:
    """``PageImageStorePort`` in memory, read back by the gateway client."""

    def __init__(self) -> None:
        self._images: dict[str, bytes] = {}
        self._lock = threading.Lock()

    def save_page(self, ref: ObjectRef, page: PageImage) -> None:
        buffer = io.BytesIO()
        Image.frombytes("RGB", (page.width, page.height), page.rgb).save(buffer, format="JPEG", quality=_JPEG_QUALITY)
        with self._lock:
            self._images[ref.key] = buffer.getvalue()

    def get(self, ref: ObjectRef) -> bytes | None:
        with self._lock:
            return self._images.get(ref.key) if ref.bucket == BUCKET else None

    def drop(self, folder: str) -> None:
        """Forgets every image under ``folder``."""
        prefix = f"{folder}/"
        with self._lock:
            for key in [key for key in self._images if key.startswith(prefix)]:
                del self._images[key]

    def __len__(self) -> int:
        with self._lock:
            return len(self._images)
