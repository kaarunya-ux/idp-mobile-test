"""``TextRecognizerPort`` with Tesseract: a second reading of a scanned page, for the model-reading check.

pytesseract runs the ``tesseract`` program, which the worker image installs; the API and health images do not.
"""

import shutil
from pathlib import Path

import pytesseract
import structlog
from PIL import Image

log = structlog.get_logger(__name__)

_PAGE_SEGMENTATION = "--psm 6"  # one uniform block of text: tables and ruled forms read better than with auto layout


def tesseract_available() -> bool:
    return shutil.which("tesseract") is not None


class TesseractTextRecognizer:
    def __init__(self, languages: str, timeout_seconds: float) -> None:
        self._languages = languages
        self._timeout = timeout_seconds

    def read_text(self, image: Path) -> str:
        try:
            with Image.open(image) as picture:
                text: str = pytesseract.image_to_string(
                    picture, lang=self._languages, config=_PAGE_SEGMENTATION, timeout=self._timeout
                )
        except (pytesseract.TesseractError, RuntimeError, OSError) as exc:  # a timeout is a RuntimeError
            log.warning("ocr_check_unavailable", error=type(exc).__name__)  # no second reading: the page is not checked
            return ""
        return text
