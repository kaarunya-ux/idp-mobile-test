"""Where a value is printed on the uploaded pages (the generic read's boxes)."""

from dataclasses import dataclass

from idp_ocr_engines.model import Box


@dataclass(frozen=True, slots=True, kw_only=True)
class PlacedBox:
    """A value's place: ``page`` counts from 1 (the uploaded pages, in order), ``box`` is fractions of that page."""

    page: int
    box: Box
