"""PP-OCR engines on the real models: the Latin text a page prints and the Devanagari text a Hindi card prints.

Skipped where ``OCR__MODELS_DIR`` has no models. The English page is drawn here, so no personal document is needed;
the Hindi check uses a card image that is already in the repository and asserts only on the script, not on names.
"""

import re
from pathlib import Path

import cv2
import numpy as np
import pytest

from extraction.core.config import get_settings
from idp_ocr_engines.engines.arrays import from_rgb_array
from idp_ocr_engines.engines.ppocr import PpOcrEngine, PpOcrModels
from idp_ocr_engines.errors import UnsupportedLanguageError
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.model import Box, PageImage
from idp_ocr_engines.registry import ModelRegistry

SETTINGS = get_settings().ocr
SERVICE_ROOT = Path(__file__).parents[3]
MODELS_PRESENT = (SETTINGS.models_dir / "PaddlePaddle-PP-OCRv6-small" / "rec" / "inference.onnx").is_file()
MARATHI_CARD = SERVICE_ROOT / "aadhaar_test_images" / "9a0167ef10d1a58334994e590fe66f59.webp"
DEVANAGARI = re.compile(r"[ऀ-ॿ]")

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MODELS_PRESENT, reason="the OCR models are not in OCR__MODELS_DIR"),
]

V5 = PpOcrModels(detector="ppocrv5-mobile-det", recognizer="ppocrv5-mobile-rec")
V5_HINDI = PpOcrModels(
    detector="ppocrv5-mobile-det", recognizer="ppocrv5-mobile-rec", extra_recognizers={"hi": "ppocrv5-devanagari-rec"}
)
V6 = PpOcrModels(detector="ppocrv6-small-det", recognizer="ppocrv6-small-rec")


def _engine(models: PpOcrModels) -> PpOcrEngine:
    limits = RuntimeLimits(threads=SETTINGS.threads, inference_timeout_seconds=60, max_pixels=SETTINGS.max_pixels)
    return PpOcrEngine.from_registry(
        engine_id="ocr-a",
        registry=ModelRegistry.from_file(SERVICE_ROOT / SETTINGS.models_file),
        models_dir=SETTINGS.models_dir,
        models=models,
        limits=limits,
    )


def _printed_page() -> PageImage:
    canvas = np.full((300, 900, 3), 255, dtype=np.uint8)
    cv2.putText(canvas, "ABCPK1234E", (40, 90), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (20, 20, 20), 4, cv2.LINE_AA)
    cv2.putText(canvas, "ARAVIND KUMAR", (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (20, 20, 20), 3, cv2.LINE_AA)
    return from_rgb_array(canvas)


def _card() -> PageImage:
    bgr = cv2.imread(str(MARATHI_CARD))
    return from_rgb_array(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))


@pytest.mark.parametrize("models", [V5, V6], ids=["ppocrv5-mobile", "ppocrv6-small"])
def test_reads_the_printed_text_in_order_when_the_page_has_two_lines(models: PpOcrModels) -> None:
    engine = _engine(models)
    engine.warm_up()

    page = engine.read_page(_printed_page(), ["en"])

    assert page.text.replace(" ", "") == "ABCPK1234E\nARAVINDKUMAR"
    assert [word.line_id for word in page.words] == [0, 1]
    assert all(word.confidence > 0.8 for word in page.words)


def test_reads_one_field_when_only_its_box_is_given() -> None:
    region = _engine(V6).read_region(_printed_page(), Box(0.03, 0.05, 0.6, 0.35), ["en"])

    assert region.text.replace(" ", "") == "ABCPK1234E"


def test_names_the_models_it_runs_when_the_version_is_asked() -> None:
    assert _engine(V5_HINDI).engine_version == (
        "ppocrv5-mobile-det@PP-OCRv5_mobile_det+ppocrv5-mobile-rec@PP-OCRv5_mobile_rec"
        "+ppocrv5-devanagari-rec@devanagari_PP-OCRv5_mobile_rec"
    )


def test_reads_devanagari_when_hindi_is_asked_on_a_card_that_prints_it() -> None:
    words = _engine(V5_HINDI).read_page(_card(), ["en", "hi"]).words

    assert sum(1 for word in words if DEVANAGARI.search(word.text)) >= 3


def test_reads_no_devanagari_when_only_english_is_asked() -> None:
    words = _engine(V5_HINDI).read_page(_card(), ["en"]).words

    assert not any(DEVANAGARI.search(word.text) for word in words)


def test_refuses_hindi_when_the_engine_has_no_devanagari_recogniser() -> None:
    with pytest.raises(UnsupportedLanguageError):
        _engine(V6).read_page(_printed_page(), ["en", "hi"])
