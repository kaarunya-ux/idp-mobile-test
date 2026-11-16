"""Page coordinates. Edit plans speak in fractions of the page as shown (rotation applied, origin top left); PDF
content is drawn in the page's own space (before rotation, origin bottom left). This maps one onto the other."""

from dataclasses import dataclass

import pikepdf

from conversion.conversion.domain.edit_plan import Box

type Matrix = tuple[float, float, float, float, float, float]


@dataclass(frozen=True, slots=True)
class PageFrame:
    """A page's media box and rotation, and its size as shown."""

    x0: float
    y0: float
    x1: float
    y1: float
    rotation: int  # 0, 90, 180 or 270, clockwise as viewers apply it

    @classmethod
    def of(cls, page: pikepdf.Page) -> "PageFrame":
        x0, y0, x1, y1 = (float(v) for v in page.mediabox)
        rotation = int(page.obj.get("/Rotate", 0)) % 360
        return cls(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1), rotation)

    @property
    def shown_size(self) -> tuple[float, float]:
        width, height = self.x1 - self.x0, self.y1 - self.y0
        return (height, width) if self.rotation in (90, 270) else (width, height)

    def to_user_space(self) -> Matrix:
        """The ``cm`` matrix that lets overlays draw in shown coordinates (origin bottom left, y up)."""
        match self.rotation:
            case 90:
                return (0.0, 1.0, -1.0, 0.0, self.x1, self.y0)
            case 180:
                return (-1.0, 0.0, 0.0, -1.0, self.x1, self.y1)
            case 270:
                return (0.0, -1.0, 1.0, 0.0, self.x0, self.y1)
            case _:
                return (1.0, 0.0, 0.0, 1.0, self.x0, self.y0)

    def shown_rect(self, box: Box) -> tuple[float, float, float, float]:
        """A plan box as (x, y, width, height) in shown points, origin bottom left."""
        width, height = self.shown_size
        x, y, w, h = box
        return (x * width, height - (y + h) * height, w * width, h * height)

    def box_from_user_rect(self, left: float, bottom: float, right: float, top: float) -> Box:
        """A rectangle in the page's own space (as PDFium reports text) as a plan box, clamped to the page."""
        corners = [self._shown_point(x, y) for x, y in ((left, bottom), (right, top))]
        width, height = self.shown_size
        xs, ys = sorted(c[0] for c in corners), sorted(c[1] for c in corners)
        fx0, fx1 = max(0.0, xs[0] / width), min(1.0, xs[1] / width)
        fy0, fy1 = max(0.0, 1 - ys[1] / height), min(1.0, 1 - ys[0] / height)
        return (fx0, fy0, max(fx1 - fx0, 1e-6), max(fy1 - fy0, 1e-6))

    def _shown_point(self, ux: float, uy: float) -> tuple[float, float]:
        match self.rotation:
            case 90:
                return (uy - self.y0, self.x1 - ux)
            case 180:
                return (self.x1 - ux, self.y1 - uy)
            case 270:
                return (self.y1 - uy, ux - self.x0)
            case _:
                return (ux - self.x0, uy - self.y0)
