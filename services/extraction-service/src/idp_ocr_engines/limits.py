"""How much an engine may be asked to do: threads, time and input size (ML-06, ML-08)."""

from dataclasses import dataclass

from idp_ocr_engines.errors import ImageTooLargeError
from idp_ocr_engines.model import PageImage


@dataclass(frozen=True, slots=True, kw_only=True)
class RuntimeLimits:
    """Caps applied by every engine. The service builds this from its settings; the library never reads settings."""

    threads: int = 4
    inference_timeout_seconds: float = 60.0
    max_pixels: int = 60_000_000

    def check_image(self, image: PageImage) -> None:
        """Raise ``ImageTooLargeError`` if the page has more pixels than the cap (ML-06)."""
        if image.pixel_count > self.max_pixels:
            message = "The page has more pixels than the configured cap."
            raise ImageTooLargeError(message)
