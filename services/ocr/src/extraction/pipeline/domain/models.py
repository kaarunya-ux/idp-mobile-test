"""What the first pipeline stages produce."""

from dataclasses import dataclass

from idp_ocr_engines.model import PageImage


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparationSteps:
    """What PREPARE did to a page, for the stage record: no document content, only numbers and flags."""

    warped: bool  # the document was cut out of the photo and straightened
    deskew_degrees: float  # small tilt removed
    rotation_degrees: int  # quarter turns applied (0, 90, 180 or 270, clockwise page rotation that was undone)
    orientation_confidence: float | None  # the orientation model's confidence, None if it was not asked


@dataclass(frozen=True, slots=True, kw_only=True)
class PreparedPage:
    """A page ready for OCR: a cleaned colour image and a black-and-white copy."""

    page_index: int
    clean: PageImage
    binarised: PageImage
    steps: PreparationSteps
