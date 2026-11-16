"""The gateway's business errors (IDP-03 ch. 4.6). Each carries its code; ``problem_details`` turns it into a reply."""

from typing import ClassVar

from ai_gateway.core.error_codes import ErrorCode
from ai_gateway.core.errors import DomainError


class CapReachedError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.CAP_REACHED
    title: ClassVar[str] = "Daily AI allowance used"

    def __init__(self) -> None:
        super().__init__("This account has used its AI calls for today.")


class BusyError(DomainError):
    """No model slot within the wait timeout. Callers treat it as "AI unavailable" (IDP-03 ch. 4.5)."""

    code: ClassVar[ErrorCode] = ErrorCode.BUSY
    title: ClassVar[str] = "AI is busy"
    retryable: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__("The model is busy. Try again later.")


class ModelUnavailableError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.MODEL_UNAVAILABLE
    title: ClassVar[str] = "AI unavailable"
    retryable: ClassVar[bool] = True

    def __init__(self, detail: str = "The model server is not available right now.") -> None:
        super().__init__(detail)


class OutputInvalidError(DomainError):
    """The model's answer did not match the schema, also after one repair attempt (LLM-03)."""

    code: ClassVar[ErrorCode] = ErrorCode.OUTPUT_INVALID
    title: ClassVar[str] = "AI answer not usable"
    retryable: ClassVar[bool] = True

    def __init__(self) -> None:
        super().__init__("The model's answer did not have the expected shape.")


class InputInvalidError(DomainError):
    code: ClassVar[ErrorCode] = ErrorCode.INPUT_INVALID
    title: ClassVar[str] = "Input not usable"

    def __init__(self, detail: str) -> None:
        super().__init__(detail)


class AnswerShapeError(ValueError):
    """Inside the gateway only: one model answer isn't the schema. The runner then tries one repair."""
