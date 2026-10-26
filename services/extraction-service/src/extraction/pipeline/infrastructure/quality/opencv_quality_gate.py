"""Measuring page quality with OpenCV (IDP-03 ch. 2.6 stage 2).

Scores are taken on the document itself: when its edges are found the document is cut out first, so a card on a dark
desk is not judged by the desk. Glare needs care. A plain share of saturated pixels (IDP-03 ch. 2.6) rates a white
page as 70% glare, since paper is saturated almost everywhere. Here a saturated area counts as glare only when its
edge is soft (light fading into the page) rather than the sharp edge of ink or of the paper.
"""

import cv2
import numpy as np
from numpy.typing import NDArray

from extraction.pipeline.domain.quality import QualityScores
from extraction.pipeline.infrastructure.preprocess.card_detector import detect_card, warp_to_card
from idp_ocr_engines.engines.arrays import to_rgb_array
from idp_ocr_engines.model import PageImage

_GLARE_WORK_SIDE = 800
_MIN_BLOB_FRACTION = 0.0005  # smaller saturated blobs are specks, not glare
_MIN_EDGE_PIXELS = 8  # a blob bounded mostly by the picture's edge has no edge of its own to judge
_SOBEL_SCALE = 1 / 8  # turns the Sobel response into grey levels per pixel


class OpenCvQualityAssessor:
    """Implements ``QualityAssessorPort``."""

    def __init__(
        self, *, min_card_area_fraction: float, saturation_level: int, glare_soft_edge_gradient: float
    ) -> None:
        self._min_card_area_fraction = min_card_area_fraction
        self._saturation_level = saturation_level
        self._soft_edge_gradient = glare_soft_edge_gradient

    def assess(self, image: PageImage) -> QualityScores:
        rgb = to_rgb_array(image)
        detection = detect_card(rgb, min_area_fraction=self._min_card_area_fraction)
        region = warp_to_card(rgb, detection.corners) if detection.corners is not None else rgb
        gray = np.asarray(cv2.cvtColor(region, cv2.COLOR_RGB2GRAY), dtype=np.uint8)
        return QualityScores(
            blur=float(cv2.Laplacian(gray, cv2.CV_64F).var()),
            brightness=float(gray.mean()) / 255.0,
            glare=glare_ratio(
                gray, saturation_level=self._saturation_level, soft_edge_gradient=self._soft_edge_gradient
            ),
            short_side_px=int(min(gray.shape)),
            edges_found=detection.corners is not None or detection.fills_frame,
        )


def glare_ratio(gray: NDArray[np.uint8], *, saturation_level: int, soft_edge_gradient: float) -> float:
    """The share of the picture covered by saturated areas whose edges fade softly."""
    scale = min(1.0, _GLARE_WORK_SIDE / max(gray.shape))
    small = (
        gray
        if scale >= 1
        else np.asarray(cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), dtype=np.uint8)
    )
    saturated = (small >= saturation_level).astype(np.uint8)
    if not saturated.any():
        return 0.0
    gradient = _gradient_magnitude(small)
    boundary = (saturated.astype(bool)) & ~cv2.erode(saturated, np.ones((3, 3), np.uint8)).astype(bool)
    boundary[0, :] = boundary[-1, :] = boundary[:, 0] = boundary[:, -1] = (
        False  # the picture's own edge is not a soft edge
    )
    count, labels, stats, _ = cv2.connectedComponentsWithStats(saturated, connectivity=8)
    minimum_area = _MIN_BLOB_FRACTION * small.size
    covered = sum(
        int(stats[label, cv2.CC_STAT_AREA])
        for label in range(1, count)
        if stats[label, cv2.CC_STAT_AREA] >= minimum_area
        and _has_soft_edge(labels == label, boundary, gradient, soft_edge_gradient)
    )
    return covered / small.size


def _gradient_magnitude(gray: NDArray[np.uint8]) -> NDArray[np.float32]:
    horizontal = cv2.Sobel(gray, cv2.CV_32F, 1, 0, ksize=3, scale=_SOBEL_SCALE)
    vertical = cv2.Sobel(gray, cv2.CV_32F, 0, 1, ksize=3, scale=_SOBEL_SCALE)
    return np.asarray(cv2.magnitude(horizontal, vertical), dtype=np.float32)


def _has_soft_edge(
    blob: NDArray[np.bool_], boundary: NDArray[np.bool_], gradient: NDArray[np.float32], limit: float
) -> bool:
    edge = blob & boundary
    return int(edge.sum()) >= _MIN_EDGE_PIXELS and float(np.median(gradient[edge])) < limit
