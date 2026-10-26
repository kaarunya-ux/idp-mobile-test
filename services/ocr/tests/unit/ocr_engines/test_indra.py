"""The Indra adapter's own logic with a scripted reader, and the real model set when it is available."""

import re
import sys
from collections.abc import Sequence
from pathlib import Path
from types import SimpleNamespace

import cv2
import numpy as np
import pytest
from numpy.typing import NDArray

from extraction.core.config import get_settings
from idp_ocr_engines.engines.arrays import from_rgb_array
from idp_ocr_engines.engines.indra import EngineNotInstalledError, IndraEngine
from idp_ocr_engines.errors import ImageTooLargeError, UnsupportedLanguageError
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.model import Box, PageImage
from idp_ocr_engines.registry import ModelRegistry


def _line(text: str, box: Sequence[float], conf: float = 0.9) -> SimpleNamespace:
    return SimpleNamespace(box=list(box), text=text, conf=conf)


class ScriptedReader:
    """Returns the scripted lines on every call and remembers how it was called."""

    def __init__(self, lines: list[SimpleNamespace]) -> None:
        self._lines = lines
        self.calls: list[tuple[tuple[int, ...], np.dtype[np.uint8], bool]] = []

    def __call__(self, gray: NDArray[np.uint8], turn: bool = True) -> SimpleNamespace:
        self.calls.append((gray.shape, gray.dtype, turn))
        return SimpleNamespace(lines=self._lines)


def _page(width: int = 400, height: int = 200) -> PageImage:
    return from_rgb_array(np.full((height, width, 3), 255, dtype=np.uint8))


def _engine(reader: ScriptedReader, limits: RuntimeLimits | None = None) -> IndraEngine:
    return IndraEngine(engine_id="ocr-b", engine_version="indra@v1", reader=reader, limits=limits or RuntimeLimits())  # type: ignore[arg-type]


def test_turns_lines_into_words_with_fractions_and_ids_when_a_page_is_read() -> None:
    reader = ScriptedReader([_line("Name", [40, 20, 240, 50]), _line("ROSHAN SAHANI", [40, 120, 200, 150], 0.8)])

    page = _engine(reader).read_page(_page(), ["en", "hi"])

    assert [(word.text, word.line_id, word.confidence) for word in page.words] == [
        ("Name", 0, 0.9),
        ("ROSHAN SAHANI", 1, 0.8),
    ]
    assert page.words[0].box == Box(0.1, 0.1, 0.5, 0.15)


def test_gives_the_reader_a_grayscale_page_and_keeps_its_own_frame_when_reading() -> None:
    reader = ScriptedReader([])

    _engine(reader).read_page(_page(400, 200), ["en"])

    assert reader.calls == [((200, 400), np.dtype(np.uint8), False)]


def test_skips_lines_with_no_text_or_an_impossible_box_when_reading() -> None:
    reader = ScriptedReader(
        [_line("   ", [0, 0, 50, 20]), _line("x", [500, 500, 600, 600]), _line("kept", [10, 10, 90, 30])]
    )

    assert [word.text for word in _engine(reader).read_page(_page(), ["en"]).words] == ["kept"]


def test_keeps_confidence_between_zero_and_one_when_the_package_overshoots() -> None:
    reader = ScriptedReader([_line("a", [10, 10, 90, 30], 1.0000001), _line("b", [10, 60, 90, 80], -0.2)])

    assert [word.confidence for word in _engine(reader).read_page(_page(), ["en"]).words] == [1.0, 0.0]


def test_refuses_a_language_that_has_not_been_tested() -> None:
    with pytest.raises(UnsupportedLanguageError):
        _engine(ScriptedReader([])).read_page(_page(), ["en", "ta"])


def test_refuses_the_page_when_it_has_more_pixels_than_the_cap() -> None:
    with pytest.raises(ImageTooLargeError):
        _engine(ScriptedReader([]), RuntimeLimits(max_pixels=1000)).read_page(_page(), ["en"])


