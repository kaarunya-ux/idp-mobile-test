"""Rules used in more than one place (standard ch. 3.3: ``*Policy``)."""

from conversion.conversion.domain.route import OCR_CONVERTERS, Converter, Route
from conversion.conversion.domain.values import WorkerPool

_PDF_POOL_CONVERTERS = frozenset({Converter.PDF_TO_IMAGES, Converter.IMAGES_TO_PDF, Converter.IMAGE_FORMATS})


def worker_pool_for(route: Route) -> WorkerPool:
    """The pool that runs a whole route. All its steps share one local folder, so one worker runs them all; the
    scarcest resource the route needs decides where (OCR, then LibreOffice, then the document libraries)."""
    converters = set(route.converters)
    if converters & OCR_CONVERTERS:
        return WorkerPool.OCR
    if Converter.OFFICE_TO_PDF in converters:
        return WorkerPool.OFFICE
    if converters - _PDF_POOL_CONVERTERS:
        return WorkerPool.DATA
    return WorkerPool.PDF
