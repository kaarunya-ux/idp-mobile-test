"""The synchronous conversion routes (ADR 0002), served by the ``conversion-sync`` process, multipart/form-data.

``POST /api/v1/convert`` takes one or more ``file`` and a ``target`` (and optionally ``options``, the JSON a job's
options are) and answers with the converted file: several files come back as one ZIP, except images to a PDF or a deck,
which become one file. ``POST /api/v1/merge`` takes PDFs and images and answers with one PDF, in the order sent. Both
use the libraries only, never OCR or the AI. The backend chooses these or the queued way (``ConversionRequested``), and
decides how much to send: ``CNV-422-SYNC-001`` and ``CNV-429-BUSY-001`` mean "send it queued".

The token is the person's, forwarded by the backend. With ``SYNC__REQUIRE_TOKEN=false`` (integration testing) a request
without one is let in; a request that sends one is still checked.
"""

import asyncio
import json
import re
import shutil
import tempfile
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Annotated, Any

from fastapi import APIRouter, Depends, File, Form, Request, UploadFile
from fastapi.responses import FileResponse
from fastapi.security import HTTPAuthorizationCredentials
from starlette.background import BackgroundTask

from conversion.conversion.application.services.convert_now import (
    ConvertNowCommand,
    ConvertNowResult,
    ConvertNowRunner,
    MergeNowCommand,
    UploadedFile,
)
from conversion.conversion.domain.exceptions import (
    BusyError,
    FileTooLargeError,
    InvalidOptionError,
    UnreadableDocumentError,
)
from conversion.core import permissions as perm
from conversion.core.errors import PermissionDeniedError, UnauthenticatedError
from conversion.core.security import TokenVerifier, bearer, provide_token_verifier

router = APIRouter(prefix="/api/v1", tags=["Conversion"])

_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": "Problem details (RFC 9457)"} for code in (400, 401, 403, 413, 415, 422, 429, 500, 504)
}
_UNSAFE_IN_NAME = re.compile(r"[^\w.-]+")
_MAX_NAME = 100
_SEVERAL_FILES_NAME = "converted"
_MERGED_NAME = "merged"
_MIB = 1024 * 1024


class Admission:
    """At most ``limit`` files at once (one worker process each); one more is told "busy" at once, not queued."""

    def __init__(self, limit: int) -> None:
        self._free = asyncio.Semaphore(limit)

    @asynccontextmanager
    async def enter(self) -> AsyncIterator[None]:
        if self._free.locked():
            raise BusyError
        async with self._free:
            yield


async def check_token(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(bearer)],
    verifier: Annotated[TokenVerifier, Depends(provide_token_verifier)],
) -> None:
    if credentials is None:
        if request.app.state.sync_require_token:
            raise UnauthenticatedError
        return
    principal = await verifier.verify(credentials.credentials)
    if perm.DOCUMENT_WRITE not in principal.permissions:
        raise PermissionDeniedError(perm.DOCUMENT_WRITE)


def provide_runner(request: Request) -> ConvertNowRunner:
    runner: ConvertNowRunner = request.app.state.convert_now  # built once in create_sync_app (DI-03)
    return runner


def provide_admission(request: Request) -> Admission:
    admission: Admission = request.app.state.sync_admission
    return admission


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadLimits:
    """The ceilings that protect the machine (FIL-05); how much to send is the backend's decision."""

    max_file_size: int
    max_total_size: int
    max_files: int


def provide_limits(request: Request) -> UploadLimits:
    limits: UploadLimits = request.app.state.sync_limits
    return limits


type Runner = Annotated[ConvertNowRunner, Depends(provide_runner)]
type Admitted = Annotated[Admission, Depends(provide_admission)]
type Limits = Annotated[UploadLimits, Depends(provide_limits)]
_FILES = "PDF, Word, Excel, PowerPoint, CSV, PNG or JPEG; repeat the field for several files"


