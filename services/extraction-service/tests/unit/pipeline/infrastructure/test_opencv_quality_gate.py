"""Measuring quality on synthetic pages: blur, light, glare (and why white paper is not glare), size and edges."""

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from extraction.pipeline.infrastructure.quality.opencv_quality_gate import OpenCvQualityAssessor, glare_ratio
from idp_ocr_engines.engines.arrays import from_rgb_array

ASSESSOR = OpenCvQualityAssessor(min_card_area_fraction=0.15, saturation_level=250, glare_soft_edge_gradient=15.0)


def _assess(rgb: NDArray[np.uint8]):  # the return type is QualityScores; kept short for readability
    return ASSESSOR.assess(from_rgb_array(rgb))


def test_scores_a_sharp_page_far_above_a_blurred_one(text_page: NDArray[np.uint8]) -> None:
    sharp = _assess(text_page).blur
    blurred = _assess(cv2.GaussianBlur(text_page, (0, 0), 4)).blur

    assert sharp > 100 > blurred


def test_scores_a_dark_page_lower_than_a_normal_one(text_page: NDArray[np.uint8]) -> None:
    normal = _assess(text_page).brightness
    dark = _assess((text_page * 0.2).astype(np.uint8)).brightness

    assert dark < 0.25 < normal


def test_reports_no_glare_when_the_page_is_white_paper_with_text(text_page: NDArray[np.uint8]) -> None:
    assert _assess(text_page).glare == pytest.approx(0.0, abs=0.01)


def test_reports_glare_when_a_soft_white_patch_washes_out_part_of_a_card(card: NDArray[np.uint8]) -> None:
    glare = card.astype(np.float32)
    patch = np.zeros(card.shape[:2], np.float32)
    cv2.ellipse(patch, (600, 250), (160, 110), 0, 0, 360, 1.0, -1)
    patch = cv2.GaussianBlur(patch, (0, 0), 30) * 3.0  # a bright core that fades out softly
    washed = np.clip(glare + patch[:, :, None] * 255, 0, 255).astype(np.uint8)

    assert _assess(washed).glare > 0.04


def test_reports_no_glare_when_the_card_has_a_white_box_with_a_printed_outline(card: NDArray[np.uint8]) -> None:
    labelled = card.copy()
    cv2.rectangle(labelled, (500, 150), (780, 330), (255, 255, 255), -1)
    cv2.rectangle(labelled, (500, 150), (780, 330), (20, 20, 20), 4)

    assert _assess(labelled).glare < 0.04


def test_measures_the_card_and_not_the_desk_when_a_card_lies_on_a_dark_desk(card_photo: NDArray[np.uint8]) -> None:
    scores = _assess(card_photo)

    assert scores.edges_found
    assert scores.brightness > 0.5  # the desk alone would score about 0.2
    assert scores.short_side_px == pytest.approx(540, rel=0.2)


def test_takes_the_whole_picture_as_the_document_when_the_card_fills_the_frame(framed_card: NDArray[np.uint8]) -> None:
    scores = _assess(framed_card)

    assert scores.edges_found
    assert scores.short_side_px == 540


def test_finds_no_document_edges_when_the_picture_is_one_flat_colour() -> None:
    assert not _assess(np.full((600, 800, 3), 128, dtype=np.uint8)).edges_found


def test_counts_nothing_as_glare_when_no_pixel_is_saturated() -> None:
    assert glare_ratio(np.full((100, 100), 120, dtype=np.uint8), saturation_level=250, soft_edge_gradient=15.0) == 0.0
