"""The PP-OCR adapter: PaddleOCR's DB detector with one or more CTC recognisers (PP-OCRv5 mobile, PP-OCRv6 small).

English is read by the general recogniser. A recogniser for another language (the Devanagari one for Hindi) can be
added; it reads every line too, but only when that language was asked for, and the more confident reading wins.
"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol

import numpy as np
from numpy.typing import NDArray

from idp_ocr_engines.engines.arrays import to_rgb_array
from idp_ocr_engines.engines.db_detector import DbDetector, crop_quad, load_db_thresholds
from idp_ocr_engines.engines.paddle_recognizer import PaddleRecognizer, load_charset
from idp_ocr_engines.errors import InvalidGeometryError
from idp_ocr_engines.languages import require_languages
from idp_ocr_engines.layout import RecognizedPiece, assign_line_and_block_ids
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.model import Box, OcrPage, OcrRegion, PageImage
from idp_ocr_engines.registry import ModelRegistry, VerifiedModel
from idp_ocr_engines.session import create_session

_GENERAL_LANGUAGE = "en"
_REGION_PADDING = 0.15  # fraction of a field's height added around it before reading


class TextDetector(Protocol):
    """Finds text lines on a page: four corner points each, in page pixels."""

    def detect(self, rgb: NDArray[np.uint8]) -> list[NDArray[np.float32]]: ...

    def warm_up(self) -> None: ...


class LineRecognizer(Protocol):
    """Reads one cropped text line."""

    def recognize(self, crop_rgb: NDArray[np.uint8]) -> tuple[str, float]: ...

    def warm_up(self) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class PpOcrModels:
    """Registry names of the models one PP-OCR engine is built from, and which language each extra recogniser reads."""

    detector: str
    recognizer: str
    extra_recognizers: Mapping[str, str] = field(default_factory=dict)


class PpOcrEngine:
    """Implements ``OcrEnginePort`` with the PaddleOCR family of models."""

    def __init__(
        self,
        *,
        engine_id: str,
        engine_version: str,
        detector: TextDetector,
        general: LineRecognizer,
        extra: Mapping[str, LineRecognizer],
        limits: RuntimeLimits,
    ) -> None:
        self._engine_id = engine_id
        self._version = engine_version
        self._detector = detector
        self._general = general
        self._extra = dict(extra)
        self._limits = limits
        self._supported = frozenset({_GENERAL_LANGUAGE, *self._extra})

    @classmethod
    def from_registry(
        cls, *, engine_id: str, registry: ModelRegistry, models_dir: Path, models: PpOcrModels, limits: RuntimeLimits
    ) -> "PpOcrEngine":
        """Verify the models' checksums, load them on the CPU and assemble the engine (ML-01, ML-02)."""
        detector_model = registry.verify(models.detector, models_dir)
        general_model = registry.verify(models.recognizer, models_dir)
        extra_models = {code: registry.verify(name, models_dir) for code, name in models.extra_recognizers.items()}
        detector = DbDetector(
            create_session(detector_model.entry_path, threads=limits.threads),
            load_db_thresholds(detector_model.path_of("inference.yml")),
            limits,
        )
        used = [detector_model, general_model, *extra_models.values()]
        return cls(
            engine_id=engine_id,
            engine_version="+".join(f"{model.spec.name}@{model.spec.version}" for model in used),
            detector=detector,
            general=_load_recognizer(general_model, limits),
            extra={code: _load_recognizer(model, limits) for code, model in extra_models.items()},
            limits=limits,
        )

    @property
    def engine_id(self) -> str:
        return self._engine_id

    @property
    def engine_version(self) -> str:
        return self._version

    def warm_up(self) -> None:
        """Run a blank page and a blank crop through every model (ML-03)."""
        self._detector.warm_up()
        self._general.warm_up()
        for recognizer in self._extra.values():
            recognizer.warm_up()

    def read_page(self, image: PageImage, languages: Sequence[str]) -> OcrPage:
        """Detect every text line on the page and read each one."""
        wanted = require_languages(languages, self._supported, engine_name=self._engine_id)
        self._limits.check_image(image)
        pieces = self._read_pieces(to_rgb_array(image), wanted)
        return OcrPage(width=image.width, height=image.height, words=assign_line_and_block_ids(pieces))

    def read_region(self, image: PageImage, box: Box, languages: Sequence[str]) -> OcrRegion:
        """Read the text inside one box: lines found in it are joined top to bottom; if none is found, the whole box
        is read as a single line."""
        wanted = require_languages(languages, self._supported, engine_name=self._engine_id)
        self._limits.check_image(image)
        left, top, right, bottom = box.to_pixels(page_width=image.width, page_height=image.height)
        pad = round((bottom - top) * _REGION_PADDING)
        crop = to_rgb_array(image)[
            max(0, top - pad) : min(image.height, bottom + pad), max(0, left - pad) : min(image.width, right + pad)
        ]
        pieces = self._read_pieces(crop, wanted)
        if not pieces:
            text, confidence = self._read_line(crop, wanted)
            return OcrRegion(box=box, text=text, confidence=confidence)
        words = assign_line_and_block_ids(pieces)
        return OcrRegion(
            box=box,
            text=" ".join(word.text for word in words),
            confidence=sum(word.confidence for word in words) / len(words),
        )

    def _read_pieces(self, rgb: NDArray[np.uint8], wanted: frozenset[str]) -> list[RecognizedPiece]:
        height, width = rgb.shape[:2]
        pieces: list[RecognizedPiece] = []
        for corners in self._detector.detect(rgb):
            text, confidence = self._read_line(crop_quad(rgb, corners), wanted)
            box = _bounding_box(corners, width, height)
            if text and box is not None:
                pieces.append(RecognizedPiece(text=text, box=box, confidence=confidence))
        return pieces

    def _read_line(self, crop: NDArray[np.uint8], wanted: frozenset[str]) -> tuple[str, float]:
        """Read a crop with the general recogniser and each requested extra one; the most confident reading wins."""
        best = self._general.recognize(crop)
        for code, recognizer in self._extra.items():
            if code in wanted:
                candidate = recognizer.recognize(crop)
                if candidate[0] and candidate[1] > best[1]:
                    best = candidate
        return best


def _load_recognizer(model: VerifiedModel, limits: RuntimeLimits) -> PaddleRecognizer:
    return PaddleRecognizer(
        create_session(model.entry_path, threads=limits.threads), load_charset(model.path_of("inference.yml")), limits
    )


def _bounding_box(corners: NDArray[np.float32], width: int, height: int) -> Box | None:
    """The page-fraction box around four corners, or ``None`` if nothing is left of it inside the page."""
    try:
        return Box.from_pixels(
            float(corners[:, 0].min()),
            float(corners[:, 1].min()),
            float(corners[:, 0].max()),
            float(corners[:, 1].max()),
            page_width=width,
            page_height=height,
        )
    except InvalidGeometryError:
        return None
