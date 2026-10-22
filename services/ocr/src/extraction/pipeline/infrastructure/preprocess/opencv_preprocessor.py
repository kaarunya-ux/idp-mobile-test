"""PREPARE: cut the document out, straighten it, turn it upright, reduce noise, even contrast (IDP-03 ch. 2.6 stage 3).

Every step is deterministic (ML-05): fixed interpolation, fixed parameters, explicit RGB order, no randomness. Steps
are mild on purpose: small letters and punctuation must survive. If the document cannot be cut out, the page is used
as it is.
"""

from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from extraction.pipeline.domain.models import PreparationSteps, PreparedPage
from extraction.pipeline.infrastructure.preprocess.card_detector import detect_card, warp_to_card
from idp_ocr_engines.engines.arrays import from_rgb_array, to_rgb_array
from idp_ocr_engines.model import PageImage

_SKEW_WORK_SIDE = 1000
_SKEW_LIMIT_DEGREES = 15.0  # steeper lines are page content, not the page's tilt
_SKEW_MIN_DEGREES = 0.3  # smaller tilts are not worth a resampling
_HOUGH_VOTES = 60
_HOUGH_MIN_LENGTH_FRACTION = 0.25
_BILATERAL = (5, 25, 5)  # diameter, colour sigma, space sigma: a gentle edge-preserving smoothing
_CLAHE_CLIP = 2.0
_CLAHE_TILES = (8, 8)
_THRESHOLD_BLOCK = 31
_THRESHOLD_OFFSET = 15
_QUARTER_TURN = 90


class OrientationPort(Protocol):
    """Anything that can say how far a page is turned clockwise."""

    def classify(self, rgb: NDArray[np.uint8]) -> tuple[int, float]: ...


class OpenCvPagePreparer:
    """Implements ``PagePreparerPort``."""

    def __init__(
        self, *, orientation: OrientationPort, min_card_area_fraction: float, orientation_min_confidence: float
    ) -> None:
        self._orientation = orientation
        self._min_card_area_fraction = min_card_area_fraction
        self._orientation_min_confidence = orientation_min_confidence

    def prepare(self, image: PageImage) -> PreparedPage:
        rgb = to_rgb_array(image)
        detection = detect_card(rgb, min_area_fraction=self._min_card_area_fraction)
        warped = detection.corners is not None
        page = warp_to_card(rgb, detection.corners) if detection.corners is not None else np.array(rgb)

        skew = estimate_skew_degrees(page)
        page = rotate_by(page, skew) if abs(skew) >= _SKEW_MIN_DEGREES else page
        degrees, confidence = self._orientation.classify(page)
        turned = degrees if confidence >= self._orientation_min_confidence else 0
        page = np.ascontiguousarray(np.rot90(page, turned // _QUARTER_TURN))

        clean = enhance_contrast(denoise(page))
        return PreparedPage(
            page_index=image.page_index,
            clean=from_rgb_array(clean, page_index=image.page_index),
            binarised=from_rgb_array(binarise(clean), page_index=image.page_index),
            steps=PreparationSteps(
                warped=warped,
                deskew_degrees=round(skew, 2) if abs(skew) >= _SKEW_MIN_DEGREES else 0.0,
                rotation_degrees=turned,
                orientation_confidence=round(confidence, 3),
            ),
        )


def estimate_skew_degrees(rgb: NDArray[np.uint8]) -> float:
    """The page's tilt in degrees, as the angle to rotate it by (counter-clockwise positive) to make lines horizontal.

    Uses the median angle of the long, nearly horizontal lines found with a Hough transform; 0 when there are none.
    """
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    scale = min(1.0, _SKEW_WORK_SIDE / max(gray.shape))
    small = cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA) if scale < 1 else gray
    edges = cv2.Canny(cv2.GaussianBlur(small, (3, 3), 0), 50, 150)
    lines = cv2.HoughLinesP(
        edges,
        1,
        np.pi / 180,
        _HOUGH_VOTES,
        minLineLength=int(_HOUGH_MIN_LENGTH_FRACTION * small.shape[1]),
        maxLineGap=10,
    )
    if lines is None:
        return 0.0
    segments = lines.reshape(-1, 4).astype(np.float64)
    angles = np.degrees(np.arctan2(segments[:, 3] - segments[:, 1], segments[:, 2] - segments[:, 0]))
    nearly_horizontal = angles[np.abs(angles) <= _SKEW_LIMIT_DEGREES]
    return float(np.median(nearly_horizontal)) if nearly_horizontal.size else 0.0


def rotate_by(rgb: NDArray[np.uint8], degrees: float) -> NDArray[np.uint8]:
    """Rotate counter-clockwise by ``degrees``, growing the canvas so no corner is cut off; new area copies the edge."""
    height, width = rgb.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    cosine, sine = abs(matrix[0, 0]), abs(matrix[0, 1])
    new_width, new_height = int(height * sine + width * cosine), int(height * cosine + width * sine)
    matrix[0, 2] += new_width / 2 - width / 2
    matrix[1, 2] += new_height / 2 - height / 2
    return np.asarray(
        cv2.warpAffine(rgb, matrix, (new_width, new_height), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE),
        dtype=np.uint8,
    )


def denoise(rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """A gentle edge-preserving smoothing that leaves letter edges sharp."""
    return np.asarray(cv2.bilateralFilter(rgb, *_BILATERAL), dtype=np.uint8)


def enhance_contrast(rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """CLAHE on the lightness channel only, so colours are not shifted."""
    lab = cv2.cvtColor(rgb, cv2.COLOR_RGB2LAB)
    lab[:, :, 0] = cv2.createCLAHE(clipLimit=_CLAHE_CLIP, tileGridSize=_CLAHE_TILES).apply(lab[:, :, 0])
    return np.asarray(cv2.cvtColor(lab, cv2.COLOR_LAB2RGB), dtype=np.uint8)


def binarise(rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """A black-and-white copy (adaptive threshold), stored as three identical channels."""
    gray = cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY)
    black_white = cv2.adaptiveThreshold(
        gray, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, _THRESHOLD_BLOCK, _THRESHOLD_OFFSET
    )
    return np.asarray(cv2.cvtColor(black_white, cv2.COLOR_GRAY2RGB), dtype=np.uint8)
