"""``PageWordsPort`` with OCR engine A: the words on the uploaded pages as they are, for the generic read's boxes."""

from collections.abc import Sequence

from extraction.pipeline.domain.ports import PageLoaderPort
from idp_ocr_engines.model import OcrPage
from idp_ocr_engines.port import OcrEnginePort

_PAGES = 2  # the generic read sees a card's front and back, or a PDF's first two pages
_LANGUAGES = ("en",)


class OcrPageWords:
    def __init__(self, loader: PageLoaderPort, engine: OcrEnginePort) -> None:
        self._loader = loader
        self._engine = engine

    def read_words(self, contents: Sequence[bytes]) -> Sequence[OcrPage]:
        """Pages in upload order, never split into front and back: a box is measured on the picture as uploaded."""
        images = [image for content in contents for image in self._loader.load_pages(content, split_cards=False)]
        return [self._engine.read_page(image, _LANGUAGES) for image in images[:_PAGES]]
