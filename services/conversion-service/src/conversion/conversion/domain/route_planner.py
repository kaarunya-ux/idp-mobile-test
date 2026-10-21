"""Chooses the route for a conversion (IDP-03 ch. 3.4).

IDP-03 lists the planner under ``application/``. It lives in the domain instead: choosing a route is a pure
business rule with no I/O, and the standard keeps such rules in ``domain/`` (ch. 2.4). See the build log, entry 2.
"""

from collections.abc import Iterator, Mapping
from dataclasses import dataclass
from enum import StrEnum

from conversion.conversion.domain.exceptions import RouteNotFoundError
from conversion.conversion.domain.formats import IMAGE_FORMATS, Format, Representation
from conversion.conversion.domain.route import Converter, Fidelity, Route, Step


class OcrMode(StrEnum):
    """The ``ocr`` conversion option (IDP-02 ch. 3.6)."""

    AUTO = "AUTO"
    ON = "ON"
    OFF = "OFF"


class RouteVariant(StrEnum):
    """Which version of a route applies. Most format pairs only have ``STANDARD``."""

    STANDARD = "STANDARD"
    SCANNED = "SCANNED"  # a PDF with no text layer: its text has to be read with OCR
    OCR = "OCR"  # the user asked for OCR (ocr=ON)


@dataclass(frozen=True, slots=True, kw_only=True)
class RouteRequest:
    """What to plan. ``pdf_is_scanned`` is known once the file is inspected (stage PREPARE)."""

    source: Format
    target: Format
    pdf_is_scanned: bool = False  # ignored unless the source is a PDF
    ocr: OcrMode = OcrMode.AUTO


type RouteKey = tuple[Format, Format, RouteVariant]


class RoutePlanner:
    """Turns a conversion request into the route that produces it."""

    def __init__(self, routes: Mapping[RouteKey, Route] | None = None) -> None:
        self._routes: dict[RouteKey, Route] = dict(routes if routes is not None else build_standard_routes())

    def get_route(self, request: RouteRequest) -> Route:
        """Return the route for ``request``; raise ``RouteNotFoundError`` (CNV-422-ROUTE-001) if there is none.

        A variant-specific route wins; otherwise the standard route for the pair applies.
        """
        route = self._routes.get((request.source, request.target, _variant_for(request)))
        if route is None:
            route = self._routes.get((request.source, request.target, RouteVariant.STANDARD))
        if route is None:
            raise RouteNotFoundError(request.source, request.target)
        return route

    def list_routes(self) -> list[Route]:
        return list(self._routes.values())


def _variant_for(request: RouteRequest) -> RouteVariant:
    if request.source is Format.PDF and request.pdf_is_scanned:
        return RouteVariant.SCANNED
    if request.ocr is OcrMode.ON:
        return RouteVariant.OCR
    return RouteVariant.STANDARD


# --- The route table ------------------------------------------------------------------------------------------
# Every cell of the IDP-00 ch. 5 matrix, with the steps from IDP-03 ch. 3.4. PDF is the visual hub (anything ->
# PDF -> images or slides); the document model is the data hub (anything -> model -> DOCX/XLSX/CSV/PPTX).

type _Entry = tuple[RouteVariant, Route]

_C = Converter
_R = Representation
_MODEL = Representation.DOCUMENT_MODEL
_STANDARD, _SCANNED, _OCR = RouteVariant.STANDARD, RouteVariant.SCANNED, RouteVariant.OCR
_E, _H, _A, _D = Fidelity.EXACT, Fidelity.HIGH, Fidelity.APPROXIMATE, Fidelity.DATA_ONLY
_IMAGES = sorted(IMAGE_FORMATS)
_WRITERS = {
    Format.DOCX: Converter.MODEL_TO_DOCX,
    Format.XLSX: Converter.MODEL_TO_XLSX,
    Format.CSV: Converter.MODEL_TO_CSV,
    Format.PPTX: Converter.MODEL_TO_PPTX,
}


def build_standard_routes() -> dict[RouteKey, Route]:
    """The full route table, keyed by (source, target, variant)."""
    entries = (*_from_pdf(), *_from_office(), *_from_sheets(), *_from_images())
    return {(route.source, route.target, variant): route for variant, route in entries}


def _route(source: Format, target: Format, fidelity: Fidelity, *hops: tuple[Converter, Representation]) -> Route:
    steps: list[Step] = []
    current = Representation.of(source)
    for converter, produces in hops:
        steps.append(Step(converter=converter, source=current, target=produces))
        current = produces
    return Route(source=source, target=target, steps=tuple(steps), fidelity=fidelity)


