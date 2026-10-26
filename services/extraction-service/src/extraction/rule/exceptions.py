"""Errors raised by rules when the template definition itself is wrong, not when a value fails a rule."""

from typing import ClassVar

from extraction.core.error_codes import ErrorCode
from extraction.core.errors import DomainError


class UnsupportedDefinitionError(DomainError):
    """A rule or normalizer in a template definition cannot be applied (maps to ``EXT-422-DEFINITION-001``)."""

    code: ClassVar[ErrorCode] = ErrorCode.DEFINITION_INVALID
    title: ClassVar[str] = "Template definition is not valid"
