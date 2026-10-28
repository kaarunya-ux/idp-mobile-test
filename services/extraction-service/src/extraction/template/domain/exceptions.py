"""Business errors of templates and document types (IDP-03 ch. 2.12)."""

from typing import ClassVar

from extraction.core.error_codes import ErrorCode
from extraction.core.errors import DomainError


class TemplateNotFoundError(DomainError):
    """Absent, or another account's (SEC-05: 404, not 403)."""

    code: ClassVar[ErrorCode] = ErrorCode.TEMPLATE_NOT_FOUND
    title: ClassVar[str] = "Template not found"

    def __init__(self) -> None:
        super().__init__("There is no such template or template version.")


class DocumentTypeNotFoundError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.DOCUMENT_TYPE_NOT_FOUND
    title: ClassVar[str] = "Document type not found"

    def __init__(self, code: str) -> None:
        super().__init__(f"There is no document type {code}.")


class DocumentTypeExistsError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.DOCUMENT_TYPE_EXISTS
    title: ClassVar[str] = "Document type exists"

    def __init__(self, code: str) -> None:
        super().__init__(f"Document type {code} already exists.")


class TemplateNotDraftError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.TEMPLATE_NOT_DRAFT
    title: ClassVar[str] = "Version is not a draft"

    def __init__(self, number: int) -> None:
        super().__init__(f"Version {number} is not a draft; create a new version to change it.")


class TemplateNotTestedError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.TEMPLATE_NOT_TESTED
    title: ClassVar[str] = "Version has not passed a test"

    def __init__(self) -> None:
        super().__init__(
            "Publishing needs a test run in the last 24 hours with every required field at 90 % or more, "
            "or an override with a reason."
        )


class TemplateStateError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.TEMPLATE_STATE
    title: ClassVar[str] = "Template can't do that now"


class DefinitionInvalidError(DomainError):
    """The definition doesn't match schema version 1 (IDP-03 ch. 2.5). ``problems`` names each bad element."""

    code: ClassVar[ErrorCode] = ErrorCode.DEFINITION_INVALID
    title: ClassVar[str] = "Template definition is not valid"

    def __init__(self, problems: list[tuple[str, str]]) -> None:
        super().__init__("The template definition is not valid; see errors.")
        self.field_errors = tuple(problems)


class VersionMismatchError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.VERSION_MISMATCH
    title: ClassVar[str] = "Template changed meanwhile"

    def __init__(self) -> None:
        super().__init__("The template was changed by someone else; reload it and try again.")


class IdempotencyKeyReusedError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.IDEMPOTENCY_KEY_REUSED
    title: ClassVar[str] = "Idempotency key reused"

    def __init__(self) -> None:
        super().__init__("This Idempotency-Key was already used for a different request.")


class SampleNotReadyError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.SAMPLE_NOT_READY
    title: ClassVar[str] = "Sample not ready"

    def __init__(self) -> None:
        super().__init__("The sample is still being prepared, or it could not be read; add another one.")


class SamplesMissingError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.SAMPLES_MISSING
    title: ClassVar[str] = "No samples to test"

    def __init__(self) -> None:
        super().__init__("A test run needs at least one prepared sample with expected values.")


class TestRunNotFoundError(DomainError):
    __test__ = False

    code: ClassVar[ErrorCode] = ErrorCode.TEST_RUN_NOT_FOUND
    title: ClassVar[str] = "Not found"

    def __init__(self) -> None:
        super().__init__("There is no such test run.")


class SuggestionsUnavailableError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.AI_UNAVAILABLE
    title: ClassVar[str] = "AI unavailable"
    retryable: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__("Field suggestions are not available right now. Try again later, or add fields by hand.")
