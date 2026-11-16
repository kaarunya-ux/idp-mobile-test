"""PDFs and images into one PDF, in the order given: the merge of a ``PDF_MERGE`` job and of ``POST /api/v1/merge``."""

from collections.abc import Sequence
from pathlib import Path

from conversion.conversion.application.commands import ConvertFileCommand
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.ports import PdfMergerPort
from conversion.conversion.domain.route_planner import OcrMode


def merge_into_pdf(
    files: Sequence[tuple[Path, Format]],
    converter: ConvertFileService,
    merger: PdfMergerPort,
    *,
    work_dir: Path,
    output: Path,
) -> int:
    """Images become pages first. Returns the merged PDF's page count."""
    pdfs: list[Path] = []
    for number, (path, file_format) in enumerate(files, start=1):
        if file_format is Format.PDF:
            pdfs.append(path)
            continue
        image_pdf = converter.convert(
            ConvertFileCommand(
                source=file_format,
                target=Format.PDF,
                inputs=(path,),
                output_dir=work_dir / f"image-{number}",
                ocr=OcrMode.OFF,
            )
        )
        pdfs.extend(image_pdf.outputs)
    return merger.merge(pdfs, output)
