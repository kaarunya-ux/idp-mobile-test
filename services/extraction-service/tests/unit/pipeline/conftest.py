"""Synthetic pages shared by the pipeline tests. Nothing here is a real document."""

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

CARD_TEXT = (
    "INCOME TAX DEPARTMENT",
    "Permanent Account Number Card",
    "ABCPK1234E",
    "Name  ARAVIND KUMAR",
    "Date of Birth  14/03/1992",
)


def _draw_lines(
    canvas: NDArray[np.uint8], lines: tuple[str, ...], *, top: int, step: int, scale: float, left: int = 40
) -> None:
    for index, text in enumerate(lines):
        cv2.putText(
            canvas, text, (left, top + index * step), cv2.FONT_HERSHEY_SIMPLEX, scale, (20, 20, 20), 2, cv2.LINE_AA
        )


@pytest.fixture
def text_page() -> NDArray[np.uint8]:
    """A white 1000 x 700 page with eight long lines of text, like a scanned letter."""
    canvas = np.full((700, 1000, 3), 255, dtype=np.uint8)
    lines = tuple(f"Line {number}: the quick brown fox jumps over the lazy dog 0123456789" for number in range(1, 9))
    _draw_lines(canvas, lines, top=80, step=75, scale=0.9)
    return canvas


@pytest.fixture
def card() -> NDArray[np.uint8]:
    """A light-blue 860 x 540 card with printed text."""
    canvas = np.full((540, 860, 3), (200, 225, 245), dtype=np.uint8)
    _draw_lines(canvas, CARD_TEXT, top=120, step=90, scale=1.1, left=60)
    return canvas


@pytest.fixture
def framed_card(card: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """The same card scanned right up to its printed border, so the picture's own edge is the document's edge."""
    framed = card.copy()
    cv2.rectangle(framed, (2, 2), (857, 537), (60, 60, 60), 4)
    return framed


@pytest.fixture
def card_photo(card: NDArray[np.uint8]) -> NDArray[np.uint8]:
    """The card photographed on a dark desk: 1400 x 1000, seen slightly from the side."""
    desk = np.full((1000, 1400, 3), (55, 50, 45), dtype=np.uint8)
    source = np.float32([[0, 0], [860, 0], [860, 540], [0, 540]])
    target = np.float32([[260, 190], [1130, 150], [1170, 760], [220, 700]])
    matrix = cv2.getPerspectiveTransform(source, target)
    warped = cv2.warpPerspective(card, matrix, (1400, 1000))
    mask = cv2.warpPerspective(np.full(card.shape[:2], 255, np.uint8), matrix, (1400, 1000)) > 0
    desk[mask] = warped[mask]
    return desk
