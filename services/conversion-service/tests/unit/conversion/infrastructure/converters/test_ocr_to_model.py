"""The scanned routes' reading step: every page of a scan, or a photo, goes to the page reader; the model file
holds what it read. A fake reader stands in for ai-gateway."""

from pathlib import Path
from uuid import uuid4

import pypdfium2 as pdfium
import pytest
from PIL import Image

from conversion.conversion.domain.document_model import Page, TextBlock
from conversion.conversion.domain.exceptions import LimitExceededError, OcrNotAvailableError, UnreadableImageError
from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.options import ConversionOptions, PageRange
from conversion.conversion.domain.ports import StepContext, StepRequest
from conversion.conversion.domain.route import Converter, Step
from conversion.conversion.infrastructure.converters.ocr_to_model import ModelFirstOcrConverter
from conversion.conversion.infrastructure.model_file import read_model

CONTEXT = StepContext(account_id=uuid4(), key_prefix="acct/job/item")


class Reader:
    def __init__(self) -> None:
        self.read: list[tuple[str, int, tuple[int, int]]] = []

    def read_page(self, image: Path, number: int, context: StepContext) -> Page:
        with Image.open(image) as picture:
            self.read.append((image.name, number, picture.size))
        return Page(blocks=(TextBlock(text=f"page {number}"),))


def _scan(tmp_path: Path, pages: int) -> Path:
    document = pdfium.PdfDocument.new()
    for _ in range(pages):
        document.new_page(595, 842)  # A4 in points
    path = tmp_path / "scan.pdf"
    document.save(str(path))
    return path


def _request(tmp_path: Path, *inputs: Path, context: StepContext | None = CONTEXT, options=None) -> StepRequest:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    step = Step(converter=Converter.OCR_TO_MODEL, source=Representation.PDF, target=Representation.DOCUMENT_MODEL)
    return StepRequest(
        step=step, inputs=inputs, output_dir=out, options=options or ConversionOptions(), context=context
    )


def _converter(reader: Reader, *, max_pages: int = 50) -> ModelFirstOcrConverter:
    return ModelFirstOcrConverter(reader, render_dpi=100, max_pages=max_pages, max_pixels=50_000_000)


def test_reads_every_page_of_a_scan_into_one_model(tmp_path: Path) -> None:
    reader = Reader()

    result = _converter(reader).convert(_request(tmp_path, _scan(tmp_path, 2)))

    assert [(name, number) for name, number, _ in reader.read] == [("scan-page-0001.png", 1), ("scan-page-0002.png", 2)]
    assert reader.read[0][2] == (827, 1170)  # A4 at 100 DPI (PDFium rounds up)
    model = read_model(result.outputs[0])
    assert [page.blocks[0].text for page in model.pages] == ["page 1", "page 2"]
    assert result.page_count == 2


def test_reads_only_the_requested_pages(tmp_path: Path) -> None:
    reader = Reader()
    options = ConversionOptions(page_range=PageRange.parse("2"))

    _converter(reader).convert(_request(tmp_path, _scan(tmp_path, 3), options=options))

    assert [name for name, _, _ in reader.read] == ["scan-page-0002.png"]


def test_reads_a_photo_as_one_page(tmp_path: Path) -> None:
    photo = tmp_path / "photo.jpg"
    Image.new("RGB", (640, 480), "white").save(photo, format="JPEG")
    reader = Reader()

    result = _converter(reader).convert(_request(tmp_path, photo))

    assert reader.read == [("photo.png", 1, (640, 480))]
    assert result.page_count == 1


def test_refuses_a_scan_longer_than_the_page_limit(tmp_path: Path) -> None:
    with pytest.raises(LimitExceededError):
        _converter(Reader(), max_pages=2).convert(_request(tmp_path, _scan(tmp_path, 3)))


def test_refuses_a_file_that_is_not_an_image(tmp_path: Path) -> None:
    junk = tmp_path / "junk.png"
    junk.write_bytes(b"not an image at all")

    with pytest.raises(UnreadableImageError):
        _converter(Reader()).convert(_request(tmp_path, junk))


def test_needs_the_jobs_context(tmp_path: Path) -> None:
    with pytest.raises(OcrNotAvailableError):
        _converter(Reader()).convert(_request(tmp_path, _scan(tmp_path, 1), context=None))
