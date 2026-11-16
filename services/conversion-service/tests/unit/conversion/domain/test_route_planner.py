"""The route table covers the whole IDP-00 ch. 5 matrix, and the planner picks the right variant (IDP-03 ch. 3.4)."""

import pytest

from conversion.conversion.domain.exceptions import RouteNotFoundError
from conversion.conversion.domain.formats import IMAGE_FORMATS, MATRIX_FORMATS, Format
from conversion.conversion.domain.route import Converter, Fidelity
from conversion.conversion.domain.route_planner import OcrMode, RoutePlanner, RouteRequest

E, H, A, D = Fidelity.EXACT, Fidelity.HIGH, Fidelity.APPROXIMATE, Fidelity.DATA_ONLY
IMAGE = "IMAGE"

# IDP-00 ch. 5, copied from the spec (not from the code): row = source, column = target, IMAGE = PNG or JPEG.
SPEC_MATRIX: dict[str, dict[str, Fidelity]] = {
    "PDF": {"DOCX": A, "XLSX": D, "CSV": D, "PPTX": A, IMAGE: E},
    "DOCX": {"PDF": H, "XLSX": D, "CSV": D, "PPTX": A, IMAGE: H},
    "XLSX": {"PDF": H, "DOCX": D, "CSV": E, "PPTX": D, IMAGE: H},
    "CSV": {"PDF": H, "DOCX": D, "XLSX": E, "PPTX": D, IMAGE: H},
    "PPTX": {"PDF": H, "DOCX": A, "XLSX": D, "CSV": D, IMAGE: H},
    IMAGE: {"PDF": E, "DOCX": A, "XLSX": D, "CSV": D, "PPTX": E},
}
CELLS = [(source, target, fidelity) for source, row in SPEC_MATRIX.items() for target, fidelity in row.items()]


def _formats(name: str) -> list[Format]:
    return sorted(IMAGE_FORMATS) if name == IMAGE else [Format(name)]


@pytest.fixture(scope="module")
def planner() -> RoutePlanner:
    return RoutePlanner()


def test_spec_matrix_has_thirty_cells_when_copied_from_idp_00() -> None:
    assert len(CELLS) == 30


@pytest.mark.parametrize(("source", "target", "fidelity"), CELLS)
def test_every_matrix_cell_has_a_route_with_the_spec_fidelity_when_planned(
    planner: RoutePlanner, source: str, target: str, fidelity: Fidelity
) -> None:
    for source_format in _formats(source):
        for target_format in _formats(target):
            route = planner.get_route(RouteRequest(source=source_format, target=target_format))
            assert route.fidelity is fidelity, f"{source_format} -> {target_format}"


@pytest.mark.parametrize(("source", "target"), [(Format.PNG, Format.JPEG), (Format.JPEG, Format.PNG)])
def test_converts_between_image_formats_exactly_when_asked(
    planner: RoutePlanner, source: Format, target: Format
) -> None:
    route = planner.get_route(RouteRequest(source=source, target=target))

    assert route.converters == (Converter.IMAGE_FORMATS,)
    assert route.fidelity is E


def test_table_holds_only_matrix_pairs_when_built(planner: RoutePlanner) -> None:
    pairs = {(route.source, route.target) for route in planner.list_routes()}

    assert len(pairs) == 7 * 6  # every format to every other format, nothing more
    assert all(source is not target for source, target in pairs)


def test_never_uses_ocr_when_the_source_has_real_text(planner: RoutePlanner) -> None:
    digital = [Format.PDF, Format.DOCX, Format.XLSX, Format.CSV, Format.PPTX]

    for source in digital:
        for target in MATRIX_FORMATS:
            if target is not source:
                assert not planner.get_route(RouteRequest(source=source, target=target)).uses_ocr, f"{source}->{target}"


@pytest.mark.parametrize(
    ("target", "reader"),
    [
        (Format.DOCX, Converter.OCR_TO_MODEL),
        (Format.XLSX, Converter.OCR_TO_MODEL),
        (Format.CSV, Converter.OCR_TO_MODEL),
    ],
)
def test_reads_text_with_ocr_when_the_pdf_is_scanned(planner: RoutePlanner, target: Format, reader: Converter) -> None:
    route = planner.get_route(RouteRequest(source=Format.PDF, target=target, pdf_is_scanned=True))

    assert route.converters[0] is reader


def test_uses_the_standard_route_when_a_scanned_pdf_has_no_special_variant(planner: RoutePlanner) -> None:
    route = planner.get_route(RouteRequest(source=Format.PDF, target=Format.PNG, pdf_is_scanned=True))

    assert route.converters == (Converter.PDF_TO_IMAGES,)


@pytest.mark.parametrize(
    ("ocr", "converter"),
    [
        (OcrMode.ON, Converter.SEARCHABLE_PDF),
        (OcrMode.AUTO, Converter.IMAGES_TO_PDF),
        (OcrMode.OFF, Converter.IMAGES_TO_PDF),
    ],
)
def test_adds_a_text_layer_only_when_ocr_is_on(planner: RoutePlanner, ocr: OcrMode, converter: Converter) -> None:
    route = planner.get_route(RouteRequest(source=Format.JPEG, target=Format.PDF, ocr=ocr))

    assert route.converters == (converter,)


def test_ignores_the_scanned_flag_when_the_source_is_not_a_pdf(planner: RoutePlanner) -> None:
    route = planner.get_route(RouteRequest(source=Format.DOCX, target=Format.PDF, pdf_is_scanned=True))

    assert route.converters == (Converter.OFFICE_TO_PDF,)


def test_routes_csv_through_a_typed_sheet_when_making_a_pdf(planner: RoutePlanner) -> None:
    route = planner.get_route(RouteRequest(source=Format.CSV, target=Format.PDF))

    assert route.converters == (Converter.XLSX_CSV, Converter.OFFICE_TO_PDF)


@pytest.mark.parametrize("file_format", list(Format))
def test_rejects_with_cnv_422_route_001_when_source_and_target_are_the_same(
    planner: RoutePlanner, file_format: Format
) -> None:
    with pytest.raises(RouteNotFoundError) as caught:
        planner.get_route(RouteRequest(source=file_format, target=file_format))

    assert caught.value.code == "CNV-422-ROUTE-001"
    assert caught.value.code.http_status == 422
    assert str(file_format) in caught.value.detail


def test_rejects_when_the_route_table_is_empty() -> None:
    with pytest.raises(RouteNotFoundError):
        RoutePlanner(routes={}).get_route(RouteRequest(source=Format.PDF, target=Format.DOCX))
