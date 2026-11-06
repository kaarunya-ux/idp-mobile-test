"""Converting one file while the caller waits, with the libraries only: its type from its content, the limits, one
file back, and anything that needs reading sent to a job."""

import zipfile
from collections.abc import Sequence
from pathlib import Path

import pytest

from conversion.bootstrap import build_convert_now_service
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.application.services.convert_now import (
    ConvertNowCommand,
    ConvertNowService,
    MergeNowCommand,
    UploadedFile,
)
from conversion.conversion.domain.exceptions import (
    FileTypeUnsupportedError,
    InvalidOptionError,
    InvalidRequestError,
    NeedsReadingError,
    RouteNotFoundError,
)
from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.ports import ConverterPort, StepRequest, StepResult
from conversion.conversion.domain.route import Converter
from conversion.conversion.domain.route_planner import RoutePlanner
from conversion.conversion.infrastructure.registry import ConverterRegistry
from conversion.core.config import Settings


class FakeInspector:
    def __init__(
        self, file_format: Format | None = Format.PDF, *, scanned: bool = False, pages: int | None = 3
    ) -> None:
        self.file_format, self.scanned, self.pages = file_format, scanned, pages

    def detect_format(self, path: Path) -> Format | None:
        return self.file_format

    def is_scanned_pdf(self, path: Path) -> bool:
        return self.scanned

    def count_pages(self, path: Path, file_format: Format) -> int | None:
        return self.pages


class FakeConverter:
    """Writes ``names`` for its step, and keeps what it was asked."""

    def __init__(self, converter: Converter, names: Sequence[str]) -> None:
        self._converter, self._names = converter, names
        self.requests: list[StepRequest] = []

    @property
    def converter(self) -> Converter:
        return self._converter

    def convert(self, request: StepRequest) -> StepResult:
        self.requests.append(request)
        outputs = tuple(request.output_dir / name for name in self._names)
        for output in outputs:
            output.write_bytes(b"made-up output")
        return StepResult(outputs=outputs, page_count=len(outputs))


class FakeMerger:
    def __init__(self) -> None:
        self.pdfs: list[Path] = []

    def merge(self, pdfs: Sequence[Path], output: Path) -> int:
        self.pdfs = list(pdfs)
        output.write_bytes(b"made-up merged pdf")
        return len(self.pdfs)


def _service(
    inspector: FakeInspector, *converters: ConverterPort, merger: FakeMerger | None = None
) -> ConvertNowService:
    planner = RoutePlanner()
    return ConvertNowService(
        planner, ConvertFileService(planner, ConverterRegistry(list(converters))), inspector, merger or FakeMerger()
    )


def _uploads(tmp_path: Path, *names: str) -> tuple[UploadedFile, ...]:
    uploads = []
    for number, name in enumerate(names, start=1):
        own = tmp_path / f"in-{number}"
        own.mkdir()
        path = own / "upload"
        path.write_bytes(b"made-up upload")
        uploads.append(UploadedFile(path=path, name=name))
    return tuple(uploads)


def _command(
    tmp_path: Path, target: str = "PNG", options: dict[str, object] | None = None, names: Sequence[str] = ("report",)
) -> ConvertNowCommand:
    return ConvertNowCommand(uploads=_uploads(tmp_path, *names), folder=tmp_path, target=target, options=options or {})


def test_runs_the_step_on_the_upload_named_by_its_real_format_and_answers_the_one_file(tmp_path: Path) -> None:
    pages = FakeConverter(Converter.PDF_TO_IMAGES, ["input-page-0001.png"])

    result = _service(FakeInspector(), pages).convert(_command(tmp_path, options={"pageRange": "1"}))

    assert pages.requests[0].inputs == (tmp_path / "in-1" / "input.pdf",)
    assert result.file.name == "report-page-0001.png"
    assert (result.content_type, result.page_count) == ("image/png", 1)


def test_answers_one_zip_when_the_conversion_makes_several_files(tmp_path: Path) -> None:
    pages = FakeConverter(Converter.PDF_TO_IMAGES, ["input-page-0001.png", "input-page-0002.png"])

    result = _service(FakeInspector(), pages).convert(_command(tmp_path))

    assert result.content_type == "application/zip"
    with zipfile.ZipFile(result.file) as archive:
        assert archive.namelist() == ["report-page-0001.png", "report-page-0002.png"]


def test_refuses_several_files_when_the_output_option_says_single(tmp_path: Path) -> None:
    pages = FakeConverter(Converter.PDF_TO_IMAGES, ["input-page-0001.png", "input-page-0002.png"])

    with pytest.raises(InvalidOptionError, match="SINGLE"):
        _service(FakeInspector(), pages).convert(_command(tmp_path, options={"output": "SINGLE"}))


def test_refuses_a_file_whose_content_is_none_of_our_formats(tmp_path: Path) -> None:
    with pytest.raises(FileTypeUnsupportedError):
        _service(FakeInspector(None)).convert(_command(tmp_path))


