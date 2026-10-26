"""The one interface every OCR engine implements (IDP-03 ch. 2.8, standard ch. 7: ports are ``typing.Protocol``)."""

from collections.abc import Sequence
from typing import Protocol

from idp_ocr_engines.model import Box, OcrPage, OcrRegion, PageImage


class OcrEnginePort(Protocol):
    """Reads text from a page image, or from one box of it. Adapters wrap one engine family each.

    ``engine_id`` is the slot the engine fills (``"ocr-a"``); ``engine_version`` names the exact models behind it and
    goes into the result's provenance (ML-04). ``languages`` are lower-case ISO 639-1 codes such as ``["en", "hi"]``;
    an adapter raises ``UnsupportedLanguageError`` for one it cannot read instead of reading something else (ML-10).
    """

    @property
    def engine_id(self) -> str:
        """The slot this engine fills, for example ``"ocr-a"``."""
        ...

    @property
    def engine_version(self) -> str:
        """The models and versions this engine runs, as recorded in the registry."""
        ...

    def warm_up(self) -> None:
        """Run a blank page through every model so the first real request is not slow (ML-03)."""
        ...

    def read_page(self, image: PageImage, languages: Sequence[str]) -> OcrPage:
        """Find and read all text on the page."""
        ...

    def read_region(self, image: PageImage, box: Box, languages: Sequence[str]) -> OcrRegion:
        """Read the text inside ``box`` of the page, for re-reading one field (IDP-03 ch. 2.6, stage 7)."""
        ...
