"""The page quality gate: what is measured and when a page is too poor to read (IDP-03 ch. 2.6 stage 2, ch. 2.7).

Measuring is infrastructure (it needs OpenCV); judging the numbers is a business rule and lives here as pure code.
The thresholds come from settings, never from literals (ML-07).
"""

from dataclasses import dataclass
from enum import StrEnum


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityScores:
    """What was measured on one page (on the document itself when its edges were found)."""

    blur: float  # variance of the Laplacian: higher is sharper
    brightness: float  # mean luminance, 0 (black) to 1 (white)
    glare: float  # share of the page covered by glare, 0 to 1
    short_side_px: int  # the shorter side of the document, in pixels
    edges_found: bool  # whether the document's edges could be found (or it fills the frame)


@dataclass(frozen=True, slots=True, kw_only=True)
class QualityThresholds:
    """The limits a page is judged against (IDP-03 ch. 2.7)."""

    blur_min: float
    brightness_min: float
    brightness_max: float
    glare_max: float
    min_short_side_px: int
    require_document_edges: bool


class QualityIssue(StrEnum):
    """Why a page is refused, listed in the order of their error codes (``EXT-422-QUALITY-001`` to ``-005``)."""

    BLURRED = "BLURRED"
    TOO_DARK = "TOO_DARK"
    TOO_BRIGHT = "TOO_BRIGHT"
    GLARE = "GLARE"
    LOW_RESOLUTION = "LOW_RESOLUTION"
    EDGES_NOT_FOUND = "EDGES_NOT_FOUND"


def list_quality_issues(scores: QualityScores, thresholds: QualityThresholds) -> tuple[QualityIssue, ...]:
    """Every check the page fails, in code order; an empty result means the page passes."""
    issues: list[QualityIssue] = []
    if scores.blur < thresholds.blur_min:
        issues.append(QualityIssue.BLURRED)
    if scores.brightness < thresholds.brightness_min:
        issues.append(QualityIssue.TOO_DARK)
    if scores.brightness > thresholds.brightness_max:
        issues.append(QualityIssue.TOO_BRIGHT)
    if scores.glare > thresholds.glare_max:
        issues.append(QualityIssue.GLARE)
    if scores.short_side_px < thresholds.min_short_side_px:
        issues.append(QualityIssue.LOW_RESOLUTION)
    if thresholds.require_document_edges and not scores.edges_found:
        issues.append(QualityIssue.EDGES_NOT_FOUND)
    return tuple(issues)