def test_joins_the_lines_found_in_the_box_when_a_region_is_read() -> None:
    reader = ScriptedReader([_line("12 Gandhi Nagar", [5, 2, 60, 12], 0.9), _line("Lucknow", [5, 20, 60, 30], 0.7)])

    region = _engine(reader).read_region(_page(), Box(0.1, 0.1, 0.5, 0.5), ["en"])

    assert (region.text, region.confidence) == ("12 Gandhi Nagar Lucknow", pytest.approx(0.8))


def test_returns_an_empty_reading_when_nothing_is_found_in_the_box() -> None:
    region = _engine(ScriptedReader([])).read_region(_page(), Box(0.1, 0.1, 0.5, 0.1), ["en"])

    assert (region.text, region.confidence) == ("", 0.0)


def test_reads_a_page_once_when_warming_up() -> None:
    reader = ScriptedReader([])

    _engine(reader).warm_up()

    assert len(reader.calls) == 1


def test_refuses_to_build_when_the_package_is_not_installed(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "indra_ocr.pipeline", None)  # makes `import` raise ImportError

    with pytest.raises(EngineNotInstalledError, match="indra"):
        IndraEngine.from_registry(
            engine_id="ocr-b",
            registry=ModelRegistry({}),
            models_dir=Path("."),
            model_name="indra-ocr",
            limits=RuntimeLimits(),
        )


SETTINGS = get_settings().ocr
SERVICE_ROOT = Path(__file__).parents[3]
MODEL_PRESENT = (SETTINGS.models_dir / "indra-ocr-v1.1.0" / "det.onnx").is_file()
MARATHI_CARD = SERVICE_ROOT / "aadhaar_test_images" / "9a0167ef10d1a58334994e590fe66f59.webp"
DEVANAGARI = re.compile(r"[ऀ-ॿ]")
needs_model = pytest.mark.skipif(not MODEL_PRESENT, reason="the Indra model set is not in OCR__MODELS_DIR")


@pytest.fixture(scope="module")
def real_engine() -> IndraEngine:
    return IndraEngine.from_registry(
        engine_id="ocr-b",
        registry=ModelRegistry.from_file(SERVICE_ROOT / SETTINGS.models_file),
        models_dir=SETTINGS.models_dir,
        model_name="indra-ocr",
        limits=RuntimeLimits(threads=SETTINGS.threads),
    )


@needs_model
@pytest.mark.integration
def test_names_the_model_set_and_the_package_when_the_version_is_asked(real_engine: IndraEngine) -> None:
    assert re.fullmatch(r"indra-ocr@v1\.1\.0\+indra-ocr-\d+\.\d+\.\d+", real_engine.engine_version)


@needs_model
@pytest.mark.integration
def test_reads_a_printed_page_and_never_goes_to_the_network_when_the_models_are_local(
    real_engine: IndraEngine, monkeypatch: pytest.MonkeyPatch
) -> None:
    def refuse_download(*_: object, **__: object) -> None:
        pytest.fail("the engine tried to download something (ML-02)")

    monkeypatch.setattr("huggingface_hub.snapshot_download", refuse_download)
    canvas = np.full((300, 900, 3), 255, dtype=np.uint8)
    cv2.putText(canvas, "ABCPK1234E", (40, 90), cv2.FONT_HERSHEY_SIMPLEX, 2.2, (20, 20, 20), 4, cv2.LINE_AA)
    cv2.putText(canvas, "ARAVIND KUMAR", (40, 220), cv2.FONT_HERSHEY_SIMPLEX, 1.8, (20, 20, 20), 3, cv2.LINE_AA)
    real_engine.warm_up()

    page = real_engine.read_page(from_rgb_array(canvas), ["en"])

    assert page.text.replace(" ", "") == "ABCPK1234E\nARAVINDKUMAR"


@needs_model
@pytest.mark.integration
def test_reads_devanagari_on_a_card_that_prints_it(real_engine: IndraEngine) -> None:
    bgr = cv2.imread(str(MARATHI_CARD))
    page = from_rgb_array(cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB))

    words = real_engine.read_page(page, ["en", "hi"]).words

    assert sum(1 for word in words if DEVANAGARI.search(word.text)) >= 3
