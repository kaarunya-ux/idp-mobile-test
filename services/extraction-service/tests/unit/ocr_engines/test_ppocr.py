"""The PP-OCR adapter's own logic (languages, extra readers, regions, limits), with a scripted detector and readers."""

from collections.abc import Sequence

import numpy as np
import pytest
from numpy.typing import NDArray

from idp_ocr_engines.engines.arrays import from_rgb_array
from idp_ocr_engines.engines.ppocr import PpOcrEngine
from idp_ocr_engines.errors import ImageTooLargeError, UnsupportedLanguageError
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.model import Box, PageImage


class ScriptedDetector:
    def __init__(self, quads: list[list[tuple[int, int]]]) -> None:
        self._quads = [np.array(quad, dtype=np.float32) for quad in quads]
        self.warmed_up = False

    def detect(self, rgb: NDArray[np.uint8]) -> list[NDArray[np.float32]]:
        return self._quads

    def warm_up(self) -> None:
        self.warmed_up = True


class ScriptedReader:
    """Returns the scripted ``(text, confidence)`` pairs in order, then repeats the last one."""

    def __init__(self, readings: Sequence[tuple[str, float]]) -> None:
        self._readings = list(readings)
        self.calls = 0
        self.warmed_up = False

    def recognize(self, crop_rgb: NDArray[np.uint8]) -> tuple[str, float]:
        reading = self._readings[min(self.calls, len(self._readings) - 1)]
        self.calls += 1
        return reading

    def warm_up(self) -> None:
        self.warmed_up = True


def _rectangle(left: int, top: int, right: int, bottom: int) -> list[tuple[int, int]]:
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


def _page(width: int = 400, height: int = 200) -> PageImage:
    return from_rgb_array(np.full((height, width, 3), 255, dtype=np.uint8))


def _engine(
    detector: ScriptedDetector,
    general: ScriptedReader,
    extra: dict[str, ScriptedReader] | None = None,
    limits: RuntimeLimits | None = None,
) -> PpOcrEngine:
    return PpOcrEngine(
        engine_id="ocr-a",
        engine_version="demo@1",
        detector=detector,
        general=general,
        extra=extra or {},
        limits=limits or RuntimeLimits(),
    )


def test_reads_every_line_into_words_with_fractions_and_ids_when_a_page_is_read() -> None:
    detector = ScriptedDetector([_rectangle(40, 20, 240, 50), _rectangle(40, 120, 200, 150)])
    engine = _engine(detector, ScriptedReader([("Name", 0.99), ("ROSHAN", 0.95)]))

    page = engine.read_page(_page(), ["en"])

    assert [(word.text, word.line_id, word.block_id) for word in page.words] == [("Name", 0, 0), ("ROSHAN", 1, 1)]
    assert page.words[0].box == Box(0.1, 0.1, 0.5, 0.15)
    assert (page.width, page.height) == (400, 200)


def test_drops_a_line_when_nothing_could_be_read_in_it() -> None:
    engine = _engine(ScriptedDetector([_rectangle(40, 20, 240, 50)]), ScriptedReader([("", 0.0)]))

    assert engine.read_page(_page(), ["en"]).words == ()


def test_reports_its_slot_and_models_when_asked() -> None:
    engine = _engine(ScriptedDetector([]), ScriptedReader([("x", 1.0)]))

    assert (engine.engine_id, engine.engine_version) == ("ocr-a", "demo@1")


def test_refuses_hindi_when_no_hindi_recogniser_is_loaded() -> None:
    engine = _engine(ScriptedDetector([]), ScriptedReader([("x", 1.0)]))

    with pytest.raises(UnsupportedLanguageError):
        engine.read_page(_page(), ["en", "hi"])


def test_prefers_the_hindi_reading_when_hindi_is_asked_and_it_is_more_confident() -> None:
    general, hindi = ScriptedReader([("HIRAT HRCHR", 0.56)]), ScriptedReader([("भारत सरकार", 0.96)])
    engine = _engine(ScriptedDetector([_rectangle(40, 20, 240, 50)]), general, {"hi": hindi})

    words = engine.read_page(_page(), ["en", "hi"]).words

    assert [word.text for word in words] == ["भारत सरकार"]


def test_keeps_the_english_reading_when_the_hindi_reader_is_less_confident() -> None:
    general, hindi = ScriptedReader([("Bajaj", 0.99)]), ScriptedReader([("Baja", 0.80)])
    engine = _engine(ScriptedDetector([_rectangle(40, 20, 240, 50)]), general, {"hi": hindi})

    assert engine.read_page(_page(), ["en", "hi"]).words[0].text == "Bajaj"


def test_skips_the_hindi_reader_when_only_english_is_asked() -> None:
    general, hindi = ScriptedReader([("Bajaj", 0.5)]), ScriptedReader([("बजाज", 0.99)])
    engine = _engine(ScriptedDetector([_rectangle(40, 20, 240, 50)]), general, {"hi": hindi})

    engine.read_page(_page(), ["en"])

    assert hindi.calls == 0


def test_refuses_the_page_when_it_has_more_pixels_than_the_cap() -> None:
    engine = _engine(ScriptedDetector([]), ScriptedReader([("x", 1.0)]), limits=RuntimeLimits(max_pixels=1000))

    with pytest.raises(ImageTooLargeError):
        engine.read_page(_page(), ["en"])


def test_joins_the_lines_found_in_the_box_when_a_region_is_read() -> None:
    engine = _engine(
        ScriptedDetector([_rectangle(5, 2, 60, 12), _rectangle(5, 20, 60, 30)]),
        ScriptedReader([("12 Gandhi Nagar", 0.9), ("Lucknow", 0.8)]),
    )

    region = engine.read_region(_page(), Box(0.1, 0.1, 0.5, 0.5), ["en"])

    assert region.text == "12 Gandhi Nagar Lucknow"
    assert region.confidence == pytest.approx(0.85)


def test_reads_the_whole_box_as_one_line_when_no_line_is_found_in_it() -> None:
    general = ScriptedReader([("ABCPK1234E", 0.97)])
    engine = _engine(ScriptedDetector([]), general)

    region = engine.read_region(_page(), Box(0.1, 0.1, 0.5, 0.1), ["en"])

    assert (region.text, region.confidence, general.calls) == ("ABCPK1234E", 0.97, 1)


def test_warms_up_every_model_when_asked() -> None:
    detector, general, hindi = ScriptedDetector([]), ScriptedReader([("x", 1.0)]), ScriptedReader([("x", 1.0)])

    _engine(detector, general, {"hi": hindi}).warm_up()

    assert (detector.warmed_up, general.warmed_up, hindi.warmed_up) == (True, True, True)
