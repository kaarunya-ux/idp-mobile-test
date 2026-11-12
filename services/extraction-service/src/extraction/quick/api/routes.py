"""The synchronous extract routes (ADR 0009), served by the ``extraction-sync`` process, which has the OCR engines.

``POST /api/v1/extract`` takes ``file`` (a PDF, or the front picture) and optionally ``back`` (the back picture of the
same document) as multipart/form-data, and answers with the document's type and its fields. OCR reads first; the AI
reads only the crops of the fields the two engines left unsure, or the whole document when no template matches.
``POST /api/v1/extract/generic`` takes the same and sends the document straight to the AI: no OCR, any document.
``POST /api/v1/extract/custom`` also takes ``fields``, the keys of a user's own template (ADR 0013), and answers with
exactly those keys: each read inside the box drawn around its value, else by its label, OCR first, the AI for what
OCR leaves unsure. The backend chooses a route or the queued one (``ExtractionRequested``): ``EXT-429-BUSY-001``
means "send it queued".
The token is the person's (forwarded by the backend): its account is the one whose daily AI calls are counted. With
``QUICK__REQUIRE_TOKEN=false`` (integration testing, ADR 0011) a request without one is counted on
``QUICK__DEFAULT_ACCOUNT_ID``; a request that sends one is still checked.
"""

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.security import HTTPAuthorizationCredentials

from extraction.core import permissions as perm
from extraction.core.errors import PermissionDeniedError, UnauthenticatedError
from extraction.core.permissions import Plan
from extraction.core.security import TokenVerifier, bearer, provide_token_verifier
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


@dataclass(frozen=True, slots=True, kw_only=True)
class TokenPolicy:
    """Whether a request must bring a token, and whose account a tokenless one is counted on."""

    required: bool
    default_account_id: UUID | None = None
    default_plan: Plan = "CORPORATE"


async def provide_caller(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    verifier: Annotated[TokenVerifier, Depends(provide_token_verifier)],
) -> QuickCaller:
    policy: TokenPolicy = request.app.state.quick_tokens
    if credentials is None:
        if policy.required or policy.default_account_id is None:
            raise UnauthenticatedError
        return QuickCaller(account_id=policy.default_account_id, plan=policy.default_plan)
    principal = await verifier.verify(credentials.credentials)
    if perm.DOCUMENT_WRITE not in principal.permissions:
        raise PermissionDeniedError(perm.DOCUMENT_WRITE)
    if principal.tenant_id is None:
        raise NoAccountError
    return QuickCaller(account_id=principal.tenant_id, plan=principal.plan)


def provide_quick_extract_service(request: Request) -> QuickExtractService:
    service: QuickExtractService = request.app.state.quick_extract_service  # built once in create_app (DI-03)
    return service


def provide_admission(request: Request) -> Admission:
    admission: Admission = request.app.state.quick_admission
    return admission


type Caller = Annotated[QuickCaller, Depends(provide_caller)]
type QuickExtract = Annotated[QuickExtractService, Depends(provide_quick_extract_service)]
type Admitted = Annotated[Admission, Depends(provide_admission)]


@router.post(
    "/extract",
    summary="Extract a document now: OCR first, the AI for what OCR leaves unsure",
    response_model=QuickExtractResponse,
    responses=_ERRORS,
)
async def extract(
    caller: Caller,
    service: QuickExtract,
    admission: Admitted,
    file: Annotated[UploadFile, File(description="One PDF, or the front picture (JPG, PNG or WebP)")],
    back: Annotated[UploadFile | None, File(description="Optional: the back picture of the same document")] = None,
) -> QuickExtractResponse:
    front, rear = await _read(service, file, back)
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
    caller: Caller,
    service: QuickExtract,
    admission: Admitted,
    file: Annotated[UploadFile, File(description="One PDF, or the front picture (JPG, PNG or WebP)")],
    back: Annotated[UploadFile | None, File(description="Optional: the back picture of the same document")] = None,
) -> QuickExtractResponse:
    front, rear = await _read(service, file, back)
    async with admission.enter():
        result = await asyncio.to_thread(service.extract_with_ai, caller, front, rear)  # PDF pages render (ASY-01)
    return mappers.to_response(result)


@router.post(
    "/extract/custom",
    summary="Extract the keys of a user's own template: OCR by label first, the AI for what OCR leaves unsure",
    response_model=QuickExtractResponse,
    responses=_ERRORS,
)
async def extract_custom(
    caller: Caller,
    service: QuickExtract,
    admission: Admitted,
    file: Annotated[UploadFile, File(description="One PDF, or the front picture (JPG, PNG or WebP)")],
    fields: Annotated[
        str,
        Form(
            description='JSON list of the keys to read: [{"id": "f1", "key": "card_number", "label": "Card No",'
            ' "type": "text", "box": {"page": 1, "x": 13.5, "y": 26.2, "w": 26, "h": 1.3}}]; id (optional, unique)'
            " is sent back on the key's field; type is text, date or number (default text); box (optional) is drawn"
            " around the value, in percent of the first page; 1 to 50 keys"
        ),
    ],
    back: Annotated[UploadFile | None, File(description="Optional: the back picture of the same document")] = None,
) -> QuickExtractResponse:
    keys, ids = mappers.custom_fields(fields)
    front, rear = await _read(service, file, back)
    async with admission.enter():
        result = await asyncio.to_thread(service.extract_custom, caller, front, rear, keys)  # OCR is CPU work
    return mappers.to_response(result, ids)


async def _read(service: QuickExtractService, file: UploadFile, back: UploadFile | None) -> tuple[bytes, bytes | None]:
    limit = service.max_file_bytes + 1  # one byte over is enough to refuse it; nothing larger is held in memory
    front = await file.read(limit)
    rear = await back.read(limit) if back is not None else None
    return front, rear
