"""A conversion route: how one source becomes one target (IDP-03 ch. 3.4).

Every route that reads a page with the model runs the same stages in the same order; a route that only draws pages
(Word to PDF, anything to page images) skips the model and everything that checks it.
"""

from dataclasses import dataclass
from enum import StrEnum

from idox_conversion.conversion.domain.formats import SourceFormat, TargetFormat


class Stage(StrEnum):
    """One stage of a conversion, in the order they run."""

    PREPARE = "prepare"  # make pages: draw the file, or take the PDF as it is, or turn the image into a page
    READ = "read"  # the model reads each page into blocks (headings, paragraphs, tables)
    CORRECT = "correct"  # libraries fix the reading against the page's text layer, or an OCR reading for a scan
    WRITE = "write"  # python-docx / openpyxl / python-pptx / csv write the file
    LAYOUT = "layout"  # footer, page breaks, repeating table headers
    CHECK = "check"  # the source's words and tables are compared with the output


MODEL_STAGES: tuple[Stage, ...] = (
    Stage.PREPARE,
    Stage.READ,
    Stage.CORRECT,
    Stage.WRITE,
    Stage.LAYOUT,
    Stage.CHECK,
)
DRAW_STAGES: tuple[Stage, ...] = (Stage.PREPARE,)


@dataclass(frozen=True, slots=True, kw_only=True)
class Route:
    """The stages that turn ``source`` into ``target``."""

    source: SourceFormat
    target: TargetFormat
    stages: tuple[Stage, ...]

    @property
    def uses_model(self) -> bool:
        """Whether the model server is needed for this route."""
        return Stage.READ in self.stages
