"""The convert-file use case plans the route and chains its steps; only final outputs reach the output folder."""

from collections.abc import Callable
from pathlib import Path

import docx
import openpyxl
import pytest
from PIL import Image

from conversion.bootstrap import build_convert_file_service, build_converter_registry
from conversion.conversion.application.commands import ConvertFileCommand
from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.domain.exceptions import RouteNotFoundError
from conversion.conversion.domain.formats import Format, Representation
from conversion.conversion.domain.options import ConversionOptions, PageRange
from conversion.conversion.domain.ports import StepRequest, StepResult
from conversion.conversion.domain.route import OCR_CONVERTERS, Converter, Fidelity, Route, Step
from conversion.conversion.domain.route_planner import RoutePlanner, RouteVariant
from conversion.conversion.infrastructure.registry import ConverterNotRegisteredError, ConverterRegistry
from conversion.core.config import Settings

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
