"""The registry finds the adapter for each kind of step, and refuses duplicates."""

import pytest

from conversion.conversion.domain.formats import Format
from conversion.conversion.domain.route import Converter
from conversion.conversion.domain.route_planner import RoutePlanner, RouteRequest
from conversion.conversion.infrastructure.converters.image_formats import PillowImageFormatConverter
from conversion.conversion.infrastructure.registry import ConverterNotRegisteredError, ConverterRegistry

image_converter = PillowImageFormatConverter(max_pixels=1_000_000)


def test_returns_the_adapter_when_its_converter_is_registered() -> None:
    assert ConverterRegistry([image_converter]).get(Converter.IMAGE_FORMATS) is image_converter


def test_raises_a_lookup_error_when_no_adapter_is_registered() -> None:
    with pytest.raises(ConverterNotRegisteredError, match="office_to_pdf"):
        ConverterRegistry([]).get(Converter.OFFICE_TO_PDF)


def test_refuses_two_adapters_when_they_run_the_same_converter() -> None:
    with pytest.raises(ValueError, match="registered twice"):
        ConverterRegistry([image_converter, PillowImageFormatConverter(max_pixels=10)])


def test_knows_which_routes_it_can_run_when_asked() -> None:
    planner, registry = RoutePlanner(), ConverterRegistry([image_converter])

    assert registry.can_run(planner.get_route(RouteRequest(source=Format.PNG, target=Format.JPEG)))
    assert not registry.can_run(planner.get_route(RouteRequest(source=Format.DOCX, target=Format.PDF)))
