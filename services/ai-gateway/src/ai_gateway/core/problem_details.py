"""RFC 9457 problem details: the one place that turns every error into the shared JSON shape (ERR-01, ERR-04, ERR-06).

The handlers registered by ``register_exception_handlers`` return the same JSON shape the Java services return, so a
client cannot tell which language answered (MSV-05). They live apart from ``DomainError`` so that domain code can
import the base class without importing a web framework (DOM-01).
"""

from datetime import datetime

import structlog
from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from pydantic import BaseModel, ConfigDict
from pydantic.alias_generators import to_camel

from ai_gateway.core.clock import Clock
from ai_gateway.core.error_codes import ErrorCode
from ai_gateway.core.errors import DomainError
from ai_gateway.core.ids import new_id
from ai_gateway.core.logging import get_correlation_id

PROBLEM_MEDIA_TYPE = "application/problem+json"
_UNKNOWN_FIELD = "(request)"
log = structlog.get_logger(__name__)


class FieldProblem(BaseModel):
    """One invalid field in a request. The offending value is never echoed back (it may be personal data)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    field: str
    message: str


class ProblemDetails(BaseModel):
    """RFC 9457 body with the IDP additions: ``code``, ``correlationId``, ``timestamp`` and optional ``errors``."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True)

    type: str = "about:blank"  # RFC 9457 ch. 4.2.1: no further meaning than the status and title
    title: str
    status: int
    code: str
    detail: str
    correlation_id: str
    timestamp: datetime
    errors: tuple[FieldProblem, ...] | None = None


def register_exception_handlers(app: FastAPI, clock: Clock) -> None:
    """Install the three handlers. Called once from ``create_app()`` (ERR-01)."""

    def respond(
        *, code: ErrorCode, title: str, detail: str, errors: tuple[FieldProblem, ...] | None = None
    ) -> JSONResponse:
        body = ProblemDetails(
            title=title,
            status=code.http_status,
            code=code.value,
            detail=detail,
            correlation_id=get_correlation_id() or str(new_id()),
            timestamp=clock.now().replace(microsecond=0),
            errors=errors,
        )
        return JSONResponse(
            status_code=code.http_status,
            content=body.model_dump(mode="json", by_alias=True, exclude_none=True),
            media_type=PROBLEM_MEDIA_TYPE,
        )

    async def on_domain_error(_: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, DomainError):  # the framework only routes DomainError here; this narrows the type
            raise exc
        field_errors = getattr(exc, "field_errors", ())  # e.g. every invalid part of a template definition
        problems = tuple(FieldProblem(field=field, message=message) for field, message in field_errors) or None
        return respond(code=exc.code, title=exc.title, detail=exc.detail, errors=problems)

    async def on_request_invalid(_: Request, exc: Exception) -> JSONResponse:
        if not isinstance(exc, RequestValidationError):
            raise exc
        problems = tuple(
            FieldProblem(field=_describe_location(error["loc"]), message=str(error["msg"])) for error in exc.errors()
        )
        return respond(
            code=ErrorCode.REQUEST_INVALID,
            title="Request is not valid",
            detail="One or more fields did not pass validation.",
            errors=problems,
        )

    async def on_unexpected_error(_: Request, exc: Exception) -> JSONResponse:
        log.error("unhandled_error", error_type=type(exc).__name__, exc_info=exc)  # the trace stays in the log (ERR-06)
        return respond(
            code=ErrorCode.INTERNAL_ERROR,
            title="Unexpected error",
            detail="The request could not be processed. Quote the correlation id when you report it.",
        )

    app.add_exception_handler(DomainError, on_domain_error)
    app.add_exception_handler(RequestValidationError, on_request_invalid)
    app.add_exception_handler(Exception, on_unexpected_error)


def _describe_location(location: tuple[int | str, ...]) -> str:
    """``("body", "lines", 0, "quantity")`` → ``lines[0].quantity``; the first part (body, query, ...) is dropped."""
    path = ""
    for part in location[1:]:
        path += f"[{part}]" if isinstance(part, int) else (f".{part}" if path else str(part))
    return path or _UNKNOWN_FIELD
