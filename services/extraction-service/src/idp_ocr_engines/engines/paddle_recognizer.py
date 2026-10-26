"""Text recognition with a PaddleOCR CTC recogniser (PP-OCRv5, v6 and the Devanagari model)."""

from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray

from idp_ocr_engines.ctc import decode_ctc_greedy
from idp_ocr_engines.errors import OcrEngineError
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.paddle_config import read_post_process
from idp_ocr_engines.session import run_session

_HEIGHT = 48
_MIN_WIDTH = 16
_MAX_WIDTH = 3200


def load_charset(config_path: Path) -> tuple[str, ...]:
    """The classes: a blank, the character dictionary, then the space. The model fixes this order."""
    characters = read_post_process(config_path).get("character_dict")
    if not isinstance(characters, list) or not characters:
        message = "The recogniser's inference.yml has no character dictionary."
        raise OcrEngineError(message)
    return ("", *characters, " ")


class PaddleRecognizer:
    """Reads one cropped text line into ``(text, confidence)``."""

    def __init__(self, session: ort.InferenceSession, charset: tuple[str, ...], limits: RuntimeLimits) -> None:
        self._session = session
        self._charset = charset
        self._limits = limits

    def recognize(self, crop_rgb: NDArray[np.uint8]) -> tuple[str, float]:
        """Decode the text on a crop."""
        return decode_ctc_greedy(self._scores(crop_rgb), self._charset)

    def warm_up(self) -> None:
        """Run a blank crop through the model and check the character set fits its output (ML-03, ML-04)."""
        scores = self._scores(np.full((_HEIGHT, _MAX_WIDTH // 10, 3), 255, dtype=np.uint8))
        if scores.shape[-1] != len(self._charset):
            message = "The recogniser's character set does not match its model output."
            raise OcrEngineError(message)

    def _scores(self, crop_rgb: NDArray[np.uint8]) -> NDArray[np.float32]:
        """Run the model: crops are scaled to 48 pixels high keeping their shape, as BGR (ML-05)."""
        height, width = crop_rgb.shape[:2]
        new_width = int(min(_MAX_WIDTH, max(_MIN_WIDTH, np.ceil(_HEIGHT * width / height))))
        resized = cv2.resize(
            cv2.cvtColor(crop_rgb, cv2.COLOR_RGB2BGR), (new_width, _HEIGHT), interpolation=cv2.INTER_LINEAR
        )
        tensor = ((resized.astype(np.float32) / 255.0 - 0.5) / 0.5).transpose(2, 0, 1)[np.newaxis]
        outputs = run_session(
            self._session, {"x": np.ascontiguousarray(tensor)}, timeout_seconds=self._limits.inference_timeout_seconds
        )
        scores: NDArray[np.float32] = np.asarray(outputs[0], dtype=np.float32)[0]
        return scores
