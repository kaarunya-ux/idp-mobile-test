"""What templates and document types need from outside (ports, standard ch. 7). Async: the API calls them."""

from collections.abc import Sequence
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Protocol, Self
from uuid import UUID

from extraction.template.domain.designer import Sample, SampleFile, TestRun
from extraction.template.domain.models import DocumentType, Template, VersionState


@dataclass(frozen=True, slots=True, kw_only=True)
class TemplateQuery:
    """What the caller may see, and their filters (IDP-03 ch. 2.10: scope, documentType, q, state)."""

    account_id: UUID | None  # standard templates plus this account's
    scope: str | None  # "STANDARD", "ORG" or None for both
    document_type: str | None
    text: str | None
    state: VersionState | None
    after: tuple[str, UUID] | None  # cursor: (name, id) of the last item seen
    limit: int


@dataclass(frozen=True, slots=True, kw_only=True)
class StoredResponse:
    request_hash: str
    body: dict[str, Any]


class TemplateRepositoryPort(Protocol):
    async def get(self, template_id: UUID) -> Template | None:
        """With every version, locked for update."""
        ...

    async def find(self, query: TemplateQuery) -> Sequence[Template]:
        """Ordered by name then id; at most ``query.limit + 1``, so the caller knows whether there are more."""
        ...

    async def add(self, template: Template) -> None: ...

    async def save(self, template: Template, *, expected_row_version: int) -> None:
        """Raise ``VersionMismatchError`` when someone else saved first (optimistic lock)."""
        ...


class DocumentTypeRepositoryPort(Protocol):
    async def get(self, code: str, account_id: UUID | None) -> DocumentType | None:
        """The account's own type of that code, else the standard one."""
        ...

    async def list_visible(self, account_id: UUID | None) -> Sequence[DocumentType]: ...

    async def add(self, document_type: DocumentType) -> None: ...

    async def save(self, document_type: DocumentType) -> None: ...


class IdempotencyStorePort(Protocol):
    """Results of non-idempotent writes, by caller and ``Idempotency-Key`` (RTR-08)."""

    async def get(self, owner: str, key: str) -> StoredResponse | None: ...

    async def put(self, owner: str, key: str, response: StoredResponse) -> None: ...


class SampleRepositoryPort(Protocol):
    async def add(self, sample: Sample) -> None: ...

    async def get(self, sample_id: UUID) -> Sample | None: ...

    async def for_version(self, template_id: UUID, version: int) -> Sequence[Sample]: ...


class TestRunRepositoryPort(Protocol):
    async def add(self, run: TestRun) -> None: ...

    async def get(self, run_id: UUID) -> TestRun | None: ...

    async def latest_completed(self, template_id: UUID, version: int) -> TestRun | None: ...


class TemplateUnitOfWorkPort(Protocol):
    @property
    def templates(self) -> TemplateRepositoryPort: ...

    @property
    def samples(self) -> SampleRepositoryPort: ...

    @property
    def test_runs(self) -> TestRunRepositoryPort: ...

    @property
    def document_types(self) -> DocumentTypeRepositoryPort: ...

    @property
    def idempotency(self) -> IdempotencyStorePort: ...

    async def __aenter__(self) -> Self: ...

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None: ...

    async def commit(self) -> None: ...


class DesignerTasksPort(Protocol):
    """Hands work to the pipeline workers: preparing a sample, running a test (Celery, ``pipeline`` queue)."""

    async def prepare_sample(self, sample_id: UUID) -> None: ...

    async def run_test(self, run_id: UUID) -> None: ...


@dataclass(frozen=True, slots=True, kw_only=True)
class Suggestion:
    fields: Sequence[dict[str, Any]]
    tables: Sequence[dict[str, Any]]
    model_id: str
    prompt_version: str


class TemplateSuggesterPort(Protocol):
    """ai-gateway's ``template-suggest``. Raises ``SuggestionsUnavailableError`` when it can't answer."""

    async def suggest(
        self, page: SampleFile, words: Sequence[dict[str, Any]], account_id: UUID | None
    ) -> Suggestion: ...


class SampleStoragePort(Protocol):
    """The prepared samples in ``idp-templates``."""

    async def read_words(self, key: str) -> list[dict[str, Any]]: ...

    async def delete_account(self, account_id: UUID) -> int:
        """Delete every sample file of an account (``AccountDeleted``); returns how many."""
        ...
