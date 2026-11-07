"""Use case: convert or merge uploaded files while the caller waits (ADR 0002).

The same conversions as a queued job, without storage or a job record: each file's real type comes from its content
(FIL-03). Several images to a PDF or a deck become one file, as on a job; any other several files are converted one by
one and come back as one ZIP, as does one file that makes several (a PDF's pages as images). A photo to Word, Excel or
CSV is read through ai-gateway, as on a job, counted on the caller's account. A scanned PDF to a document is refused
with a code that means "send it as a job" (many pages, minutes of reading), and so is a photo when this build has no
reader.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol
from uuid import UUID

from conversion.conversion.application.commands import ConvertFileCommand
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.application.services.merging import merge_into_pdf
from conversion.conversion.application.services.packaging import format_of_file, package_outputs
from conversion.conversion.domain.exceptions import (
    FileTypeUnsupportedError,
    InvalidRequestError,
    NeedsReadingError,
    NoAccountError,
)
from conversion.conversion.domain.formats import ZIP_CONTENT_TYPE, Format
from conversion.conversion.domain.options import JobOptions, parse_job_options, resolve_target
from conversion.conversion.domain.ports import FileInspectorPort, PdfMergerPort, StepContext
from conversion.conversion.domain.route_planner import RoutePlanner, RouteRequest
from conversion.conversion.domain.values import OutputMode
from conversion.core.errors import DomainError

_COMBINED_TARGETS = frozenset({Format.PDF, Format.PPTX})  # several images make one of these
_MERGED_NAME = "merged.pdf"


@dataclass(frozen=True, slots=True, kw_only=True)
class UploadedFile:
    path: Path  # alone in its own folder, under a name of ours
    name: str  # the caller's name without its extension, made safe: only for the outputs' names and for errors


@dataclass(frozen=True, slots=True, kw_only=True)
class ConvertNowCommand:
    """The outputs are written under ``folder``, and the caller removes the folder."""

    uploads: tuple[UploadedFile, ...]
    folder: Path
    target: str
    options: Mapping[str, object]
    account_id: UUID | None = None  # whose AI calls a photo's reading is counted on; None: no account


@dataclass(frozen=True, slots=True, kw_only=True)
class MergeNowCommand:
    uploads: tuple[UploadedFile, ...]
    folder: Path


@dataclass(frozen=True, slots=True, kw_only=True)
class ConvertNowResult:
    file: Path
    content_type: str
    page_count: int


class ConvertNowRunner(Protocol):
    """Runs the use case away from the event loop: conversions are CPU work (ASY-01)."""

    async def convert(self, command: ConvertNowCommand) -> ConvertNowResult: ...

    async def merge(self, command: MergeNowCommand) -> ConvertNowResult: ...


class ConvertNowService:
    def __init__(
        self,
        planner: RoutePlanner,
        converter: ConvertFileService,
        inspector: FileInspectorPort,
        merger: PdfMergerPort,
        *,
        reads_photos: bool = False,
    ) -> None:
        self._planner = planner
        self._converter = converter
        self._inspector = inspector
        self._merger = merger
        self._reads_photos = reads_photos  # the scanned reader is in this build (ai-gateway configured)

    def convert(self, command: ConvertNowCommand) -> ConvertNowResult:
        options = parse_job_options(command.options)
        target = resolve_target(command.target, command.options.get("imageFormat"))
        files = [(upload, *self._named_by_format(upload)) for upload in command.uploads]
        if len(files) > 1 and target in _COMBINED_TARGETS and all(found.is_image for _, _, found in files):
            return self._combined(files, target, options, command.folder)
        converted = [
            self._converted_one(
                upload, path, source, target, options, command.folder / f"out-{number}", account_id=command.account_id
            )
            for number, (upload, path, source) in enumerate(files, start=1)
        ]
        outputs = tuple(file for files_of_one, _ in converted for file in files_of_one)
        pages = sum(page_count for _, page_count in converted)
        return self._packaged(outputs, options.output, pages, command.folder)

    def merge(self, command: MergeNowCommand) -> ConvertNowResult:
        files = []
        for upload in command.uploads:
            path, source = self._named_by_format(upload)
            if source is not Format.PDF and not source.is_image:
                raise InvalidRequestError(f"{upload.name}: only PDFs and images can be merged.")
            files.append((path, source))
        output = command.folder / _MERGED_NAME
        pages = merge_into_pdf(files, self._converter, self._merger, work_dir=command.folder / "work", output=output)
        return ConvertNowResult(file=output, content_type=Format.PDF.content_type, page_count=pages)

    def _named_by_format(self, upload: UploadedFile) -> tuple[Path, Format]:
        """The upload's real format, and the file renamed to it: the converters read the extension."""
        source = self._inspector.detect_format(upload.path)
        if source is None:
            error = FileTypeUnsupportedError()
            error.detail = f"{upload.name}: {error.detail}"  # with several files, which one
            raise error
        return upload.path.rename(upload.path.with_name(f"input{source.suffix}")), source

    def _combined(
        self, files: Sequence[tuple[UploadedFile, Path, Format]], target: Format, options: JobOptions, folder: Path
    ) -> ConvertNowResult:
        source = files[0][2]
        self._reading_context(source, target, scanned=False, options=options, account_id=None)  # never reads
        result = self._converter.convert(
            ConvertFileCommand(
                source=source,
                target=target,
                inputs=tuple(path for _, path, _ in files),
                output_dir=folder / "out",
                options=options.conversion,
                ocr=options.ocr,
            )
        )
        return self._packaged(result.outputs, options.output, result.page_count, folder)

    def _converted_one(
        self,
        upload: UploadedFile,
        path: Path,
        source: Format,
        target: Format,
        options: JobOptions,
        output_dir: Path,
        *,
        account_id: UUID | None,
    ) -> tuple[tuple[Path, ...], int]:
        """Its outputs named after the caller's file, so several files' outputs can share one ZIP."""
        try:
            scanned = source is Format.PDF and self._inspector.is_scanned_pdf(path)
            context = self._reading_context(source, target, scanned=scanned, options=options, account_id=account_id)
            result = self._converter.convert(
                ConvertFileCommand(
                    source=source,
                    target=target,
                    inputs=(path,),
                    output_dir=output_dir,
                    options=options.conversion,
                    pdf_is_scanned=scanned,
                    ocr=options.ocr,
                    context=context,
                )
            )
        except DomainError as exc:
            exc.detail = f"{upload.name}: {exc.detail}"  # with several files, which one
            raise
        return tuple(_renamed(file, upload.name) for file in result.outputs), result.page_count

    def _reading_context(
        self, source: Format, target: Format, *, scanned: bool, options: JobOptions, account_id: UUID | None
    ) -> StepContext | None:
        """Whom a photo's reading is for; None when the route reads nothing. A route that doesn't exist answers its
        own error here, before any work, and so does one that has to go as a job."""
        route = self._planner.get_route(
            RouteRequest(source=source, target=target, pdf_is_scanned=scanned, ocr=options.ocr)
        )
        if not route.uses_ocr:
            return None
        if scanned or not self._reads_photos:
            raise NeedsReadingError
        if account_id is None:
            raise NoAccountError
        # the readers send each picture inside the call and store nothing, so the prefix only names the work
        return StepContext(account_id=account_id, key_prefix=f"{account_id}/sync")

    def _packaged(self, outputs: tuple[Path, ...], output: OutputMode, pages: int, folder: Path) -> ConvertNowResult:
        mode = OutputMode.SINGLE if output is OutputMode.SINGLE else OutputMode.ZIP  # one answer, one file
        (file,) = package_outputs(_unique(outputs, folder / "named"), mode, folder / "zip")
        file_format = format_of_file(file)
        return ConvertNowResult(
            file=file,
            content_type=file_format.content_type if file_format else ZIP_CONTENT_TYPE,
            page_count=pages,
        )


def _renamed(file: Path, name: str) -> Path:
    """``input-page-0001.png`` of ``report`` is ``report-page-0001.png``."""
    return file.rename(file.with_name(file.name.replace("input", name, 1)))


def _unique(files: tuple[Path, ...], folder: Path) -> tuple[Path, ...]:
    """Two uploads with one name keep both outputs: ``report.docx`` and ``report-2.docx``."""
    if len({file.name for file in files}) == len(files):
        return files
    folder.mkdir()
    seen: dict[str, int] = {}
    moved = []
    for file in files:
        seen[file.name] = seen.get(file.name, 0) + 1
        name = file.name if seen[file.name] == 1 else f"{file.stem}-{seen[file.name]}{file.suffix}"
        moved.append(file.rename(folder / name))
    return tuple(moved)