def test_refuses_a_conversion_that_does_not_exist_before_any_work(tmp_path: Path) -> None:
    with pytest.raises(RouteNotFoundError):
        _service(FakeInspector()).convert(_command(tmp_path, "PDF"))


@pytest.mark.parametrize("target", ["DOCX", "XLSX", "CSV"])
def test_sends_a_scanned_pdf_to_a_job_without_reading_it(tmp_path: Path, target: str) -> None:
    reader = FakeConverter(Converter.OCR_TO_MODEL, ["input.model.json"])

    with pytest.raises(NeedsReadingError):
        _service(FakeInspector(scanned=True), reader).convert(_command(tmp_path, target))

    assert reader.requests == []


def test_sends_a_photo_to_a_document_to_a_job(tmp_path: Path) -> None:
    with pytest.raises(NeedsReadingError):
        _service(FakeInspector(Format.PNG, pages=1)).convert(_command(tmp_path, "DOCX"))


def test_still_draws_a_scanned_pdfs_pages_as_images_because_that_reads_nothing(tmp_path: Path) -> None:
    pages = FakeConverter(Converter.PDF_TO_IMAGES, ["input-page-0001.png"])

    assert _service(FakeInspector(scanned=True), pages).convert(_command(tmp_path)).file.exists()


def test_converts_several_files_one_by_one_into_one_zip_named_after_each(tmp_path: Path) -> None:
    reader = FakeConverter(Converter.PDF_TO_MODEL, ["input.model.json"])
    writer = FakeConverter(Converter.MODEL_TO_DOCX, ["input.docx"])

    result = _service(FakeInspector(), reader, writer).convert(_command(tmp_path, "DOCX", names=("first", "second")))

    assert result.content_type == "application/zip"
    with zipfile.ZipFile(result.file) as archive:
        assert archive.namelist() == ["first.docx", "second.docx"]
    assert len(reader.requests) == 2


def test_keeps_both_outputs_when_two_files_have_one_name(tmp_path: Path) -> None:
    pages = FakeConverter(Converter.PDF_TO_IMAGES, ["input-page-0001.png"])

    result = _service(FakeInspector(), pages).convert(_command(tmp_path, names=("same", "same")))

    with zipfile.ZipFile(result.file) as archive:
        assert archive.namelist() == ["same-page-0001.png", "same-page-0001-2.png"]


def test_combines_several_images_into_one_file_when_the_target_is_a_pdf_or_a_deck(tmp_path: Path) -> None:
    wrapper = FakeConverter(Converter.IMAGES_TO_PDF, ["input.pdf"])

    result = _service(FakeInspector(Format.PNG), wrapper).convert(_command(tmp_path, "PDF", names=("a", "b")))

    assert result.content_type == "application/pdf"
    assert [path.parent.name for path in wrapper.requests[0].inputs] == ["in-1", "in-2"]


def test_says_which_file_failed_when_one_of_several_cannot_be_converted(tmp_path: Path) -> None:
    pages = FakeConverter(Converter.PDF_TO_IMAGES, ["input-page-0001.png"])
    inspector = FakeInspector(scanned=True)

    with pytest.raises(NeedsReadingError) as caught:
        _service(inspector, pages).convert(_command(tmp_path, "DOCX", names=("first", "second")))

    assert caught.value.detail.startswith("first: ")


def test_merges_pdfs_and_images_in_the_order_sent(tmp_path: Path) -> None:
    class ByUpload(FakeInspector):
        def __init__(self, formats: list[Format]) -> None:
            super().__init__()
            self.formats = formats

        def detect_format(self, path: Path) -> Format | None:
            return self.formats.pop(0)

    wrapper = FakeConverter(Converter.IMAGES_TO_PDF, ["input.pdf"])
    merger = FakeMerger()
    uploads = _uploads(tmp_path, "first", "picture", "last")

    result = _service(ByUpload([Format.PDF, Format.PNG, Format.PDF]), wrapper, merger=merger).merge(
        MergeNowCommand(uploads=uploads, folder=tmp_path)
    )

    assert [path.parent.name for path in merger.pdfs] == ["in-1", "image-2", "in-3"]
    assert (result.file.name, result.page_count, result.content_type) == ("merged.pdf", 3, "application/pdf")


def test_refuses_to_merge_anything_but_pdfs_and_images(tmp_path: Path) -> None:
    with pytest.raises(InvalidRequestError, match="report: only PDFs and images"):
        _service(FakeInspector(Format.DOCX)).merge(
            MergeNowCommand(uploads=_uploads(tmp_path, "report"), folder=tmp_path)
        )


def test_never_gets_the_scanned_reader_even_when_ai_gateway_is_configured() -> None:
    settings = Settings.model_validate(
        {
            "ai_gateway": {"url": "http://ai-gateway:8002"},
            "service_auth": {"token_url": "http://keycloak:8080/realms/idp/token", "client_secret": "test-secret"},
            "office": {"binary": "no-such-office"},  # no spare LibreOffice for this test
        }
    )

    service = build_convert_now_service(settings)

    with pytest.raises(LookupError):
        service._converter._converters.get(Converter.OCR_TO_MODEL)
