"""Finding a card in a photo: corners, the case where the image already is the card, and straightening."""

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from extraction.pipeline.infrastructure.preprocess.card_detector import (
    TwoCards,
    detect_card,
    find_two_cards,
    warp_to_card,
)

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


TWO_CARDS = TwoCards(min_area_fraction=0.15, min_size_ratio=0.7)


def _on_white(card: NDArray[np.uint8], places: list[tuple[int, int]], size: tuple[int, int]) -> NDArray[np.uint8]:
    """``card`` printed at each (left, top) of a white page of ``size`` (width, height), like a scanned photocopy."""
    page = np.full((size[1], size[0], 3), 255, dtype=np.uint8)
    for left, top in places:
        page[top : top + card.shape[0], left : left + card.shape[1]] = card
        page[top : top + 3, left : left + card.shape[1]] = 90  # the card's printed border
        page[top + card.shape[0] - 3 : top + card.shape[0], left : left + card.shape[1]] = 90
        page[top : top + card.shape[0], left : left + 3] = 90
        page[top : top + card.shape[0], left + card.shape[1] - 3 : left + card.shape[1]] = 90
    return page


def test_finds_the_front_on_top_first_when_two_cards_lie_one_above_the_other(card: NDArray[np.uint8]) -> None:
    found = find_two_cards(_on_white(card, [(70, 60), (70, 680)], (1000, 1300)), TWO_CARDS)

    assert found is not None
    (left, top, right, bottom), second = found
    assert left <= 70
    assert top <= 60
    assert right >= 930
    assert bottom >= 600  # the whole top card, with its margin
    assert second[1] > bottom - 20


def test_finds_the_left_card_first_when_two_cards_lie_side_by_side(card: NDArray[np.uint8]) -> None:
    found = find_two_cards(_on_white(card, [(1000, 100), (60, 120)], (1960, 800)), TWO_CARDS)

    assert found is not None
    assert found[0][0] < found[1][0]


def test_does_not_split_a_picture_of_one_card(card: NDArray[np.uint8], card_photo: NDArray[np.uint8]) -> None:
    assert find_two_cards(card_photo, TWO_CARDS) is None
    assert find_two_cards(_on_white(card, [(70, 60)], (1000, 700)), TWO_CARDS) is None


def test_does_not_split_when_the_second_card_is_much_smaller(card: NDArray[np.uint8]) -> None:
    page = _on_white(card, [(70, 60)], (1000, 1300))
    small = cv2.resize(card, (430, 270))
    page[700:970, 70:500] = small
    page[700:703, 70:500] = page[967:970, 70:500] = 90
    page[700:970, 70:73] = page[700:970, 497:500] = 90

    assert find_two_cards(page, TWO_CARDS) is None
