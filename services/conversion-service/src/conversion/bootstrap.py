"""Composition root: builds the long-lived objects once per process (DI-01, DI-03).

The only module allowed to import from every layer. The message consumer and the Celery workers (step 4) build
their services here, so there is one wiring for every entry point.
"""

from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.domain.route_planner import RoutePlanner
from conversion.conversion.infrastructure.converters.image_formats import PillowImageFormatConverter
from conversion.conversion.infrastructure.converters.images_to_pdf import Img2PdfImageWrapper
from conversion.conversion.infrastructure.converters.pdf_to_images import PdfiumPageRenderer
from conversion.conversion.infrastructure.registry import ConverterRegistry
from conversion.core.config import LimitsSettings, Settings


def build_converter_registry(limits: LimitsSettings) -> ConverterRegistry:
    """Every converter this build of the service can run."""
    return ConverterRegistry(
        [
            PillowImageFormatConverter(max_pixels=limits.max_image_pixels),
            PdfiumPageRenderer(max_pages=limits.max_pages, max_side_px=limits.max_render_side_px),
            Img2PdfImageWrapper(max_pixels=limits.max_image_pixels),
        ]
    )


def build_convert_file_service(settings: Settings) -> ConvertFileService:
    return ConvertFileService(RoutePlanner(), build_converter_registry(settings.limits))
