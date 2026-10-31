"""The planner offers exactly the 27 conversions the service converts and refuses the rest (IDP-03 ch. 3.4)."""

import itertools

import pytest

from idox_conversion.conversion.application.plan_route import RoutePlanner
from idox_conversion.conversion.domain.exceptions import RouteUnsupportedError
from idox_conversion.conversion.domain.formats import SourceFormat, TargetFormat, source_format_of
from idox_conversion.conversion.domain.route import Stage
from idox_conversion.core.error_codes import ErrorCode

_PLANNER = RoutePlanner()
_PAIRS = list(itertools.product(SourceFormat, TargetFormat))


def test_service_offers_twenty_seven_conversions() -> None:
    assert len(_PLANNER.offered()) == 27


@pytest.mark.parametrize(("source", "target"), _PAIRS)
def test_every_pair_is_planned_or_refused_with_the_route_code(source: SourceFormat, target: TargetFormat) -> None:
    if (source, target) in _PLANNER.offered():
        assert _PLANNER.plan(source, target).source is source
    else:
        with pytest.raises(RouteUnsupportedError) as raised:
            _PLANNER.plan(source, target)
        assert raised.value.code is ErrorCode.ROUTE_UNSUPPORTED


@pytest.mark.parametrize(
    ("source", "target"),
    [
        (SourceFormat.DOCX, TargetFormat.PDF),
        (SourceFormat.PPTX, TargetFormat.PDF),
        (SourceFormat.XLSX, TargetFormat.PDF),
        (SourceFormat.CSV, TargetFormat.PDF),
        (SourceFormat.PDF, TargetFormat.IMAGES),
        (SourceFormat.DOCX, TargetFormat.IMAGES),
    ],
)
def test_route_that_only_draws_pages_does_not_use_the_model(source: SourceFormat, target: TargetFormat) -> None:
    route = _PLANNER.plan(source, target)

    assert not route.uses_model
    assert route.stages == (Stage.PREPARE,)


def test_route_that_reads_pages_runs_every_stage_in_order() -> None:
    route = _PLANNER.plan(SourceFormat.PDF, TargetFormat.DOCX)

    assert route.uses_model
    assert route.stages == (Stage.PREPARE, Stage.READ, Stage.CORRECT, Stage.WRITE, Stage.LAYOUT, Stage.CHECK)


def test_image_to_pdf_reads_with_the_model() -> None:
    assert _PLANNER.plan(SourceFormat.IMAGE, TargetFormat.PDF).uses_model


@pytest.mark.parametrize(
    ("name", "expected"),
    [
        ("report.PDF", SourceFormat.PDF),
        ("scan.tiff", SourceFormat.IMAGE),
        ("photo.jpeg", SourceFormat.IMAGE),
        ("deck.pptx", SourceFormat.PPTX),
        ("notes.txt", None),
        ("noextension", None),
    ],
)
def test_source_format_comes_from_the_file_suffix(name: str, expected: SourceFormat | None) -> None:
    assert source_format_of(name) is expected
