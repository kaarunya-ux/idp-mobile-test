"""Finding a document (an ID card or a page) in a photo, and cutting it out straight (IDP-03 ch. 2.6 stage 3)."""

from dataclasses import dataclass

import cv2
import numpy as np
from numpy.typing import NDArray

_WORK_SIDE = 800  # the search runs on a copy no bigger than this, for speed
_ASPECT_RANGE = (1.3, 1.9)  # ID cards are 1.586; an A4 page is 1.414
_FILLS_FRAME_FRACTION = 0.92  # a shape covering more than this is the picture's own edge: the image is the document
_SOLIDITY_MIN = 0.85  # how much of the quadrilateral the shape must fill
_CONTOURS_TRIED = 5
_CORNER_COUNT = 4
_CANNY = (30, 100)


@dataclass(frozen=True, slots=True)
class CardDetection:
    """What looking for a document found: its four corners in page pixels, or that the image already is the document."""

    corners: NDArray[np.float32] | None
    fills_frame: bool


def detect_card(rgb: NDArray[np.uint8], *, min_area_fraction: float) -> CardDetection:
    """The largest card-shaped quadrilateral covering at least ``min_area_fraction`` of the picture."""
    height, width = rgb.shape[:2]
    scale = min(1.0, _WORK_SIDE / max(height, width))
    small = _shrink(rgb, scale)
    area = small.shape[0] * small.shape[1]
    for contour in _edge_contours(small)[:_CONTOURS_TRIED]:
        fraction = cv2.contourArea(contour) / area
        if fraction < min_area_fraction:
            break
        if fraction > _FILLS_FRAME_FRACTION:
            return CardDetection(corners=None, fills_frame=True)
        quad = _as_card_quad(contour)
        if quad is not None:
            return CardDetection(corners=(quad / scale).astype(np.float32), fills_frame=False)
    return CardDetection(corners=None, fills_frame=False)


def warp_to_card(rgb: NDArray[np.uint8], corners: NDArray[np.float32]) -> NDArray[np.uint8]:
    """The document cut out of the photo and straightened to a rectangle, keeping its own orientation."""
    top_left, top_right, bottom_right, bottom_left = corners
    width = round(max(float(np.linalg.norm(top_right - top_left)), float(np.linalg.norm(bottom_right - bottom_left))))
    height = round(max(float(np.linalg.norm(bottom_left - top_left)), float(np.linalg.norm(bottom_right - top_right))))
    target = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(corners, target)
    return np.asarray(
        cv2.warpPerspective(rgb, matrix, (max(width, 1), max(height, 1)), flags=cv2.INTER_CUBIC), dtype=np.uint8
    )


def _shrink(rgb: NDArray[np.uint8], scale: float) -> NDArray[np.uint8]:
    if scale >= 1:
        return rgb
    return np.asarray(cv2.resize(rgb, None, fx=scale, fy=scale, interpolation=cv2.INTER_AREA), dtype=np.uint8)


def _edge_contours(small: NDArray[np.uint8]) -> list[NDArray[np.int32]]:
    gray = cv2.GaussianBlur(cv2.cvtColor(small, cv2.COLOR_RGB2GRAY), (5, 5), 0)
    edges = cv2.dilate(cv2.Canny(gray, *_CANNY), np.ones((3, 3), np.uint8), iterations=2)
    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    found = [np.asarray(contour, dtype=np.int32) for contour in contours]
    return sorted(found, key=cv2.contourArea, reverse=True)


def _as_card_quad(contour: NDArray[np.int32]) -> NDArray[np.float32] | None:
    hull = cv2.convexHull(contour)
    approximation = cv2.approxPolyDP(hull, 0.02 * cv2.arcLength(hull, True), True)
    points = (
        approximation.reshape(-1, 2) if len(approximation) == _CORNER_COUNT else cv2.boxPoints(cv2.minAreaRect(hull))
    )
    quad = _order_corners(np.asarray(points, dtype=np.float32))
    across = max(float(np.linalg.norm(quad[1] - quad[0])), float(np.linalg.norm(quad[2] - quad[3])))
    down = max(float(np.linalg.norm(quad[3] - quad[0])), float(np.linalg.norm(quad[2] - quad[1])))
    aspect = max(across, down) / max(1.0, min(across, down))
    solidity = cv2.contourArea(hull) / max(1.0, cv2.contourArea(quad))
    if _ASPECT_RANGE[0] <= aspect <= _ASPECT_RANGE[1] and solidity > _SOLIDITY_MIN:
        return quad
    return None


def _order_corners(points: NDArray[np.float32]) -> NDArray[np.float32]:
    """Top-left, top-right, bottom-right, bottom-left, from the sums and differences of the coordinates."""
    sums = points.sum(axis=1)
    differences = np.diff(points, axis=1).ravel()
    return np.array(
        [points[sums.argmin()], points[differences.argmin()], points[sums.argmax()], points[differences.argmax()]],
        dtype=np.float32,
    )