def _via_model(source: Format, target: Format, fidelity: Fidelity, reader: Converter) -> Route:
    return _route(source, target, fidelity, (reader, _MODEL), (_WRITERS[target], _R.of(target)))


def _from_pdf() -> Iterator[_Entry]:
    pdf = Format.PDF
    yield _STANDARD, _route(pdf, Format.DOCX, _A, (_C.PDF_TO_DOCX, _R.DOCX))
    yield _SCANNED, _via_model(pdf, Format.DOCX, _A, _C.OCR_TO_MODEL)
    for table_target in (Format.XLSX, Format.CSV):
        yield _STANDARD, _via_model(pdf, table_target, _D, _C.PDF_TABLES)
        yield _SCANNED, _via_model(pdf, table_target, _D, _C.OCR_TO_MODEL)
    yield _STANDARD, _route(pdf, Format.PPTX, _A, (_C.PDF_TO_IMAGES, _R.PNG), (_C.PPTX_FROM_IMAGES, _R.PPTX))
    for image in _IMAGES:
        yield _STANDARD, _route(pdf, image, _E, (_C.PDF_TO_IMAGES, _R.of(image)))


def _from_office() -> Iterator[_Entry]:
    for office in (Format.DOCX, Format.XLSX, Format.PPTX):
        yield _STANDARD, _route(office, Format.PDF, _H, (_C.OFFICE_TO_PDF, _R.PDF))
        for image in _IMAGES:
            yield _STANDARD, _route(office, image, _H, (_C.OFFICE_TO_PDF, _R.PDF), (_C.PDF_TO_IMAGES, _R.of(image)))
    for table_target in (Format.XLSX, Format.CSV):
        yield _STANDARD, _via_model(Format.DOCX, table_target, _D, _C.DOCX_TO_MODEL)
        yield _STANDARD, _via_model(Format.PPTX, table_target, _D, _C.PPTX_TO_MODEL)
    yield (
        _STANDARD,
        _route(
            Format.DOCX,
            Format.PPTX,
            _A,
            (_C.OFFICE_TO_PDF, _R.PDF),
            (_C.PDF_TO_IMAGES, _R.PNG),
            (_C.PPTX_FROM_IMAGES, _R.PPTX),
        ),
    )
    yield _STANDARD, _via_model(Format.PPTX, Format.DOCX, _A, _C.PPTX_TO_MODEL)


def _from_sheets() -> Iterator[_Entry]:
    csv, xlsx = Format.CSV, Format.XLSX
    yield _STANDARD, _route(xlsx, csv, _E, (_C.XLSX_CSV, _R.CSV))
    yield _STANDARD, _route(csv, xlsx, _E, (_C.XLSX_CSV, _R.XLSX))
    for sheet in (xlsx, csv):
        yield _STANDARD, _via_model(sheet, Format.DOCX, _D, _C.SHEET_TO_MODEL)
        yield _STANDARD, _via_model(sheet, Format.PPTX, _D, _C.SHEET_TO_MODEL)
    yield _STANDARD, _route(csv, Format.PDF, _H, (_C.XLSX_CSV, _R.XLSX), (_C.OFFICE_TO_PDF, _R.PDF))
    for image in _IMAGES:
        yield (
            _STANDARD,
            _route(
                csv, image, _H, (_C.XLSX_CSV, _R.XLSX), (_C.OFFICE_TO_PDF, _R.PDF), (_C.PDF_TO_IMAGES, _R.of(image))
            ),
        )


def _from_images() -> Iterator[_Entry]:
    for image in _IMAGES:
        yield _STANDARD, _route(image, Format.PDF, _E, (_C.IMAGES_TO_PDF, _R.PDF))
        yield _OCR, _route(image, Format.PDF, _E, (_C.SEARCHABLE_PDF, _R.PDF))
        yield _STANDARD, _via_model(image, Format.DOCX, _A, _C.OCR_TO_MODEL)
        yield _STANDARD, _via_model(image, Format.XLSX, _D, _C.OCR_TO_MODEL)
        yield _STANDARD, _via_model(image, Format.CSV, _D, _C.OCR_TO_MODEL)
        yield _STANDARD, _route(image, Format.PPTX, _E, (_C.PPTX_FROM_IMAGES, _R.PPTX))
        for other in _IMAGES:
            if other is not image:
                yield _STANDARD, _route(image, other, _E, (_C.IMAGE_FORMATS, _R.of(other)))
