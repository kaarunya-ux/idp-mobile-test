"""The CTC recogniser: scaling, decoding and the check that its character set fits the model."""

from pathlib import Path

import numpy as np
import pytest
from numpy.typing import NDArray

from idp_ocr_engines.engines.paddle_recognizer import PaddleRecognizer, load_charset
from idp_ocr_engines.errors import OcrEngineError
from idp_ocr_engines.limits import RuntimeLimits

CHARSET = ("", "A", "B", "C", " ")


class ScriptedScoreSession:
    """Answers every run with one-hot scores for the given class sequence, and remembers the input shapes."""

    def __init__(self, classes: list[int], class_count: int = len(CHARSET)) -> None:
        self._classes = classes
        self._class_count = class_count
        self.input_shapes: list[tuple[int, ...]] = []

    def run(
        self, _names: object, feeds: dict[str, NDArray[np.float32]], run_options: object = None
    ) -> list[NDArray[np.float32]]:
        self.input_shapes.append(feeds["x"].shape)
        scores = np.zeros((1, len(self._classes), self._class_count), dtype=np.float32)
        scores[0, np.arange(len(self._classes)), self._classes] = 20.0
        return [scores]


def _recognizer(session: ScriptedScoreSession) -> PaddleRecognizer:
    return PaddleRecognizer(session, CHARSET, RuntimeLimits())  # type: ignore[arg-type]  # scripted stand-in for ort.InferenceSession


def _crop(width: int, height: int) -> NDArray[np.uint8]:
    return np.full((height, width, 3), 255, dtype=np.uint8)


def test_decodes_the_text_when_the_model_scores_a_sequence_of_classes() -> None:
    text, confidence = _recognizer(ScriptedScoreSession([1, 0, 2, 0, 3])).recognize(_crop(100, 20))

    assert text == "ABC"
    assert confidence > 0.99


@pytest.mark.parametrize(
    ("crop_size", "expected_width"),
    [((20, 10), 96), ((1, 100), 16), ((100000, 10), 3200), ((480, 48), 480)],
)
def test_scales_the_crop_to_48_pixels_high_with_a_bounded_width(
    crop_size: tuple[int, int], expected_width: int
) -> None:
    session = ScriptedScoreSession([1])

    _recognizer(session).recognize(_crop(*crop_size))

    assert session.input_shapes[0] == (1, 3, 48, expected_width)


def test_accepts_the_model_when_the_charset_matches_its_output_size() -> None:
    _recognizer(ScriptedScoreSession([0, 0])).warm_up()


def test_refuses_the_model_when_the_charset_does_not_match_its_output_size() -> None:
    with pytest.raises(OcrEngineError, match="character set"):
        _recognizer(ScriptedScoreSession([0, 0], class_count=99)).warm_up()


def test_puts_a_blank_first_and_a_space_last_when_the_charset_is_loaded(tmp_path: Path) -> None:
    config = tmp_path / "inference.yml"
    config.write_text("PostProcess:\n  character_dict:\n  - a\n  - b\n  - 'ि'\n", encoding="utf-8")

    assert load_charset(config) == ("", "a", "b", "ि", " ")
