"""A conversion route: the ordered steps that turn one format into another (IDP-03 ch. 3.4)."""

from dataclasses import dataclass
from enum import StrEnum

from conversion.conversion.domain.exceptions import InvalidRouteError
from conversion.conversion.domain.formats import Format, Representation


class Converter(StrEnum):
    """One kind of conversion step.

    Writer names come from IDP-03 ch. 3.3. IDP-03 describes the readers into the document model but doesn't
    name them, so they are named ``<input>_to_model``.
    """

    OFFICE_TO_PDF = "office_to_pdf"  # LibreOffice: DOCX/XLSX/PPTX -> PDF
    XLSX_CSV = "xlsx_csv"  # XLSX <-> CSV, typed
    PDF_TO_IMAGES = "pdf_to_images"  # render pages
    IMAGES_TO_PDF = "images_to_pdf"  # lossless, no OCR
    IMAGE_FORMATS = "image_formats"  # PNG <-> JPEG
    SEARCHABLE_PDF = "searchable_pdf"  # OCR: image -> PDF with an invisible text layer
    PDF_TO_DOCX = "pdf_to_docx"  # digital PDF -> DOCX, layout rebuilt
    PPTX_FROM_IMAGES = "pptx_from_images"  # one slide per image
    PDF_TABLES = "pdf_tables"  # digital PDF tables -> document model
    OCR_TO_MODEL = "ocr_to_model"  # scanned PDF or image -> document model
    DOCX_TO_MODEL = "docx_to_model"
    SHEET_TO_MODEL = "sheet_to_model"  # XLSX or CSV
    PPTX_TO_MODEL = "pptx_to_model"
    MODEL_TO_DOCX = "model_to_docx"
    MODEL_TO_XLSX = "model_to_xlsx"
    MODEL_TO_CSV = "model_to_csv"
    MODEL_TO_PPTX = "model_to_pptx"


OCR_CONVERTERS: frozenset[Converter] = frozenset({Converter.SEARCHABLE_PDF, Converter.OCR_TO_MODEL})


class Fidelity(StrEnum):
    """How closely the output matches the input; shown to users so nobody is surprised (IDP-00 ch. 5)."""

    EXACT = "E"
    HIGH = "H"  # layout kept
    APPROXIMATE = "A"  # layout rebuilt
    DATA_ONLY = "D"  # tables and text, no layout


@dataclass(frozen=True, slots=True, kw_only=True)
class Step:
    """One converter run: reads ``source``, writes ``target``."""

    converter: Converter
    source: Representation
    target: Representation


@dataclass(frozen=True, slots=True, kw_only=True)
class Route:
    """The steps from ``source`` to ``target``. Each step reads what the previous one wrote."""

    source: Format
    target: Format
    steps: tuple[Step, ...]
    fidelity: Fidelity

    def __post_init__(self) -> None:
        if not self.steps:
            raise InvalidRouteError(f"{self.source} -> {self.target}: a route needs at least one step")
        current = Representation.of(self.source)
        for position, step in enumerate(self.steps, start=1):
            if step.source != current:
                raise InvalidRouteError(
                    f"{self.source} -> {self.target}: step {position} reads {step.source} but receives {current}"
                )
            current = step.target
        if current != Representation.of(self.target):
            raise InvalidRouteError(f"{self.source} -> {self.target}: the last step writes {current}")

    @property
    def converters(self) -> tuple[Converter, ...]:
        """The converters in the order they run."""
        return tuple(step.converter for step in self.steps)

    @property
    def uses_ocr(self) -> bool:
        return any(step.converter in OCR_CONVERTERS for step in self.steps)
