"""Composition root: builds the long-lived objects once per process (DI-01, DI-03).

The only module allowed to import from every layer. The message consumer and the Celery workers (step 4) build
their services here, so there is one wiring for every entry point.
"""

from conversion.conversion.application.services.convert_file import ConvertFileService
from conversion.conversion.domain.route_planner import RoutePlanner
from conversion.conversion.infrastructure.converters.docx_to_model import PythonDocxReader
from conversion.conversion.infrastructure.converters.image_formats import PillowImageFormatConverter
from conversion.conversion.infrastructure.converters.images_to_pdf import Img2PdfImageWrapper
from conversion.conversion.infrastructure.converters.model_to_docx import PythonDocxWriter
from conversion.conversion.infrastructure.converters.model_to_pptx import PythonPptxWriter
from conversion.conversion.infrastructure.converters.model_to_sheets import CsvModelWriter, OpenpyxlModelWriter
from conversion.conversion.infrastructure.converters.office_to_pdf import LibreOfficePdfConverter
from conversion.conversion.infrastructure.converters.pdf_to_images import PdfiumPageRenderer
from conversion.conversion.infrastructure.converters.pdf_to_model import PdfplumberReader
from conversion.conversion.infrastructure.converters.pptx_from_images import PptxImageSlides
from conversion.conversion.infrastructure.converters.pptx_to_model import PythonPptxReader
from conversion.conversion.infrastructure.converters.sheet_to_model import SheetReader
from conversion.conversion.infrastructure.converters.xlsx_csv import OpenpyxlCsvConverter
from conversion.conversion.infrastructure.registry import ConverterRegistry
from conversion.core.config import Settings


def build_converter_registry(settings: Settings) -> ConverterRegistry:
    """Every converter this build of the service can run."""
    limits, office = settings.limits, settings.office
    return ConverterRegistry(
        [
            PillowImageFormatConverter(max_pixels=limits.max_image_pixels),
            PdfiumPageRenderer(max_pages=limits.max_pages, max_side_px=limits.max_render_side_px),
            Img2PdfImageWrapper(max_pixels=limits.max_image_pixels),
            PptxImageSlides(max_pixels=limits.max_image_pixels),
            LibreOfficePdfConverter(binary=office.binary, timeout=office.timeout),
            OpenpyxlCsvConverter(),
            PythonDocxReader(),
            SheetReader(),
            PythonPptxReader(),
            PdfplumberReader(max_pages=limits.max_pages),
            PythonDocxWriter(max_table_rows=limits.max_document_table_rows),
            PythonPptxWriter(max_table_rows=limits.max_document_table_rows),
            OpenpyxlModelWriter(),
            CsvModelWriter(),
        ]
    )


def build_convert_file_service(settings: Settings) -> ConvertFileService:
    return ConvertFileService(RoutePlanner(), build_converter_registry(settings))
