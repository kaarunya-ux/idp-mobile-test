"""Page orientation with PaddleOCR's ``PP-LCNet_x1_0_doc_ori`` model: is the page turned 0, 90, 180 or 270 degrees?

The model is registered in ``models.yaml`` (ML-01). In our check on 76 rotated card images it was right 75 times;
its confidence is returned so the caller can ignore a weak answer.
"""

from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray

from idp_ocr_engines.ctc import to_probabilities
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.registry import ModelRegistry
from idp_ocr_engines.session import create_session, run_session

_SHORT_SIDE = 256  # the model's own preprocessing: resize the short side, then take the centre 224 x 224
_CROP = 224
_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_QUARTER_TURN = 90


class DocumentOrientationClassifier:
    """Says how far a page is turned clockwise (0, 90, 180 or 270) and how sure the model is."""

    def __init__(self, session: ort.InferenceSession, limits: RuntimeLimits) -> None:
        self._session = session
        self._input_name: str = session.get_inputs()[0].name
        self._limits = limits

    @classmethod
    def from_registry(
        cls, *, registry: ModelRegistry, models_dir: Path, model_name: str, limits: RuntimeLimits
    ) -> "DocumentOrientationClassifier":
        """Verify the model's checksums and load it on the CPU (ML-01, ML-02)."""
        model = registry.verify(model_name, models_dir)
        return cls(create_session(model.entry_path, threads=limits.threads), limits)

    def classify(self, rgb: NDArray[np.uint8]) -> tuple[int, float]:
        """The clockwise rotation of the page in degrees and the model's confidence (0 to 1)."""
        outputs = run_session(
            self._session, {self._input_name: _to_tensor(rgb)}, timeout_seconds=self._limits.inference_timeout_seconds
        )
        probabilities = to_probabilities(np.asarray(outputs[0], dtype=np.float32))[0]
        return int(probabilities.argmax()) * _QUARTER_TURN, float(probabilities.max())

    def warm_up(self) -> None:
        """Classify a blank page (ML-03)."""
        self.classify(np.full((_SHORT_SIDE, _SHORT_SIDE, 3), 255, dtype=np.uint8))


def _to_tensor(rgb: NDArray[np.uint8]) -> NDArray[np.float32]:
    height, width = rgb.shape[:2]
    factor = _SHORT_SIDE / min(height, width)
    resized = cv2.resize(
        rgb, (max(_CROP, round(width * factor)), max(_CROP, round(height * factor))), interpolation=cv2.INTER_LINEAR
    )
    top, left = (resized.shape[0] - _CROP) // 2, (resized.shape[1] - _CROP) // 2
    crop = resized[top : top + _CROP, left : left + _CROP]
    tensor = ((crop.astype(np.float32) / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)[np.newaxis]
    return np.ascontiguousarray(tensor)
