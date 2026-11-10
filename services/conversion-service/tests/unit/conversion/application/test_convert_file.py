"""The convert-file use case plans the route and chains its steps; only final outputs reach the output folder."""

from collections.abc import Callable
from pathlib import Path
from uuid import uuid4

import docx
import openpyxl
import pytest
from PIL import Image

from conversion.bootstrap import build_convert_file_service, build_converter_registry
from conversion.conversion.application.commands import ConvertFileCommand
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.domain.document_model import Page, Table, TextBlock, TextKind
from conversion.conversion.domain.exceptions import RouteNotFoundError
from conversion.conversion.domain.formats import Format, Representation
from conversion.conversion.domain.options import ConversionOptions, PageRange
from conversion.conversion.domain.ports import StepContext, StepRequest, StepResult
from conversion.conversion.domain.route import OCR_CONVERTERS, Converter, Fidelity, Route, Step
from conversion.conversion.domain.route_planner import RoutePlanner, RouteVariant
from conversion.conversion.infrastructure.registry import ConverterNotRegisteredError, ConverterRegistry
from conversion.core.config import MissingSettingsError, Settings

MakeImage = Callable[..., Path]
MakePdf = Callable[..., Path]


@pytest.fixture
def service() -> ConvertFileService:
    return build_convert_file_service(Settings(_env_file=None))


def test_wraps_images_into_one_pdf_when_asked_for_pdf(
    service: ConvertFileService, make_image: MakeImage, tmp_path: Path
) -> None:
    inputs = (make_image("a.jpg"), make_image("b.jpg"))

    result = service.convert(
        ConvertFileCommand(source=Format.JPEG, target=Format.PDF, inputs=inputs, output_dir=tmp_path / "result")
    )

    assert [o.name for o in result.outputs] == ["a.pdf"]
    assert result.page_count == 2
    assert result.route.converters == (Converter.IMAGES_TO_PDF,)


def test_renders_the_chosen_page_when_converting_pdf_to_png(
    service: ConvertFileService, make_pdf: MakePdf, tmp_path: Path
) -> None:
    pdf = make_pdf("doc.pdf", sizes=[(100, 50)] * 3)
    options = ConversionOptions(dpi=144, page_range=PageRange.parse("3"))

    result = service.convert(
        ConvertFileCommand(
            source=Format.PDF, target=Format.PNG, inputs=(pdf,), output_dir=tmp_path / "result", options=options
        )
    )

    (page,) = result.outputs
    assert page.name == "doc-page-0003.png"
    with Image.open(page) as image:
        assert image.size == (200, 100)


class _Recorder:
    """A fake adapter that copies its input, so a multi-step route can be checked without real converters."""

    def __init__(self, converter: Converter) -> None:
        self.converter = converter
        self.seen: list[StepRequest] = []

    def convert(self, request: StepRequest) -> StepResult:
        self.seen.append(request)
        output = request.output_dir / f"{request.step.converter}.{str(request.step.target).lower()}"
        output.write_bytes(b"x")
        return StepResult(outputs=(output,), page_count=7)


def test_feeds_each_step_the_previous_output_and_keeps_only_the_last_when_the_route_has_steps(
    tmp_path: Path,
) -> None:
    first, second = _Recorder(Converter.XLSX_CSV), _Recorder(Converter.OFFICE_TO_PDF)
    service = ConvertFileService(RoutePlanner(), ConverterRegistry([first, second]))
    source = tmp_path / "sales.csv"
    source.write_text("a,b\n1,2\n", encoding="utf-8")

    result = service.convert(
        ConvertFileCommand(source=Format.CSV, target=Format.PDF, inputs=(source,), output_dir=tmp_path / "result")
    )

    first_output = first.seen[0].output_dir / "xlsx_csv.xlsx"
    assert second.seen[0].inputs == (first_output,)  # the second step reads what the first wrote
    assert [p.name for p in (tmp_path / "result").iterdir()] == ["office_to_pdf.pdf"]  # nothing intermediate leaks
    assert not first.seen[0].output_dir.exists()  # the scratch folder is gone
    assert result.page_count == 7


def test_raises_cnv_422_route_001_when_the_conversion_does_not_exist(
    service: ConvertFileService, make_image: MakeImage, tmp_path: Path
) -> None:
    with pytest.raises(RouteNotFoundError):
        service.convert(
            ConvertFileCommand(
                source=Format.PNG, target=Format.PNG, inputs=(make_image("a.png"),), output_dir=tmp_path / "r"
            )
        )


def test_raises_a_lookup_error_when_this_build_cannot_run_a_step_yet(
    service: ConvertFileService, make_image: MakeImage, tmp_path: Path
) -> None:
    with pytest.raises(ConverterNotRegisteredError):  # OCR converters arrive once the OCR engines are chosen
        service.convert(
            ConvertFileCommand(
                source=Format.PNG, target=Format.DOCX, inputs=(make_image("scan.png"),), output_dir=tmp_path / "r"
            )
        )


def test_uses_a_route_the_planner_was_given_when_the_table_is_custom(make_image: MakeImage, tmp_path: Path) -> None:
    step = Step(converter=Converter.IMAGE_FORMATS, source=Representation.PNG, target=Representation.JPEG)
    route = Route(source=Format.PNG, target=Format.JPEG, steps=(step,), fidelity=Fidelity.EXACT)
    recorder = _Recorder(Converter.IMAGE_FORMATS)
    service = ConvertFileService(
        RoutePlanner(routes={(Format.PNG, Format.JPEG, RouteVariant.STANDARD): route}), ConverterRegistry([recorder])
    )

    service.convert(
        ConvertFileCommand(source=Format.PNG, target=Format.JPEG, inputs=(make_image("a.png"),), output_dir=tmp_path)
    )

    assert recorder.seen[0].step is step


