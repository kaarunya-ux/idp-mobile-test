"""``EXPORT``: extraction results into JSON, CSV, XLSX, DOCX, PDF or XML (IDP-03 ch. 3.6).

The inputs are result files (JSON, IDP-00 ch. 7.8), one per document. Masked fields stay masked: the values are
read as extraction-service stored them. The DOCX and PDF reports show each document's first page image when it
can be fetched; a PDF report is the DOCX report converted through the Office -> PDF route.
"""

import json
from pathlib import Path

from conversion.conversion.application.commands import ConvertFileCommand
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.application.services.run_job import JobInput, JobOutput, document_id_of
from conversion.conversion.domain.exceptions import InvalidRequestError
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.ports import ObjectStoragePort, ResultExporterPort
from conversion.conversion.domain.results import ResultDocument, parse_export_options, parse_result
from conversion.conversion.domain.route_planner import OcrMode
from conversion.conversion.domain.values import ObjectRef

_REPORTS = frozenset({Format.DOCX, Format.PDF})


class ExportHandler:
    def __init__(
        self,
        exporter: ResultExporterPort,
        converter: ConvertFileService,
        storage: ObjectStoragePort,
        *,
        derived_bucket: str,
    ) -> None:
        self._exporter = exporter
        self._converter = converter
        self._storage = storage
        self._derived_bucket = derived_bucket

    def run(self, job: JobInput) -> JobOutput:
        target = job.request.target
        results = [self._read(path, n, job) for n, path in enumerate(job.files, start=1)]
        thumbnails = self._thumbnails(results, job.work_dir) if target in _REPORTS else {}
        options = parse_export_options(job.request.options)
        if target is not Format.PDF:
            files = self._exporter.export(results, target, options, job.output_dir, thumbnails)
            return JobOutput(files=files, page_count=len(results))
        report = self._exporter.export(results, Format.DOCX, options, job.work_dir / "report", thumbnails)
        pdf = self._converter.convert(
            ConvertFileCommand(
                source=Format.DOCX, target=Format.PDF, inputs=report, output_dir=job.output_dir, ocr=OcrMode.OFF
            )
        )
        return JobOutput(files=pdf.outputs, page_count=len(results))

    def _read(self, path: Path, number: int, job: JobInput) -> ResultDocument:
        key = job.request.inputs[number - 1].ref.key
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise InvalidRequestError("An input is not valid JSON.") from exc
        return parse_result(data, name=document_id_of(key) or f"document-{number}")

    def _thumbnails(self, results: list[ResultDocument], folder: Path) -> dict[str, Path]:
        """First page images; one that can't be fetched is left out of the report rather than failing it."""
        thumbnails: dict[str, Path] = {}
        for number, result in enumerate(results, start=1):
            if result.thumbnail_key is None:
                continue
            destination = folder / f"thumbnail-{number}"
            try:
                self._storage.download(ObjectRef(bucket=self._derived_bucket, key=result.thumbnail_key), destination)
            except InvalidRequestError:  # the page image is gone (expired, or never made)
                continue
            thumbnails[result.name] = destination
        return thumbnails
