"""The real orientation model, and the whole prepare chain on a real card that was photographed sideways.

Skipped where ``OCR__MODELS_DIR`` has no models. Uses card images that are already in the repository.
"""

from pathlib import Path

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from extraction.core.config import get_settings
from extraction.pipeline.application.commands import PrepareDocumentCommand
from extraction.pipeline.application.services.prepare_document import PrepareDocumentService
from extraction.pipeline.domain.quality import QualityThresholds
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.infrastructure.preprocess.opencv_preprocessor import OpenCvPagePreparer
from extraction.pipeline.infrastructure.preprocess.orientation import DocumentOrientationClassifier
from extraction.pipeline.infrastructure.quality.opencv_quality_gate import OpenCvQualityAssessor
from extraction.pipeline.infrastructure.quality.page_loader import DocumentPageLoader
from idp_ocr_engines import ModelRegistry, RuntimeLimits

SETTINGS = get_settings()
SERVICE_ROOT = Path(__file__).parents[4]
MODEL_PRESENT = (SETTINGS.ocr.models_dir / "PaddlePaddle-PP-LCNet-doc-ori" / "inference.onnx").is_file()
CARDS = ("1aea9462babd9fb2fd84f7a2f81f5ccf", "9bf222f6ac90b212dc9f6f46dcd3470e")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MODEL_PRESENT, reason="the orientation model is not in OCR__MODELS_DIR"),
]


@pytest.fixture(scope="module")
def classifier() -> DocumentOrientationClassifier:
    return DocumentOrientationClassifier.from_registry(
        registry=ModelRegistry.from_file(SERVICE_ROOT / SETTINGS.ocr.models_file),
        models_dir=SETTINGS.ocr.models_dir,
        model_name="doc-orientation",
        limits=RuntimeLimits(threads=SETTINGS.ocr.threads),
    )


def _card(name: str) -> NDArray[np.uint8]:
    return cv2.cvtColor(cv2.imread(str(SERVICE_ROOT / "pan_test_images" / f"{name}.webp")), cv2.COLOR_BGR2RGB)


# np.rot90(page, k) turns the page k quarter turns counter-clockwise: a clockwise rotation of (4 - k) * 90 degrees
@pytest.mark.parametrize("name", CARDS)
@pytest.mark.parametrize(("quarter_turns", "clockwise_degrees"), [(0, 0), (1, 270), (2, 180), (3, 90)])
def test_tells_how_far_a_card_is_turned_when_it_is_rotated(
    classifier: DocumentOrientationClassifier, name: str, quarter_turns: int, clockwise_degrees: int
) -> None:
    degrees, confidence = classifier.classify(np.ascontiguousarray(np.rot90(_card(name), quarter_turns)))

    assert degrees == clockwise_degrees
    assert confidence > 0.6


def test_runs_a_blank_page_through_the_model_when_warming_up(classifier: DocumentOrientationClassifier) -> None:
    classifier.warm_up()


class RecordingProgress:
    def __init__(self) -> None:
        self.stages: list[Stage] = []

    def report(self, stage: Stage, percent: int) -> None:
        self.stages.append(stage)


def test_prepares_a_sideways_card_upright_when_the_whole_chain_runs_on_the_real_models(
    classifier: DocumentOrientationClassifier,
) -> None:
    sideways = np.ascontiguousarray(np.rot90(_card(CARDS[0]), 1))
    encoded = cv2.imencode(".png", cv2.cvtColor(sideways, cv2.COLOR_RGB2BGR))[1].tobytes()
    progress = RecordingProgress()
    limits = SETTINGS.limits
    service = PrepareDocumentService(
        loader=DocumentPageLoader(
            max_file_bytes=int(limits.max_file_size),
            max_pages=limits.max_pages,
            max_pixels=limits.max_image_pixels,
            pdf_dpi=limits.pdf_dpi,
        ),
        assessor=OpenCvQualityAssessor(
            min_card_area_fraction=SETTINGS.prepare.min_card_area_fraction,
            saturation_level=SETTINGS.quality.saturation_level,
            glare_soft_edge_gradient=SETTINGS.quality.glare_soft_edge_gradient,
        ),
        preparer=OpenCvPagePreparer(
            orientation=classifier,
            min_card_area_fraction=SETTINGS.prepare.min_card_area_fraction,
            orientation_min_confidence=SETTINGS.prepare.orientation_min_confidence,
        ),
        thresholds=QualityThresholds(
            blur_min=SETTINGS.quality.blur_min,
            brightness_min=0.25,
            brightness_max=0.90,
            glare_max=0.04,
            min_short_side_px=300,
            require_document_edges=False,
        ),
        progress=progress,
    )

    result = service.prepare(PrepareDocumentCommand(content=encoded))

    page = result.pages[0]
    assert page.steps.rotation_degrees == 270
    assert page.clean.width > page.clean.height  # a landscape card again
    assert progress.stages[0] is Stage.SCAN
    assert progress.stages[-1] is Stage.PREPARE
