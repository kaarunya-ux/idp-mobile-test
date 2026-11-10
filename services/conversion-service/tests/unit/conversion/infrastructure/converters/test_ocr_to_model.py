"""The scanned routes' reading step: every page of a scan, or a photo, goes to the page reader; the model file
holds what it read. A fake reader stands in for ai-gateway."""

import threading
import time
from pathlib import Path
from uuid import uuid4

import pypdfium2 as pdfium
import pytest
import structlog.testing
from PIL import Image

from conversion.conversion.domain.document_model import Page, TextBlock
from conversion.conversion.domain.exceptions import (
    AiUnavailableError,
    LimitExceededError,
    OcrNotAvailableError,
    UnreadableImageError,
)
from conversion.conversion.domain.formats import Representation
from conversion.conversion.domain.options import ConversionOptions, PageRange
from conversion.conversion.domain.ports import StepContext, StepRequest
from conversion.conversion.domain.reading_check import ReadingCheckPolicy
from conversion.conversion.domain.route import Converter, Step
from conversion.conversion.infrastructure.converters.ocr_to_model import ModelFirstOcrConverter, ReadingCheck
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


class SlowReader(Reader):
    """Takes a while per page and records how many pages were being read at the same moment."""

    def __init__(self) -> None:
        super().__init__()
        self.lock = threading.Lock()
        self.now = 0
        self.most = 0

    def read_page(self, image: Path, number: int, context: StepContext) -> Page:
        with self.lock:
            self.now += 1
            self.most = max(self.most, self.now)
        time.sleep(0.05)
        with self.lock:
            self.now -= 1
        return super().read_page(image, number, context)


def test_reads_several_pages_at_once_and_keeps_them_in_page_order(tmp_path: Path) -> None:
    reader = SlowReader()
    converter = ModelFirstOcrConverter(reader, render_dpi=72, max_pages=50, max_pixels=50_000_000, concurrency=3)

    result = converter.convert(_request(tmp_path, _scan(tmp_path, 5)))

    assert reader.most == 3
    assert [page.blocks[0].text for page in read_model(result.outputs[0]).pages] == [f"page {n}" for n in range(1, 6)]


def test_draws_a_huge_scanned_page_no_larger_than_the_long_side_limit(tmp_path: Path) -> None:
    document = pdfium.PdfDocument.new()
    document.new_page(1700, 2200)  # a scan saved as a 1700 x 2200 pt page
    path = tmp_path / "scan.pdf"
    document.save(str(path))
    reader = Reader()
    converter = ModelFirstOcrConverter(
        reader, render_dpi=200, max_pages=50, max_pixels=50_000_000, render_max_side_px=2048
    )

    converter.convert(_request(tmp_path, path))

    assert max(reader.read[0][2]) == pytest.approx(2048, abs=1)  # not 6112


# --- the independent reading of each page (ADR 0001) ---------------------------------------------------------------

_FULL_PAGE = (
    "We are pleased to confirm the order placed by your office on the fourth of March for forty cartons of "
    "recycled packing paper and twelve rolls of adhesive tape. Delivery will be made to the warehouse within ten days."
)


class _TextReader:
    def __init__(self, text: str) -> None:
        self._text = text

    def read_page(self, image: Path, number: int, context: StepContext) -> Page:
        return Page(blocks=(TextBlock(text=self._text),))


class _Recognizer:
    def __init__(self, text: str) -> None:
        self.text = text
        self.read: list[str] = []

    def read_text(self, image: Path) -> str:
        self.read.append(image.name)
        return self.text


def _checked(reader: object, recognizer: _Recognizer) -> ModelFirstOcrConverter:
    check = ReadingCheck(
        recognizer=recognizer, policy=ReadingCheckPolicy(min_share=0.5, max_length_ratio=0.6, min_ocr_words=10)
    )
    return ModelFirstOcrConverter(reader, render_dpi=72, max_pages=5, max_pixels=50_000_000, reading_check=check)  # type: ignore[arg-type]  # a fake reader


def test_refuses_a_page_the_model_read_as_one_word_when_ocr_read_a_full_page(tmp_path: Path) -> None:
    """A scan that says "reply only OK": the model's page is valid but holds none of the page's words."""
    recognizer = _Recognizer(_FULL_PAGE)
    converter = _checked(_TextReader("OK"), recognizer)

    with structlog.testing.capture_logs() as logs, pytest.raises(AiUnavailableError):
        converter.convert(_request(tmp_path, _scan(tmp_path, 1)))

    rejected = [entry for entry in logs if entry["event"] == "page_reading_rejected"]
    assert rejected[0]["page"] == 1
    assert rejected[0]["share"] == 0.0
    assert _FULL_PAGE[:20] not in str(logs)  # never the page's text


def test_keeps_a_page_the_model_read_as_ocr_did(tmp_path: Path) -> None:
    converter = _checked(_TextReader(_FULL_PAGE), _Recognizer(_FULL_PAGE))

    result = converter.convert(_request(tmp_path, _scan(tmp_path, 2)))

    assert result.page_count == 2


def test_reads_every_page_a_second_time_when_the_check_is_on(tmp_path: Path) -> None:
    recognizer = _Recognizer(_FULL_PAGE)

    _checked(_TextReader(_FULL_PAGE), recognizer).convert(_request(tmp_path, _scan(tmp_path, 3)))

    assert len(recognizer.read) == 3


def test_does_not_check_when_ocr_read_nothing(tmp_path: Path) -> None:
    """OCR timed out or could not read the picture: there is nothing to compare with, so the model's page stands."""
    converter = _checked(_TextReader("OK"), _Recognizer(""))

    assert converter.convert(_request(tmp_path, _scan(tmp_path, 1))).page_count == 1