def test_this_build_can_run_every_route_that_needs_no_ocr() -> None:
    registry = build_converter_registry(Settings(_env_file=None))
    routes = [route for route in RoutePlanner().list_routes() if not set(route.converters) & OCR_CONVERTERS]

    missing = [f"{route.source} -> {route.target}" for route in routes if not registry.can_run(route)]

    assert len(routes) > 30
    assert missing == []


def test_turns_word_tables_into_sheets_when_converting_docx_to_xlsx(
    service: ConvertFileService, tmp_path: Path
) -> None:
    document = docx.Document()
    document.add_paragraph("Intro text is dropped: XLSX is data only")
    grid = document.add_table(rows=2, cols=2)
    for column, (head, value) in enumerate([("item", "Paper"), ("qty", "10")]):
        grid.cell(0, column).paragraphs[0].add_run(head).bold = True
        grid.cell(1, column).text = value
    source = tmp_path / "report.docx"
    document.save(str(source))

    result = service.convert(
        ConvertFileCommand(source=Format.DOCX, target=Format.XLSX, inputs=(source,), output_dir=tmp_path / "out")
    )

    sheet = openpyxl.load_workbook(result.outputs[0])["Table 1"]
    assert [[c.value for c in row] for row in sheet.iter_rows()] == [["item", "qty"], ["Paper", 10]]
    assert result.route.converters == (Converter.DOCX_TO_MODEL, Converter.MODEL_TO_XLSX)


class _FakePageReader:
    """Stands in for ai-gateway's page-read: one heading and one table per page, made up."""

    def __init__(self) -> None:
        self.contexts: list[StepContext] = []

    def read_page(self, image: Path, number: int, context: StepContext) -> Page:
        self.contexts.append(context)
        table = Table(rows=(("Item", "Amount"), ("Test item", "45.00")), header_rows=1)
        return Page(blocks=(TextBlock(text=f"Scanned page {number}", kind=TextKind.HEADING), table))


def _scanned_pdf(tmp_path: Path) -> Path:
    import pypdfium2 as pdfium  # noqa: PLC0415 - only this test draws a PDF

    document = pdfium.PdfDocument.new()
    document.new_page(595, 842)
    path = tmp_path / "scan.pdf"
    document.save(str(path))
    return path


def test_converts_a_scanned_pdf_to_word_through_the_page_reader_with_the_jobs_context(tmp_path: Path) -> None:
    from conversion.conversion.infrastructure.converters.ocr_to_model import ModelFirstOcrConverter  # noqa: PLC0415

    reader = _FakePageReader()
    registry = build_converter_registry(Settings(_env_file=None))
    ocr = ModelFirstOcrConverter(reader, render_dpi=72, max_pages=5, max_pixels=50_000_000)
    service = ConvertFileService(RoutePlanner(), ConverterRegistry([ocr, registry.get(Converter.MODEL_TO_DOCX)]))
    context = StepContext(account_id=uuid4(), key_prefix="acct/job/item")

    result = service.convert(
        ConvertFileCommand(
            source=Format.PDF,
            target=Format.DOCX,
            inputs=(_scanned_pdf(tmp_path),),
            output_dir=tmp_path / "result",
            pdf_is_scanned=True,
            context=context,
        )
    )

    document = docx.Document(str(result.outputs[0]))
    assert [p.text for p in document.paragraphs if p.text] == ["Scanned page 1"]
    assert document.tables[0].rows[1].cells[1].text == "45.00"
    assert reader.contexts == [context]
    assert result.route.converters[0] is Converter.OCR_TO_MODEL


def test_registers_the_scanned_reader_only_when_the_gateway_and_its_login_are_configured() -> None:
    plain = build_converter_registry(Settings(_env_file=None))
    configured = Settings.model_validate(
        {
            "ai_gateway": {"url": "http://ai-gateway:8002"},
            "service_auth": {"token_url": "http://keycloak:8080/realms/idp/token", "client_secret": "test-secret"},
            "reading_check": {"enabled": False},  # this test is about registration; the check has its own tests below
        }
    )

    with pytest.raises(ConverterNotRegisteredError):
        plain.get(Converter.OCR_TO_MODEL)
    assert build_converter_registry(configured).get(Converter.OCR_TO_MODEL)  # no storage needed: pages go inline


def _configured(**reading_check: object) -> Settings:
    return Settings.model_validate(
        {
            "ai_gateway": {"url": "http://ai-gateway:8002"},
            "service_auth": {"token_url": "http://keycloak:8080/realms/idp/token", "client_secret": "test-secret"},
            "reading_check": reading_check,
        }
    )


def test_stops_the_worker_when_the_reading_check_is_on_and_tesseract_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("conversion.bootstrap.tesseract_available", lambda: False)

    with pytest.raises(MissingSettingsError, match="READING_CHECK__ENABLED"):
        build_converter_registry(_configured())


def test_turns_the_reading_check_on_for_the_scanned_route_when_tesseract_is_installed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr("conversion.bootstrap.tesseract_available", lambda: True)

    converter = build_converter_registry(_configured(min_share=0.4)).get(Converter.OCR_TO_MODEL)

    assert converter._check is not None
    assert converter._check.policy.min_share == 0.4


def test_reads_scanned_pages_without_the_check_when_it_is_switched_off() -> None:
    converter = build_converter_registry(_configured(enabled=False)).get(Converter.OCR_TO_MODEL)

    assert converter._check is None
