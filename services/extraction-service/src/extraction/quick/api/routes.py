"""The synchronous extract routes (ADR 0009), served by the ``extraction-sync`` process, which has the OCR engines.

``POST /api/v1/extract`` takes ``file`` (a PDF, or the front picture) and optionally ``back`` (the back picture of the
same document) as multipart/form-data, and answers with the document's type and its fields. OCR reads first; the AI
reads only the crops of the fields the two engines left unsure, or the whole document when no template matches.
``POST /api/v1/extract/generic`` takes the same and sends the document straight to the AI: no OCR, any document. The
backend chooses a route or the queued one (``ExtractionRequested``): ``EXT-429-BUSY-001`` means "send it queued".
The token is the person's (forwarded by the backend): its account is the one whose daily AI calls are counted.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Request, UploadFile

from extraction.core import permissions as perm
from extraction.core.security import Principal, require_permission
from extraction.quick.api import mappers
from extraction.quick.api.schemas import QuickExtractResponse
from extraction.quick.application.quick_extract import QuickExtractService
from extraction.quick.domain.exceptions import BusyError, NoAccountError
from extraction.quick.domain.models import QuickCaller

router = APIRouter(prefix="/api/v1", tags=["Extraction"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": "Problem details (RFC 9457)"} for code in (400, 401, 403, 413, 415, 422, 429, 503)
}


class Admission:
    """At most ``limit`` documents at once (the CPU's share for OCR); one more is told "busy" at once, not queued."""

    def __init__(self, limit: int) -> None:
        self._free = asyncio.Semaphore(limit)

    @asynccontextmanager
    async def enter(self) -> AsyncIterator[None]:
        if self._free.locked():
            raise BusyError
        async with self._free:
            yield


def provide_quick_extract_service(request: Request) -> QuickExtractService:
    service: QuickExtractService = request.app.state.quick_extract_service  # built once in create_app (DI-03)
    return service


def provide_admission(request: Request) -> Admission:
    admission: Admission = request.app.state.quick_admission
    return admission


type Writer = Annotated[Principal, Depends(require_permission(perm.DOCUMENT_WRITE))]
type QuickExtract = Annotated[QuickExtractService, Depends(provide_quick_extract_service)]
type Admitted = Annotated[Admission, Depends(provide_admission)]


@router.post(
    "/extract",
    summary="Extract a document now: OCR first, the AI for what OCR leaves unsure",
    response_model=QuickExtractResponse,
    responses=_ERRORS,
)
async def extract(
    principal: Writer,
    service: QuickExtract,
    admission: Admitted,
    file: Annotated[UploadFile, File(description="One PDF, or the front picture (JPG or PNG)")],
    back: Annotated[UploadFile | None, File(description="Optional: the back picture of the same document")] = None,
) -> QuickExtractResponse:
    caller, front, rear = await _read(principal, service, file, back)
    async with admission.enter():
        result = await asyncio.to_thread(service.extract, caller, front, rear)  # OCR is CPU work (ASY-01)
    return mappers.to_response(result)


@router.post(
    "/extract/generic",
    summary="Extract any document straight with the AI: its type and every field, no OCR",
    response_model=QuickExtractResponse,
    responses=_ERRORS,
)
async def extract_generic(
    principal: Writer,
    service: QuickExtract,
    admission: Admitted,
    file: Annotated[UploadFile, File(description="One PDF, or the front picture (JPG or PNG)")],
    back: Annotated[UploadFile | None, File(description="Optional: the back picture of the same document")] = None,
) -> QuickExtractResponse:
    caller, front, rear = await _read(principal, service, file, back)
    async with admission.enter():
        result = await asyncio.to_thread(service.extract_with_ai, caller, front, rear)  # PDF pages render (ASY-01)
    return mappers.to_response(result)


async def _read(
    principal: Principal, service: QuickExtractService, file: UploadFile, back: UploadFile | None
) -> tuple[QuickCaller, bytes, bytes | None]:
    if principal.tenant_id is None:
        raise NoAccountError
    limit = service.max_file_bytes + 1  # one byte over is enough to refuse it; nothing larger is held in memory
    front = await file.read(limit)
    rear = await back.read(limit) if back is not None else None
    return QuickCaller(account_id=principal.tenant_id, plan=principal.plan), front, rear
