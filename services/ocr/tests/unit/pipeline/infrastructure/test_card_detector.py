"""Finding a card in a photo: corners, the case where the image already is the card, and straightening."""

import numpy as np
import pytest
from numpy.typing import NDArray

from extraction.pipeline.infrastructure.preprocess.card_detector import detect_card, warp_to_card

TRUE_CORNERS = np.float32([[260, 190], [1130, 150], [1170, 760], [220, 700]])


def test_finds_the_four_corners_when_a_card_lies_on_a_dark_desk(card_photo: NDArray[np.uint8]) -> None:
    detection = detect_card(card_photo, min_area_fraction=0.15)

    assert detection.corners is not None
    assert not detection.fills_frame
    assert np.abs(detection.corners - TRUE_CORNERS).max() < 25


def test_says_the_image_is_the_document_when_the_card_fills_the_frame(framed_card: NDArray[np.uint8]) -> None:
    detection = detect_card(framed_card, min_area_fraction=0.15)

    assert detection.corners is None
    assert detection.fills_frame


def test_finds_nothing_when_the_picture_is_one_flat_colour() -> None:
    detection = detect_card(np.full((600, 800, 3), 128, dtype=np.uint8), min_area_fraction=0.15)

    assert detection.corners is None
    assert not detection.fills_frame


def test_finds_nothing_when_the_card_is_smaller_than_the_minimum_area(card_photo: NDArray[np.uint8]) -> None:
    detection = detect_card(card_photo, min_area_fraction=0.8)

    assert detection.corners is None


def test_cuts_the_card_out_with_its_own_shape_when_it_is_warped(card_photo: NDArray[np.uint8]) -> None:
    corners = detect_card(card_photo, min_area_fraction=0.15).corners
    assert corners is not None

    flat = warp_to_card(card_photo, corners)

    height, width = flat.shape[:2]
    assert width / height == pytest.approx(860 / 540, rel=0.12)
    assert flat.mean() > 150  # the light card, not the dark desk
