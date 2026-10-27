"""What a job item does to its downloaded files, by job type (IDP-00 ch. 7.5; IDP-03 ch. 3.1).

``ProcessConversionService`` downloads, reports progress, packages and uploads the same way for every job type;
a handler per type does the middle:

==============  ===============================================================================================
Job type        Work
==============  ===============================================================================================
``CONVERSION``  the planned route from the source format to the target (IDP-03 ch. 3.4)
``PDF_MERGE``   each image becomes a PDF through the images -> PDF route; then every page, in input order, is
                written to one PDF
``PREVIEW``     one JPEG thumbnail per page: PDFs and Office files through their route to JPEG with the pages
                rendered small, images shrunk directly (there is no JPEG -> JPEG conversion)
``PDF_EDIT``    the edit plan (``edit_pdf``)
``EXPORT``      extraction results into a file (``export_results``)
==============  ===============================================================================================
"""

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from conversion.conversion.application.commands import ConvertFileCommand
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.domain.exceptions import OcrNotAvailableError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.models import ConversionRequest
from conversion.conversion.domain.options import MAX_DPI, ConversionOptions, JobOptions
from conversion.conversion.domain.ports import ImageThumbnailPort, PdfMergerPort
from conversion.conversion.domain.route_planner import OcrMode
from conversion.conversion.domain.values import JobType, ThumbnailSpec

MERGED_FILE_NAME = "merged.pdf"


@dataclass(frozen=True, slots=True, kw_only=True)
class JobInput:
    request: ConversionRequest
    files: tuple[Path, ...]  # the downloaded inputs, in the request's order
    output_dir: Path  # everything written here is uploaded
    work_dir: Path  # scratch space that is not uploaded
    options: JobOptions
    pdf_is_scanned: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class JobOutput:
    files: tuple[Path, ...]
    page_count: int  # reported to Java as usage.pages


class JobHandler(Protocol):
    def run(self, job: JobInput) -> JobOutput: ...


class JobRunner:
    def __init__(self, handlers: Mapping[JobType, JobHandler]) -> None:
        missing = set(JobType) - set(handlers)
        if missing:
            raise ValueError(f"No handler for {sorted(missing)}")  # a wiring mistake: fail at start-up
        self._handlers = handlers

    def run(self, job: JobInput) -> JobOutput:
        job.output_dir.mkdir(parents=True, exist_ok=True)
        job.work_dir.mkdir(parents=True, exist_ok=True)
        return self._handlers[job.request.job_type].run(job)


class ConvertHandler:
    def __init__(self, converter: ConvertFileService) -> None:
        self._converter = converter

    def run(self, job: JobInput) -> JobOutput:
        request, options = job.request, job.options
        try:
            result = self._converter.convert(
                ConvertFileCommand(
                    source=request.source,
                    target=request.target,
                    inputs=job.files,
                    output_dir=job.output_dir,
                    options=options.conversion,
                    pdf_is_scanned=job.pdf_is_scanned,
                    ocr=options.ocr,
                )
            )
        except LookupError as exc:  # the route needs a converter this build doesn't have: OCR
            raise OcrNotAvailableError from exc
        return JobOutput(files=result.outputs, page_count=result.page_count)


class MergeHandler:
    def __init__(self, converter: ConvertFileService, merger: PdfMergerPort) -> None:
        self._converter = converter
        self._merger = merger

    def run(self, job: JobInput) -> JobOutput:
        pdfs: list[Path] = []
        for number, (path, source) in enumerate(zip(job.files, job.request.inputs, strict=True), start=1):
            if source.format is Format.PDF:
                pdfs.append(path)
                continue
            image_pdf = self._converter.convert(
                ConvertFileCommand(
                    source=source.format,
                    target=Format.PDF,
                    inputs=(path,),
                    output_dir=job.work_dir / f"image-{number}",
                    ocr=OcrMode.OFF,
                )
            )
            pdfs.extend(image_pdf.outputs)
        output = job.output_dir / MERGED_FILE_NAME
        return JobOutput(files=(output,), page_count=self._merger.merge(pdfs, output))


class PreviewHandler:
    def __init__(self, converter: ConvertFileService, thumbnails: ImageThumbnailPort, *, spec: ThumbnailSpec) -> None:
        self._converter = converter
        self._thumbnails = thumbnails
        self._spec = spec

    def run(self, job: JobInput) -> JobOutput:
        source, document = job.request.source, job.files[0]
        if source.is_image:
            thumbnail = job.output_dir / f"{document.stem}-page-0001{Format.JPEG.suffix}"
            self._thumbnails.thumbnail(document, thumbnail, self._spec)
            return JobOutput(files=(thumbnail,), page_count=1)
        result = self._converter.convert(
            ConvertFileCommand(
                source=source,
                target=Format.JPEG,
                inputs=(document,),
                output_dir=job.output_dir,
                # the highest DPI, so the size cap alone decides how big a page is drawn
                options=ConversionOptions(
                    dpi=MAX_DPI, jpeg_quality=self._spec.jpeg_quality, max_side_px=self._spec.max_side_px
                ),
                ocr=OcrMode.OFF,
            )
        )
        return JobOutput(files=result.outputs, page_count=result.page_count)


def document_id_of(key: str) -> str | None:
    """``<accountId>/<documentId>/<fileId>`` (IDP-00 ch. 7.7): the document an input or result belongs to."""
    parts = key.split("/")
    return parts[1] if len(parts) >= 3 else None  # noqa: PLR2004 - three segments in the key pattern
