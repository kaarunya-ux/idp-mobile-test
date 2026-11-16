"""The DB detector: probability map to boxes, with a scripted model standing in for the real one."""

from pathlib import Path

import numpy as np
import pytest
from hypothesis import given
from hypothesis import strategies as st
from numpy.typing import NDArray

from idp_ocr_engines.engines.db_detector import DbDetector, DbThresholds, _order_corners, crop_quad, load_db_thresholds
from idp_ocr_engines.errors import ModelFileError
from idp_ocr_engines.limits import RuntimeLimits

THRESHOLDS = DbThresholds(threshold=0.3, box_threshold=0.6, unclip_ratio=1.5)


class ScriptedMapSession:
    """Answers every run with a map that is 0 except for the given blobs, written as fractions of the map."""

    def __init__(self, blobs: list[tuple[float, float, float, float]], value: float = 0.9) -> None:
        self._blobs = blobs
        self._value = value
        self.input_shapes: list[tuple[int, ...]] = []

    def run(
        self, _names: object, feeds: dict[str, NDArray[np.float32]], run_options: object = None
    ) -> list[NDArray[np.float32]]:
        _, _, height, width = feeds["x"].shape
        self.input_shapes.append(feeds["x"].shape)
        scores = np.zeros((1, 1, height, width), dtype=np.float32)
        for left, top, right, bottom in self._blobs:
            scores[0, 0, int(top * height) : int(bottom * height), int(left * width) : int(right * width)] = self._value
        return [scores]


def _detector(session: ScriptedMapSession) -> DbDetector:
    return DbDetector(session, THRESHOLDS, RuntimeLimits())  # type: ignore[arg-type]  # a scripted stand-in for ort.InferenceSession


def _page(width: int = 400, height: int = 200) -> NDArray[np.uint8]:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def test_finds_one_box_around_the_text_when_the_map_has_one_probable_blob() -> None:
    quads = _detector(ScriptedMapSession([(0.1, 0.4, 0.5, 0.5)])).detect(_page())

    assert len(quads) == 1
    center = quads[0].mean(axis=0)
    assert center[0] == pytest.approx(0.3 * 400, abs=3)
    assert center[1] == pytest.approx(0.45 * 200, abs=3)


def test_grows_the_box_beyond_the_blob_when_unclip_is_applied() -> None:
    quad = _detector(ScriptedMapSession([(0.1, 0.4, 0.5, 0.5)])).detect(_page())[0]

    assert quad[:, 0].max() - quad[:, 0].min() > 0.4 * 400


def test_finds_every_box_when_the_map_has_several_blobs() -> None:
    quads = _detector(ScriptedMapSession([(0.1, 0.1, 0.5, 0.2), (0.1, 0.6, 0.6, 0.7)])).detect(_page())

    assert len(quads) == 2


def test_ignores_the_blob_when_its_probability_is_below_the_box_threshold() -> None:
    assert _detector(ScriptedMapSession([(0.1, 0.4, 0.5, 0.5)], value=0.45)).detect(_page()) == []


def test_ignores_the_blob_when_its_probability_is_below_the_detection_threshold() -> None:
    assert _detector(ScriptedMapSession([(0.1, 0.4, 0.5, 0.5)], value=0.2)).detect(_page()) == []


def test_ignores_the_blob_when_it_is_only_a_couple_of_map_pixels_wide() -> None:
    assert _detector(ScriptedMapSession([(0.1, 0.4, 0.1015, 0.5)])).detect(_page()) == []


@pytest.mark.parametrize("size", [(100, 50), (400, 200), (4000, 1000), (1000, 4000), (33, 777)])
def test_feeds_the_model_a_size_that_is_a_multiple_of_32_when_the_page_has_any_shape(size: tuple[int, int]) -> None:
    session = ScriptedMapSession([])

    _detector(session).detect(_page(*size))

    _, channels, height, width = session.input_shapes[0]
    assert (channels, height % 32, width % 32) == (3, 0, 0)


def test_enlarges_a_small_page_when_it_is_far_below_the_target_size() -> None:
    session = ScriptedMapSession([])

    _detector(session).detect(_page(100, 50))

    assert session.input_shapes[0][-1] == 960


def test_shrinks_a_huge_page_when_it_is_above_the_cap() -> None:
    session = ScriptedMapSession([])

    _detector(session).detect(_page(4000, 1000))

    assert max(session.input_shapes[0][-2:]) <= 2016


@given(st.permutations([0, 1, 2, 3]))
def test_orders_the_corners_the_same_way_when_the_points_arrive_in_any_order(order: list[int]) -> None:
    rectangle = np.array([[10, 20], [110, 20], [110, 60], [10, 60]], dtype=np.float32)

    assert np.array_equal(_order_corners(rectangle[order]), rectangle)


def test_crops_the_text_line_straight_when_the_box_is_axis_aligned() -> None:
    page = _page(300, 200)
    page[50:90, 40:240] = 0
    corners = np.array([[40, 50], [240, 50], [240, 90], [40, 90]], dtype=np.float32)

    crop = crop_quad(page, corners)

    assert crop.shape == (40, 200, 3)
    assert crop.mean() < 5


def test_turns_a_tall_box_so_the_text_runs_sideways_when_the_box_is_taller_than_wide() -> None:
    corners = np.array([[40, 10], [80, 10], [80, 190], [40, 190]], dtype=np.float32)

    crop = crop_quad(_page(300, 200), corners)

    assert crop.shape[1] > crop.shape[0]


def test_reads_the_thresholds_when_the_detector_config_is_given(tmp_path: Path) -> None:
    config = tmp_path / "inference.yml"
    config.write_text("PostProcess:\n  thresh: 0.2\n  box_thresh: 0.45\n  unclip_ratio: 1.4\n", encoding="utf-8")

    assert load_db_thresholds(config) == DbThresholds(threshold=0.2, box_threshold=0.45, unclip_ratio=1.4)


@pytest.mark.parametrize(
    "body",
    ["  thresh: 0.2\n", "  thresh: high\n  box_thresh: 0.45\n  unclip_ratio: 1.4\n", "  thresh:\n  - 1\n"],
)
def test_refuses_a_detector_config_with_missing_or_unreadable_thresholds(tmp_path: Path, body: str) -> None:
    config = tmp_path / "inference.yml"
    config.write_text("PostProcess:\n" + body, encoding="utf-8")

    with pytest.raises(ModelFileError):
        load_db_thresholds(config)
