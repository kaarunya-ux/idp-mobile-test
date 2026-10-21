"""Errors raised by the conversion rules (ERR-02, IDP-03 ch. 3.8)."""

from conversion.conversion.domain.formats import Format
from conversion.core.error_codes import ErrorCode
from conversion.core.errors import DomainError


class RouteNotFoundError(DomainError):
    """No route turns ``source`` into ``target``. Reported to the user as ``CNV-422-ROUTE-001``."""

    code = ErrorCode.ROUTE_NOT_SUPPORTED
    title = "Conversion not supported"

    def __init__(self, source: Format, target: Format) -> None:
        super().__init__(f"There is no conversion from {source} to {target}.")
        self.source = source
        self.target = target


class InvalidRouteError(ValueError):
    """A route whose steps don't chain together. A bug in the route table, never a user error; tests catch it."""
