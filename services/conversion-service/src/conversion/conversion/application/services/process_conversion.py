"""Use case: run one job item, PREPARE -> CONVERT -> PACKAGE (IDP-00 ch. 7.5; IDP-03 ch. 3.2, the workers).

Runs in a Celery worker. Each stage commits its progress with a ``JobProgressed`` outbox row; the end commits
the result with ``ConversionCompleted`` or ``WorkFailed``. Business errors (a damaged file, a bad option) end the
item. Anything else is a dependency or a bug: the item goes back to ``QUEUED`` and the exception goes up, so the
task can retry it (CEL-04) or, after the last retry, call ``fail``.
"""

import tempfile
import zipfile
from collections.abc import Callable
from datetime import timedelta
from pathlib import Path
from uuid import UUID

import structlog

from conversion.conversion.application.commands import ConvertFileCommand, ProcessItemCommand
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.domain.events import ConversionEvent
from conversion.conversion.domain.exceptions import InvalidOptionError, OcrNotAvailableError
from conversion.conversion.domain.formats import ZIP_CONTENT_TYPE, Format
from conversion.conversion.domain.models import ConversionItem, ConversionRequest
from conversion.conversion.domain.options import JobOptions, parse_job_options
from conversion.conversion.domain.ports import FileInspectorPort, ObjectStoragePort, UnitOfWorkFactory
from conversion.conversion.domain.values import ObjectRef, OutputMode, Stage, StoredOutput
from conversion.core.clock import Clock
from conversion.core.errors import DomainError

log = structlog.get_logger(__name__)

CONVERT_STARTS_AT, PACKAGE_STARTS_AT = 30, 80  # percent shown to the user
_LOCAL_STEM = "document"  # inputs get a fixed local name; keys and user file names are never paths (FIL-06)


class ProcessConversionService:
    def __init__(
        self,
        units: UnitOfWorkFactory,
        storage: ObjectStoragePort,
        inspector: FileInspectorPort,
        converter: ConvertFileService,
        clock: Clock,
        *,
        outputs_bucket: str,
        stale_after: timedelta,
    ) -> None:
        self._units = units
        self._storage = storage
        self._inspector = inspector
        self._converter = converter
        self._clock = clock
        self._outputs_bucket = outputs_bucket
        self._stale_after = stale_after

    def process(self, command: ProcessItemCommand) -> None:
        item = self._start(command.item_id)
        if item is None:
            return
        try:
            outputs, pages = self._run(item.request)
        except DomainError as error:
            self.fail(item.id, error)
            return
        except Exception:
            self._transition(item.id, lambda it: it.requeue(self._clock.now()))  # let the retry start it again
            raise
        self._transition(item.id, lambda it: it.complete(outputs, pages, self._clock.now()))
        log.info("conversion_completed", item_id=str(item.id), outputs=len(outputs), pages=pages)

    def fail(self, item_id: UUID, error: DomainError) -> None:
        """End the item with ``error`` (also called by the task after its last retry, or on a time limit)."""
        self._transition(
            item_id,
            lambda it: it.fail(error.code, retryable=error.retryable, detail=error.detail, now=self._clock.now()),
        )
        log.warning("conversion_failed", item_id=str(item_id), code=error.code, retryable=error.retryable)

    def _start(self, item_id: UUID) -> ConversionItem | None:
        with self._units() as unit:
            item = unit.items.get_for_update(item_id)
            if item is None or not item.can_start(self._clock.now(), stale_after=self._stale_after):
                return None  # already running elsewhere, finished, or a task for an item that was never stored
            unit.add_event(item.start(self._clock.now()))
            unit.items.save(item)
            unit.commit()
        return item

    def _run(self, request: ConversionRequest) -> tuple[tuple[StoredOutput, ...], int]:
        options = parse_job_options(request.options)
        with tempfile.TemporaryDirectory(prefix="item-") as scratch:  # always removed (FIL-06)
            folder = Path(scratch)
            inputs = self._download(request, folder / "in")
            scanned = request.source is Format.PDF and self._inspector.is_scanned_pdf(inputs[0])
            self._progress(request, Stage.CONVERT, CONVERT_STARTS_AT)
            try:
                result = self._converter.convert(
                    ConvertFileCommand(
                        source=request.source,
                        target=request.target,
                        inputs=inputs,
                        output_dir=folder / "out",
                        options=options.conversion,
                        pdf_is_scanned=scanned,
                        ocr=options.ocr,
                    )
                )
            except LookupError as exc:  # the route needs a converter this build doesn't have: OCR
                raise OcrNotAvailableError from exc
            self._progress(request, Stage.PACKAGE, PACKAGE_STARTS_AT)
            files = _package(result.outputs, options, folder / "zip")
            outputs = tuple(self._upload(file, request) for file in files)
        return outputs, result.page_count

    def _download(self, request: ConversionRequest, folder: Path) -> tuple[Path, ...]:
        folder.mkdir()
        paths = []
        for number, source in enumerate(request.inputs, start=1):
            stem = _LOCAL_STEM if number == 1 else f"{_LOCAL_STEM}-{number}"
            path = folder / f"{stem}{source.format.suffix}"
            self._storage.download(source.ref, path)
            paths.append(path)
        return tuple(paths)

    def _upload(self, file: Path, request: ConversionRequest) -> StoredOutput:
        file_format = _format_of(file)
        content_type = file_format.content_type if file_format else ZIP_CONTENT_TYPE
        ref = ObjectRef(bucket=self._outputs_bucket, key=request.output_key(file.name))
        uploaded = self._storage.upload(file, ref, content_type)
        pages = self._inspector.count_pages(file, file_format) if file_format else None
        return StoredOutput(
            ref=ref, content_type=content_type, size=uploaded.size, sha256=uploaded.sha256, page_count=pages
        )

    def _progress(self, request: ConversionRequest, stage: Stage, percent: int) -> None:
        self._transition(request.context.item_id, lambda it: it.progress(stage, percent, self._clock.now()))

    def _transition(self, item_id: UUID, change: Callable[[ConversionItem], ConversionEvent | None]) -> None:
        """Lock the item, apply ``change``, and commit it with the event it returns."""
        with self._units() as unit:
            item = unit.items.get_for_update(item_id)
            if item is None:
                return
            event = change(item)
            unit.items.save(item)
            if event is not None:
                unit.add_event(event)
            unit.commit()


def _package(files: tuple[Path, ...], options: JobOptions, folder: Path) -> tuple[Path, ...]:
    """Apply the ``output`` option: files as they are, one file only, or one ZIP of all of them."""
    if options.output is OutputMode.ZIP and len(files) > 1:
        folder.mkdir()
        archive = folder / f"{files[0].stem.split('-page-')[0]}.zip"
        with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
            for file in files:
                bundle.write(file, arcname=file.name)
        return (archive,)
    if options.output is OutputMode.SINGLE and len(files) > 1:
        raise InvalidOptionError("output", f"is SINGLE, but this conversion makes {len(files)} files; use ZIP")
    return files


def _format_of(file: Path) -> Format | None:
    suffix = file.suffix.lower()
    return next((member for member in Format if member.suffix == suffix), None)
