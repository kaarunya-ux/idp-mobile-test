"""PREPARE on synthetic pages: warping, tilt, turning upright (scripted model), clean-up, determinism."""

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from extraction.pipeline.infrastructure.preprocess.opencv_preprocessor import (
    OpenCvPagePreparer,
    binarise,
    denoise,
    enhance_contrast,
    estimate_skew_degrees,
    rotate_by,
)
from idp_ocr_engines.engines.arrays import from_rgb_array, to_rgb_array


class ScriptedOrientation:
    def __init__(self, degrees: int, confidence: float) -> None:
        self._answer = (degrees, confidence)

    def classify(self, rgb: NDArray[np.uint8]) -> tuple[int, float]:
        return self._answer


def _preparer(degrees: int = 0, confidence: float = 0.95) -> OpenCvPagePreparer:
    return OpenCvPagePreparer(
        orientation=ScriptedOrientation(degrees, confidence),
        min_card_area_fraction=0.15,
        orientation_min_confidence=0.6,
    )


def _tilted(page: NDArray[np.uint8], degrees: float) -> NDArray[np.uint8]:
    height, width = page.shape[:2]
    matrix = cv2.getRotationMatrix2D((width / 2, height / 2), degrees, 1.0)
    return np.asarray(cv2.warpAffine(page, matrix, (width, height), borderValue=(255, 255, 255)), dtype=np.uint8)


@pytest.mark.parametrize("tilt", [-5.0, -2.0, 2.0, 5.0])
def test_measures_the_tilt_within_one_degree_when_a_text_page_is_rotated(
    text_page: NDArray[np.uint8], tilt: float
) -> None:
    assert estimate_skew_degrees(_tilted(text_page, tilt)) == pytest.approx(-tilt, abs=1.0)


def test_measures_no_tilt_when_the_page_has_no_long_lines() -> None:
    assert estimate_skew_degrees(np.full((300, 300, 3), 255, dtype=np.uint8)) == 0.0


def test_removes_the_tilt_when_a_tilted_page_is_prepared(text_page: NDArray[np.uint8]) -> None:
    prepared = _preparer().prepare(from_rgb_array(_tilted(text_page, 4.0)))

    assert prepared.steps.deskew_degrees == pytest.approx(-4.0, abs=1.0)


def test_leaves_the_tilt_alone_when_it_is_too_small_to_matter(text_page: NDArray[np.uint8]) -> None:
    assert _preparer().prepare(from_rgb_array(text_page)).steps.deskew_degrees == 0.0


def test_cuts_the_card_out_when_a_card_lies_on_a_desk(card_photo: NDArray[np.uint8]) -> None:
    prepared = _preparer().prepare(from_rgb_array(card_photo))

    assert prepared.steps.warped
    assert prepared.clean.width < card_photo.shape[1]


def test_uses_the_page_as_it_is_when_no_card_can_be_found() -> None:
    noise = np.random.default_rng(9).integers(0, 255, (300, 400, 3), dtype=np.uint8)

    prepared = _preparer().prepare(from_rgb_array(noise))

    assert not prepared.steps.warped
    assert (prepared.clean.width, prepared.clean.height) == (400, 300)


@pytest.mark.parametrize(("degrees", "turns"), [(90, 1), (180, 2), (270, 3)])
def test_turns_the_page_back_when_the_model_is_sure_it_is_rotated(
    text_page: NDArray[np.uint8], degrees: int, turns: int
) -> None:
    prepared = _preparer(degrees, 0.95).prepare(from_rgb_array(text_page))

    assert prepared.steps.rotation_degrees == degrees
    expected_shape = np.rot90(text_page, turns).shape[:2]
    assert (prepared.clean.height, prepared.clean.width) == expected_shape


def test_leaves_the_page_alone_when_the_model_is_not_sure(text_page: NDArray[np.uint8]) -> None:
    prepared = _preparer(180, 0.3).prepare(from_rgb_array(text_page))

    assert prepared.steps.rotation_degrees == 0
    assert prepared.steps.orientation_confidence == 0.3


def test_gives_the_same_pixels_every_time_when_the_same_page_is_prepared_twice(text_page: NDArray[np.uint8]) -> None:
    first = _preparer().prepare(from_rgb_array(_tilted(text_page, 3.0)))
    second = _preparer().prepare(from_rgb_array(_tilted(text_page, 3.0)))

    assert first.clean.rgb == second.clean.rgb
    assert first.binarised.rgb == second.binarised.rgb


def test_makes_a_black_and_white_copy_with_three_identical_channels(text_page: NDArray[np.uint8]) -> None:
    black_white = binarise(text_page)

    assert set(np.unique(black_white).tolist()) <= {0, 255}
    assert np.array_equal(black_white[:, :, 0], black_white[:, :, 1])
    assert black_white.shape == text_page.shape


def test_keeps_the_picture_size_when_it_is_cleaned(text_page: NDArray[np.uint8]) -> None:
    assert denoise(text_page).shape == text_page.shape
    assert enhance_contrast(text_page).shape == text_page.shape


def test_keeps_white_paper_white_when_contrast_is_evened_out() -> None:
    assert enhance_contrast(np.full((64, 64, 3), 255, dtype=np.uint8)).min() >= 250


def test_grows_the_canvas_so_no_corner_is_lost_when_a_page_is_rotated_45_degrees(text_page: NDArray[np.uint8]) -> None:
    rotated = rotate_by(text_page, 45.0)

    assert rotated.shape[0] > text_page.shape[0]
    assert rotated.shape[1] > text_page.shape[1]


def test_keeps_the_page_index_when_a_later_page_is_prepared(text_page: NDArray[np.uint8]) -> None:
    prepared = _preparer().prepare(from_rgb_array(text_page, page_index=4))

    assert (prepared.page_index, prepared.clean.page_index, prepared.binarised.page_index) == (4, 4, 4)


def test_reads_the_prepared_pixels_back_as_an_array_when_inspected(text_page: NDArray[np.uint8]) -> None:
    prepared = _preparer().prepare(from_rgb_array(text_page))

    assert to_rgb_array(prepared.clean).dtype == np.uint8
