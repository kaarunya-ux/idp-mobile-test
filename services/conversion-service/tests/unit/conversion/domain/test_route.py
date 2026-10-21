"""A route can only exist if its steps chain from source to target (DOM-02)."""

import pytest

from conversion.conversion.domain.exceptions import InvalidRouteError
from conversion.conversion.domain.formats import Format, Representation
from conversion.conversion.domain.route import Converter, Fidelity, Route, Step

R, C = Representation, Converter


def _route(*steps: Step, source: Format = Format.CSV, target: Format = Format.PDF) -> Route:
    return Route(source=source, target=target, steps=steps, fidelity=Fidelity.HIGH)


def test_builds_the_route_when_every_step_feeds_the_next() -> None:
    route = _route(
        Step(converter=C.XLSX_CSV, source=R.CSV, target=R.XLSX),
        Step(converter=C.OFFICE_TO_PDF, source=R.XLSX, target=R.PDF),
    )

    assert route.converters == (C.XLSX_CSV, C.OFFICE_TO_PDF)
    assert not route.uses_ocr


def test_rejects_the_route_when_it_has_no_steps() -> None:
    with pytest.raises(InvalidRouteError, match="at least one step"):
        _route()


def test_rejects_the_route_when_the_first_step_does_not_read_the_source() -> None:
    with pytest.raises(InvalidRouteError, match="step 1 reads XLSX but receives CSV"):
        _route(Step(converter=C.OFFICE_TO_PDF, source=R.XLSX, target=R.PDF))


def test_rejects_the_route_when_a_middle_step_breaks_the_chain() -> None:
    with pytest.raises(InvalidRouteError, match="step 2 reads DOCX but receives XLSX"):
        _route(
            Step(converter=C.XLSX_CSV, source=R.CSV, target=R.XLSX),
            Step(converter=C.OFFICE_TO_PDF, source=R.DOCX, target=R.PDF),
        )


def test_rejects_the_route_when_the_last_step_does_not_write_the_target() -> None:
    with pytest.raises(InvalidRouteError, match="the last step writes XLSX"):
        _route(Step(converter=C.XLSX_CSV, source=R.CSV, target=R.XLSX))


def test_reports_ocr_when_any_step_reads_text_from_pixels() -> None:
    route = _route(
        Step(converter=C.OCR_TO_MODEL, source=R.PNG, target=R.DOCUMENT_MODEL),
        Step(converter=C.MODEL_TO_DOCX, source=R.DOCUMENT_MODEL, target=R.DOCX),
        source=Format.PNG,
        target=Format.DOCX,
    )

    assert route.uses_ocr


def test_knows_which_formats_are_images_when_asked() -> None:
    assert {f for f in Format if f.is_image} == {Format.PNG, Format.JPEG}
    assert Representation.of(Format.PDF) is R.PDF
