"""The Indra OCR adapter (``datalab-to/indra-ocr``): detection, script routing and recognition in one model set.

The package is an optional extra (``indra``); its model weights carry a modified OpenRAIL-M licence, so using this
engine in production needs the clearance recorded in ADR 0002. The adapter loads the model folder it is given and
never downloads anything (ML-02).

Known limits, by design of the package:

* Indra picks the recogniser for each line itself, so it reads every script it knows on the page. ``languages`` is
  checked against what has been tested (English, Hindi) but cannot switch a script off.
* The package runs its own ONNX sessions, so the per-run timeout of ``session.run_session`` cannot be applied; the
  worker's task time limit (CEL-06) is the bound (ML-06).
"""

from collections.abc import Sequence
from importlib import metadata
from pathlib import Path
from typing import Protocol

import cv2
import numpy as np
from numpy.typing import NDArray

from idp_ocr_engines.engines.arrays import to_rgb_array
from idp_ocr_engines.errors import InvalidGeometryError, OcrEngineError
from idp_ocr_engines.languages import require_languages
from idp_ocr_engines.layout import RecognizedPiece, assign_line_and_block_ids
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.model import Box, OcrPage, OcrRegion, PageImage
from idp_ocr_engines.registry import ModelRegistry

_SUPPORTED_LANGUAGES = frozenset({"en", "hi"})
_REGION_PADDING = 0.15
_WARM_UP_SIZE = (120, 480)


class IndraLine(Protocol):
    """A line as the package returns it: pixel corners ``x0, y0, x1, y1``, the text and a 0-1 confidence."""

    box: Sequence[float]
    text: str
    conf: float


class IndraPage(Protocol):
    """What the package returns for one page: the lines in reading order."""

    lines: Sequence[IndraLine]


class IndraReader(Protocol):
    """The package's OCR object: reads a grayscale page. ``turn=False`` keeps coordinates in the caller's frame."""

    def __call__(self, gray: NDArray[np.uint8], turn: bool = ...) -> IndraPage: ...


class EngineNotInstalledError(OcrEngineError):
    """An optional engine's package is not installed in this image."""


class IndraEngine:
    """Implements ``OcrEnginePort`` with Indra OCR."""

    def __init__(self, *, engine_id: str, engine_version: str, reader: IndraReader, limits: RuntimeLimits) -> None:
        self._engine_id = engine_id
        self._version = engine_version
        self._reader = reader
        self._limits = limits

    @classmethod
    def from_registry(
        cls, *, engine_id: str, registry: ModelRegistry, models_dir: Path, model_name: str, limits: RuntimeLimits
    ) -> "IndraEngine":
        """Verify the model set's checksums, then load it from its local folder (ML-01, ML-02)."""
        try:
            from indra_ocr.pipeline import OrtOCR  # noqa: PLC0415  (optional extra: imported only when this engine is built)
        except ImportError as error:
            message = "indra-ocr is not installed; build the image with the 'indra' extra to use this engine."
            raise EngineNotInstalledError(message) from error
        model = registry.verify(model_name, models_dir)
        reader = OrtOCR(model.entry_path.parent, threads=limits.threads)
        package_version = metadata.version("indra-ocr")
        return cls(
            engine_id=engine_id,
            engine_version=f"{model.spec.name}@{model.spec.version}+indra-ocr-{package_version}",
            reader=reader,
            limits=limits,
        )

    @property
    def engine_id(self) -> str:
        return self._engine_id

    @property
    def engine_version(self) -> str:
        return self._version

    def warm_up(self) -> None:
        """Read a small page with some text on it, so every model runs once (ML-03)."""
        canvas = np.full(_WARM_UP_SIZE, 255, dtype=np.uint8)
        cv2.putText(canvas, "Warm up 123", (10, 80), cv2.FONT_HERSHEY_SIMPLEX, 2.0, 0, 3, cv2.LINE_AA)
        self._reader(canvas, turn=False)

    def read_page(self, image: PageImage, languages: Sequence[str]) -> OcrPage:
        """Read every line on the page."""
        require_languages(languages, _SUPPORTED_LANGUAGES, engine_name=self._engine_id)
        self._limits.check_image(image)
        gray = _to_gray(to_rgb_array(image))
        pieces = _pieces_from(self._reader(gray, turn=False), image.width, image.height)
        return OcrPage(width=image.width, height=image.height, words=assign_line_and_block_ids(pieces))

    def read_region(self, image: PageImage, box: Box, languages: Sequence[str]) -> OcrRegion:
        """Read the text inside one box. Lines found in it are joined top to bottom."""
        require_languages(languages, _SUPPORTED_LANGUAGES, engine_name=self._engine_id)
        self._limits.check_image(image)
        left, top, right, bottom = box.to_pixels(page_width=image.width, page_height=image.height)
        pad = round((bottom - top) * _REGION_PADDING)
        crop = to_rgb_array(image)[
            max(0, top - pad) : min(image.height, bottom + pad), max(0, left - pad) : min(image.width, right + pad)
        ]
        pieces = _pieces_from(self._reader(_to_gray(crop), turn=False), crop.shape[1], crop.shape[0])
        if not pieces:
            return OcrRegion(box=box, text="", confidence=0.0)
        words = assign_line_and_block_ids(pieces)
        return OcrRegion(
            box=box,
            text=" ".join(word.text for word in words),
            confidence=sum(word.confidence for word in words) / len(words),
        )


def _to_gray(rgb: NDArray[np.uint8]) -> NDArray[np.uint8]:
    return np.ascontiguousarray(cv2.cvtColor(rgb, cv2.COLOR_RGB2GRAY))


def _pieces_from(page: IndraPage, width: int, height: int) -> list[RecognizedPiece]:
    pieces: list[RecognizedPiece] = []
    for line in page.lines:
        text = line.text.strip()
        if not text:
            continue
        try:
            box = Box.from_pixels(
                line.box[0], line.box[1], line.box[2], line.box[3], page_width=width, page_height=height
            )
        except InvalidGeometryError:
            continue
        pieces.append(RecognizedPiece(text=text, box=box, confidence=min(1.0, max(0.0, float(line.conf)))))
    return pieces
