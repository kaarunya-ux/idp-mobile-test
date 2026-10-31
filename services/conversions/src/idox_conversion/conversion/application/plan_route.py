"""The route planner: (source, target) to the stages that convert one into the other (IDP-03 ch. 3.3, 3.4)."""

from idox_conversion.conversion.domain.exceptions import RouteUnsupportedError
from idox_conversion.conversion.domain.formats import SourceFormat, TargetFormat
from idox_conversion.conversion.domain.route import DRAW_STAGES, MODEL_STAGES, Route

# The conversions the service offers. 27 pairs; every other pair is CNV-422-ROUTE-001.
_TARGETS: dict[SourceFormat, frozenset[TargetFormat]] = {
    SourceFormat.PPTX: frozenset({TargetFormat.DOCX, TargetFormat.XLSX, TargetFormat.CSV, TargetFormat.PDF}),
    SourceFormat.XLSX: frozenset({TargetFormat.DOCX, TargetFormat.PPTX, TargetFormat.CSV, TargetFormat.PDF}),
    SourceFormat.CSV: frozenset({TargetFormat.DOCX, TargetFormat.XLSX, TargetFormat.PPTX, TargetFormat.PDF}),
    SourceFormat.PDF: frozenset(
        {TargetFormat.DOCX, TargetFormat.XLSX, TargetFormat.PPTX, TargetFormat.CSV, TargetFormat.IMAGES}
    ),
    SourceFormat.DOCX: frozenset(
        {TargetFormat.PDF, TargetFormat.PPTX, TargetFormat.CSV, TargetFormat.IMAGES, TargetFormat.XLSX}
    ),
    SourceFormat.IMAGE: frozenset(
        {TargetFormat.DOCX, TargetFormat.PDF, TargetFormat.CSV, TargetFormat.PPTX, TargetFormat.XLSX}
    ),
}
# These only draw the pages, so no model reads them: an Office file or CSV to PDF, and anything to page images.
_DRAW_ONLY = frozenset(
    {
        (source, TargetFormat.PDF)
        for source in (SourceFormat.PPTX, SourceFormat.XLSX, SourceFormat.CSV, SourceFormat.DOCX)
    }
    | {(source, TargetFormat.IMAGES) for source in (SourceFormat.PDF, SourceFormat.DOCX)}
)


class RoutePlanner:
    """Builds the route for a conversion, or says there is none."""

    def plan(self, source: SourceFormat, target: TargetFormat) -> Route:
        if target not in _TARGETS.get(source, frozenset()):
            raise RouteUnsupportedError(f"No conversion from {source.value} to {target.value}.")
        stages = DRAW_STAGES if (source, target) in _DRAW_ONLY else MODEL_STAGES
        return Route(source=source, target=target, stages=stages)

    def offered(self) -> frozenset[tuple[SourceFormat, TargetFormat]]:
        """Every (source, target) pair the service converts."""
        return frozenset((source, target) for source, targets in _TARGETS.items() for target in targets)
