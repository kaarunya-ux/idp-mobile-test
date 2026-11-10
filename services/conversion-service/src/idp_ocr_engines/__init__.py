"""IDP OCR engines (``idp-ocr-engines`` in IDP-03 ch. 1): adapters for the chosen OCR engines behind one protocol.

Infrastructure only, no business rules. It imports nothing from ``extraction`` so it can move to the shared
``idp-py-platform`` repository unchanged, and conversion-service can use it too.
"""

from idp_ocr_engines.errors import (
    ImageTooLargeError,
    InferenceTimeoutError,
    InvalidGeometryError,
    ModelFileError,
    ModelRegistryError,
    OcrEngineError,
    UnknownModelError,
    UnsupportedLanguageError,
)
from idp_ocr_engines.limits import RuntimeLimits
from idp_ocr_engines.model import Box, OcrPage, OcrRegion, OcrWord, PageImage
from idp_ocr_engines.port import OcrEnginePort
from idp_ocr_engines.registry import ModelFile, ModelRegistry, ModelSpec, VerifiedModel

__all__ = [
    "Box",
    "ImageTooLargeError",
    "InferenceTimeoutError",
    "InvalidGeometryError",
    "ModelFile",
    "ModelFileError",
    "ModelRegistry",
    "ModelRegistryError",
    "ModelSpec",
    "OcrEngineError",
    "OcrEnginePort",
    "OcrPage",
    "OcrRegion",
    "OcrWord",
    "PageImage",
    "RuntimeLimits",
    "UnknownModelError",
    "UnsupportedLanguageError",
    "VerifiedModel",
]
