"""Text detection with a DB (differentiable binarisation) model, as used by PP-OCRv5 and v6 (IDP-03 ch. 2.8).

The model turns a page into a probability map; contours of the probable-text area become boxes, which are grown
slightly (``unclip``) because the map is a shrunken version of each text line. Every step is deterministic (ML-05):
fixed interpolation, explicit BGR colour order, no randomness.
"""

from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np
import onnxruntime as ort
from numpy.typing import NDArray

from idp_ocr_engines.errors import ModelFileError
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.paddle_config import read_post_process
from idp_ocr_engines.session import run_session

_MEAN = np.array([0.485, 0.456, 0.406], dtype=np.float32)  # applied to BGR, as PaddleOCR does
_STD = np.array([0.229, 0.224, 0.225], dtype=np.float32)
_SMALL_SIDE_TARGET = 960  # pages smaller than this are enlarged to it
_LARGE_SIDE_CAP = 2000  # pages larger than this are shrunk to it
_GRID = 32  # the model needs both sides to be multiples of 32
_MIN_BOX_SIDE = 3.0  # map pixels; smaller blobs are noise
_CORNERS = 4


@dataclass(frozen=True, slots=True, kw_only=True)
class DbThresholds:
    """The detector's own post-processing values, read from its ``inference.yml``."""

    threshold: float
    box_threshold: float
    unclip_ratio: float


def load_db_thresholds(config_path: Path) -> DbThresholds:
    """Read ``PostProcess`` from a PaddleOCR detector's ``inference.yml``."""
    post = read_post_process(config_path)
    try:
        return DbThresholds(
            threshold=_number(post["thresh"]),
            box_threshold=_number(post["box_thresh"]),
            unclip_ratio=_number(post["unclip_ratio"]),
        )
    except (KeyError, ValueError) as error:
        message = "The detector's inference.yml lacks thresh, box_thresh or unclip_ratio."
        raise ModelFileError(message) from error


def _number(value: str | list[str]) -> float:
    if isinstance(value, list):
        raise ValueError
    return float(value)


class DbDetector:
    """Finds text lines on a page and returns each as four corner points in page pixels."""

    def __init__(self, session: ort.InferenceSession, thresholds: DbThresholds, limits: RuntimeLimits) -> None:
        self._session = session
        self._thresholds = thresholds
        self._limits = limits

    def detect(self, rgb: NDArray[np.uint8]) -> list[NDArray[np.float32]]:
        """Corner points (top-left, top-right, bottom-right, bottom-left) of every text line found."""
        tensor, scale_x, scale_y = _prepare_input(rgb)
        outputs = run_session(self._session, {"x": tensor}, timeout_seconds=self._limits.inference_timeout_seconds)
        probability = np.asarray(outputs[0])[0, 0]
        quads: list[NDArray[np.float32]] = []
        for contour in _find_contours(probability, self._thresholds.threshold):
            quad = _quad_for(contour, probability, self._thresholds)
            if quad is not None:
                quads.append(_order_corners(quad * np.array([scale_x, scale_y], dtype=np.float32)))
        return quads

    def warm_up(self) -> None:
        """Run a blank page through the model (ML-03)."""
        self.detect(np.full((_GRID * 4, _GRID * 4, 3), 255, dtype=np.uint8))


def _prepare_input(rgb: NDArray[np.uint8]) -> tuple[NDArray[np.float32], float, float]:
    """Resize to multiples of 32, convert to normalised BGR, and return the map-to-page scale factors."""
    height, width = rgb.shape[:2]
    longest = max(height, width)
    factor = _SMALL_SIDE_TARGET / longest if longest < _SMALL_SIDE_TARGET else min(1.0, _LARGE_SIDE_CAP / longest)
    new_height = max(_GRID, round(height * factor / _GRID) * _GRID)
    new_width = max(_GRID, round(width * factor / _GRID) * _GRID)
    resized = cv2.resize(cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR), (new_width, new_height), interpolation=cv2.INTER_LINEAR)
    normalised = ((resized.astype(np.float32) / 255.0 - _MEAN) / _STD).transpose(2, 0, 1)[np.newaxis]
    return np.ascontiguousarray(normalised), width / new_width, height / new_height


def _find_contours(probability: NDArray[np.float32], threshold: float) -> list[NDArray[np.int32]]:
    binary = (probability > threshold).astype(np.uint8)
    contours, _ = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
    return [np.asarray(contour, dtype=np.int32) for contour in contours]


def _quad_for(
    contour: NDArray[np.int32], probability: NDArray[np.float32], thresholds: DbThresholds
) -> NDArray[np.float32] | None:
    """The grown box around one contour in map pixels, or ``None`` if it is too small or not probable enough."""
    (center_x, center_y), (width, height), angle = cv2.minAreaRect(contour)
    if min(width, height) < _MIN_BOX_SIDE:
        return None
    mask = np.zeros(probability.shape, dtype=np.uint8)
    cv2.fillPoly(mask, [contour.reshape(-1, 2)], 1)
    if float(probability[mask > 0].mean()) < thresholds.box_threshold:
        return None
    growth = width * height * thresholds.unclip_ratio / (2 * (width + height))
    return np.asarray(
        cv2.boxPoints(((center_x, center_y), (width + 2 * growth, height + 2 * growth), angle)), dtype=np.float32
    )


def _order_corners(points: NDArray[np.float32]) -> NDArray[np.float32]:
    """Order four points as top-left, top-right, bottom-right, bottom-left."""
    by_x = points[np.argsort(points[:, 0])]
    left, right = by_x[:2], by_x[2:]
    top_left, bottom_left = left[np.argsort(left[:, 1])]
    top_right, bottom_right = right[np.argsort(right[:, 1])]
    return np.array([top_left, top_right, bottom_right, bottom_left], dtype=np.float32)


def crop_quad(rgb: NDArray[np.uint8], corners: NDArray[np.float32]) -> NDArray[np.uint8]:
    """Cut one text line out of the page, straightened. A box taller than wide is turned so the text runs sideways."""
    top_left, top_right, bottom_right, bottom_left = corners
    across = max(float(np.linalg.norm(top_right - top_left)), float(np.linalg.norm(bottom_right - bottom_left)))
    down = max(float(np.linalg.norm(bottom_left - top_left)), float(np.linalg.norm(bottom_right - top_right)))
    oriented = corners if across >= down else np.roll(corners, -1, axis=0)
    width, height = (round(max(across, down)), round(min(across, down)))
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(oriented, target)
    return np.asarray(
        cv2.warpPerspective(
            rgb, matrix, (max(width, 1), max(height, 1)), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE
        ),
        dtype=np.uint8,
    )