@router.post(
    "/convert",
    summary="Convert files now with the libraries: the answer is the converted file, or one ZIP",
    response_class=FileResponse,
    responses=_ERRORS,
    dependencies=[Depends(check_token)],
)
async def convert(
    runner: Runner,
    admission: Admitted,
    limits: Limits,
    file: Annotated[list[UploadFile], File(description=_FILES)],
    target: Annotated[str, Form(description="PDF, DOCX, XLSX, CSV, PPTX, PNG or JPEG", max_length=32)],
    options: Annotated[str | None, Form(description="JSON, as a job's options: pageRange, dpi, ...")] = None,
) -> FileResponse:
    parsed = _parsed_options(options)
    _check_uploads(file, limits)
    async with admission.enter(), _WorkFolder() as folder:
        uploads = await asyncio.to_thread(_saved, file, folder.path)
        result = await runner.convert(
            ConvertNowCommand(uploads=uploads, folder=folder.path, target=target, options=parsed)
        )
    name = uploads[0].name if len(uploads) == 1 else _SEVERAL_FILES_NAME
    return _answer(result, name, folder.path)


@router.post(
    "/merge",
    summary="Merge PDFs and images now into one PDF, in the order sent",
    response_class=FileResponse,
    responses=_ERRORS,
    dependencies=[Depends(check_token)],
)
async def merge(
    runner: Runner,
    admission: Admitted,
    limits: Limits,
    file: Annotated[list[UploadFile], File(description="PDFs and PNG or JPEG images; repeat the field, in page order")],
) -> FileResponse:
    _check_uploads(file, limits)
    async with admission.enter(), _WorkFolder() as folder:
        uploads = await asyncio.to_thread(_saved, file, folder.path)
        result = await runner.merge(MergeNowCommand(uploads=uploads, folder=folder.path))
    return _answer(result, _MERGED_NAME, folder.path)


class _WorkFolder:
    """A private folder (FIL-06): removed here when the request fails, and after the answer when it succeeds."""

    path: Path

    async def __aenter__(self) -> "_WorkFolder":
        self.path = Path(await asyncio.to_thread(tempfile.mkdtemp, prefix="convert-now-"))
        return self

    async def __aexit__(self, error_type: type[BaseException] | None, *_: object) -> None:
        if error_type is not None:
            shutil.rmtree(self.path, ignore_errors=True)


def _check_uploads(files: list[UploadFile], limits: UploadLimits) -> None:
    if len(files) > limits.max_files:
        raise FileTooLargeError(f"This has {len(files):,} files; up to {limits.max_files:,} go in one request.")
    for found in files:
        if not found.size:
            raise UnreadableDocumentError
        if found.size > limits.max_file_size:
            raise FileTooLargeError(f"A file is larger than {limits.max_file_size // _MIB} MB. Send it as a job.")
    if sum(found.size or 0 for found in files) > limits.max_total_size:
        raise FileTooLargeError(f"The files are larger than {limits.max_total_size // _MIB} MB together.")


def _saved(files: list[UploadFile], folder: Path) -> tuple[UploadedFile, ...]:
    """Each file alone in its own folder, under a name of ours: never the caller's as a path (FIL-06)."""
    uploads = []
    for number, found in enumerate(files, start=1):
        own = folder / f"in-{number}"
        own.mkdir()
        path = own / "upload"
        with path.open("wb") as stream:
            shutil.copyfileobj(found.file, stream)
        uploads.append(UploadedFile(path=path, name=_safe_name(found.filename)))
    return tuple(uploads)


def _answer(result: ConvertNowResult, name: str, folder: Path) -> FileResponse:
    return FileResponse(
        result.file,
        media_type=result.content_type,
        filename=f"{name}{result.file.suffix}",
        headers={"X-Page-Count": str(result.page_count)},
        background=BackgroundTask(shutil.rmtree, folder, ignore_errors=True),
    )


def _parsed_options(options: str | None) -> dict[str, object]:
    if options is None or not options.strip():
        return {}
    try:
        parsed = json.loads(options)
    except ValueError:
        parsed = None
    if not isinstance(parsed, dict):
        raise InvalidOptionError("options", "must be a JSON object")
    return parsed


def _safe_name(uploaded: str | None) -> str:
    """The caller's name without folders or extension, safe for a header and for a name inside the ZIP."""
    stem = _UNSAFE_IN_NAME.sub("_", PureWindowsPath(uploaded or "").stem)[:_MAX_NAME].strip("._")
    return stem or "converted"
