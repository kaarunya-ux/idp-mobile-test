"""Run the extraction pipeline on image files and print what it found: ``uv run python scripts/try_extract.py FILE...``.

A manual check, not part of the service. It runs the PREPARE stage first (``--raw`` skips it and reads the image as it
is; the quality gate is not applied) and then reads each image with
PP-OCRv6 in slot A and PP-OCRv5 (English and Hindi) in slot B. Slot A reads English only, so Hindi fields have one
reader and come out NEEDS_REVIEW until a second Hindi engine is chosen (Indra is parked, see docs/adr/0002).
It prints one block per file. Aadhaar numbers are masked by the template.
"""

import sys
from datetime import UTC, datetime
from pathlib import Path

import cv2

from extraction.core.config import Settings, get_settings
from extraction.pipeline.application.commands import ExtractDocumentCommand
from extraction.pipeline.application.services.extract_document import ExtractDocumentService
from extraction.pipeline.domain.decisions import Thresholds
from extraction.pipeline.domain.models import PreparationSteps, PreparedPage
from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.domain.standard_templates import STANDARD_TEMPLATES
from extraction.pipeline.infrastructure.preprocess.opencv_preprocessor import OpenCvPagePreparer
from extraction.pipeline.infrastructure.preprocess.orientation import DocumentOrientationClassifier
from extraction.pipeline.infrastructure.quality.page_loader import DocumentPageLoader
from idp_ocr_engines import ModelRegistry, RuntimeLimits
from idp_ocr_engines.engines.arrays import from_rgb_array
from idp_ocr_engines.engines.ppocr import PpOcrEngine, PpOcrModels
from idp_ocr_engines.port import OcrEnginePort


class _Clock:
    def now(self) -> datetime:
        return datetime.now(UTC)


class _NoProgress:
    def report(self, stage: Stage, percent: int) -> None:
        """Progress is not shown."""


def main(files: list[str], *, prepare: bool) -> None:
    settings = get_settings()
    registry = ModelRegistry.from_file(Path("models.yaml"))

    def engine(slot: str, detector: str, recognizer: str, extra: dict[str, str] | None = None) -> PpOcrEngine:
        return PpOcrEngine.from_registry(
            engine_id=slot,
            registry=registry,
            models_dir=settings.ocr.models_dir,
            models=PpOcrModels(detector=detector, recognizer=recognizer, extra_recognizers=extra or {}),
            limits=RuntimeLimits(threads=settings.ocr.threads),
        )

    engines: dict[str, OcrEnginePort] = {
        "A": engine("ocr-a", "ppocrv6-small-det", "ppocrv6-small-rec"),
        "B": engine("ocr-b", "ppocrv5-mobile-det", "ppocrv5-mobile-rec", {"hi": "ppocrv5-devanagari-rec"}),
    }
    service = ExtractDocumentService(
        engines=engines,
        languages={"B": ("en", "hi")},
        templates=STANDARD_TEMPLATES,
        thresholds=Thresholds(
            name_similarity=settings.verify.name_similarity, review_confidence=settings.review.confidence_threshold
        ),
        clock=_Clock(),
        pipeline_version="0.1.0",
        progress=_NoProgress(),
    )
    quality = QualityScores(blur=0.0, brightness=0.0, glare=0.0, short_side_px=0, edges_found=True)
    steps = PreparationSteps(warped=False, deskew_degrees=0.0, rotation_degrees=0, orientation_confidence=None)
    preparer = _build_preparer(registry, settings) if prepare else None
    loader = DocumentPageLoader(
        max_file_bytes=settings.limits.max_file_size,
        max_pages=settings.limits.max_pages,
        max_pixels=settings.limits.max_image_pixels,
        pdf_dpi=settings.limits.pdf_dpi,
    )
    for file in files:
        if preparer is not None:  # the PREPARE stage: cut out the card, straighten it, turn it upright, clean it
            page = preparer.prepare(loader.load_pages(Path(file).read_bytes())[0])
        else:
            image = from_rgb_array(cv2.cvtColor(cv2.imread(file), cv2.COLOR_BGR2RGB))
            page = PreparedPage(page_index=0, clean=image, binarised=image, steps=steps)
        result = service.extract(ExtractDocumentCommand(pages=(page,), qualities=(quality,)))
        _show(file, result)


def _build_preparer(registry: ModelRegistry, settings: Settings) -> OpenCvPagePreparer:
    orientation = DocumentOrientationClassifier.from_registry(
        registry=registry,
        models_dir=settings.ocr.models_dir,
        model_name="doc-orientation",
        limits=RuntimeLimits(threads=settings.ocr.threads),
    )
    return OpenCvPagePreparer(
        orientation=orientation,
        min_card_area_fraction=settings.prepare.min_card_area_fraction,
        orientation_min_confidence=settings.prepare.orientation_min_confidence,
    )


def _show(file: str, result: dict[str, object]) -> None:
    out = sys.stdout.write
    document: dict[str, object] = result["documentType"]  # type: ignore[assignment]
    validation: dict[str, object] = result["validation"]  # type: ignore[assignment]
    out(
        f"\n== {Path(file).name}\n   type: {document['code']}"
        f"   accepted: {validation['accepted']}/{validation['fieldsTotal']}\n"
    )
    for field in result["fields"]:  # type: ignore[attr-defined]
        out(f"   {field['key']:<15} {field['status']:<13} conf {field['confidence']:<6} {field['normalizedValue']}\n")
        if field["status"] != "ACCEPTED":
            reads = "   ".join(f"{slot} read: {field['readings'][slot]!r}" for slot in ("A", "B", "C"))
            out(f"   {'':<15} {reads}\n")


if __name__ == "__main__":
    flags = {"--raw"}
    main(
        [arg for arg in sys.argv[1:] if arg not in flags],
        prepare="--raw" not in sys.argv,
    )
