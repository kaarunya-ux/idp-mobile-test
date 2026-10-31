"""Business errors of the conversion domain (ERR-02). Each declares its code and title (ERR-05)."""

from typing import ClassVar

from idox_conversion.core.error_codes import ErrorCode
from idox_conversion.core.errors import DomainError


class RouteUnsupportedError(DomainError):
    """The planner cannot build a route from this source to this target."""

    code: ClassVar[ErrorCode] = ErrorCode.ROUTE_UNSUPPORTED
    title: ClassVar[str] = "Conversion not supported"


class ModelUnavailableError(DomainError):
    """The model server did not answer, or kept answering with a server error, after the allowed tries (LLM-05)."""

    code: ClassVar[ErrorCode] = ErrorCode.MODEL_UNAVAILABLE
    title: ClassVar[str] = "Model server unavailable"


class ModelAnswerInvalidError(DomainError):
    """The model answered, but not with a valid page: cut off, not JSON, or not the schema (LLM-03)."""

    code: ClassVar[ErrorCode] = ErrorCode.MODEL_ANSWER_INVALID
    title: ClassVar[str] = "Model answer invalid"
