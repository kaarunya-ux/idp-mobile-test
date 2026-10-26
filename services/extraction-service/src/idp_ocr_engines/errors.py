"""Errors the OCR engines raise.

This library imports nothing from ``extraction``, so these are plain exceptions. The extraction service maps them to
its own error codes at the adapter boundary (HTTP-03: a library's exceptions never travel further than its adapter).
"""


class OcrEngineError(Exception):
    """Base class for everything this library raises on purpose."""


class InvalidGeometryError(OcrEngineError, ValueError):
    """A box or image has impossible dimensions (DOM-02: invalid values cannot be constructed)."""


class ModelRegistryError(OcrEngineError):
    """``models.yaml`` is missing, unreadable or not valid (ML-01)."""


class UnknownModelError(OcrEngineError):
    """A model name is not in the registry. Code refers to registry names, never to paths (ML-01)."""


class ModelFileError(OcrEngineError):
    """A model file is missing or does not match its recorded SHA-256 (ML-02)."""


class UnsupportedLanguageError(OcrEngineError):
    """The engine cannot read a requested language. It never silently reads something else (ML-10)."""


class ImageTooLargeError(OcrEngineError):
    """The image has more pixels than the configured cap (ML-06)."""


class InferenceTimeoutError(OcrEngineError):
    """A model run took longer than the configured timeout and was stopped (ML-06)."""
