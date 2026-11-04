"""``WorkspacePort`` in memory: one request's pages and crops, under ``<accountId>/quick/<id>/`` (ADR 0012).

Nothing goes to storage on the synchronous lane: the images live in ``InlineImages`` while the request runs, are sent
inside the ai-gateway calls that need them, and are dropped when it ends, whatever happened.
"""

from extraction.core.ids import new_id
from extraction.pipeline.infrastructure.ai.inline_images import InlineImages
from extraction.quick.domain.models import QuickCaller


class MemoryWorkspace:
    def __init__(self, images: InlineImages) -> None:
        self._images = images

    def open(self, caller: QuickCaller) -> str:
        return f"{caller.account_id}/quick/{new_id()}"

    def discard(self, folder: str) -> None:
        self._images.drop(folder)
