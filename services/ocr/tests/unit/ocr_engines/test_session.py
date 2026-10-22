"""Loading the real models: checksums match the registry, sessions run on CPU, and a slow run is stopped (ML-02, ML-06).

These need the model files, so they are skipped where ``OCR__MODELS_DIR`` has none (for example in CI before the
models are baked into the image).
"""

from pathlib import Path

import numpy as np
import pytest

from extraction.core.config import get_settings
from idp_ocr_engines.errors import InferenceTimeoutError
from idp_ocr_engines.registry import ModelRegistry
from idp_ocr_engines.session import create_session, run_session

SETTINGS = get_settings().ocr
REGISTRY_FILE = Path(__file__).parents[3] / SETTINGS.models_file
MODELS_PRESENT = (SETTINGS.models_dir / "PaddlePaddle-PP-OCRv5-mobile" / "det" / "inference.onnx").is_file()

pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(not MODELS_PRESENT, reason="the OCR models are not in OCR__MODELS_DIR"),
]

REGISTERED = (
    "easyocr-detector",
    "easyocr-recognizer",
    "ppocrv5-mobile-det",
    "ppocrv5-mobile-rec",
    "ppocrv5-devanagari-rec",
    "ppocrv6-small-det",
    "ppocrv6-small-rec",
)
INDRA_PRESENT = (SETTINGS.models_dir / "indra-ocr-v1.1.0" / "det.onnx").is_file()


def test_matches_every_recorded_checksum_when_the_indra_model_set_is_verified() -> None:
    if not INDRA_PRESENT:
        pytest.skip("the Indra model set is not in OCR__MODELS_DIR")

    assert ModelRegistry.from_file(REGISTRY_FILE).verify("indra-ocr", SETTINGS.models_dir).entry_path.is_file()


@pytest.mark.parametrize("name", REGISTERED)
def test_matches_every_recorded_checksum_when_a_registered_model_is_verified(name: str) -> None:
    model = ModelRegistry.from_file(REGISTRY_FILE).verify(name, SETTINGS.models_dir)

    assert model.entry_path.is_file()


def test_runs_the_detector_on_the_cpu_when_given_a_blank_page() -> None:
    model = ModelRegistry.from_file(REGISTRY_FILE).verify("ppocrv5-mobile-det", SETTINGS.models_dir)
    session = create_session(model.entry_path, threads=SETTINGS.threads)

    outputs = run_session(session, {"x": np.zeros((1, 3, 64, 64), np.float32)}, timeout_seconds=30)

    assert session.get_providers() == ["CPUExecutionProvider"]
    assert outputs[0].shape[-2:] == (64, 64)


def test_stops_the_run_and_raises_when_the_timeout_is_far_too_short() -> None:
    model = ModelRegistry.from_file(REGISTRY_FILE).verify("ppocrv5-mobile-det", SETTINGS.models_dir)
    session = create_session(model.entry_path, threads=1)

    with pytest.raises(InferenceTimeoutError):
        run_session(session, {"x": np.zeros((1, 3, 2000, 2000), np.float32)}, timeout_seconds=0.001)
