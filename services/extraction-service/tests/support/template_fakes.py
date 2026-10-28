"""In-memory async unit of work for the template use cases."""

import copy
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Self
from uuid import UUID

from extraction.template.application.commands import Caller
from extraction.template.domain.designer import Sample, TestRun, TestRunStatus
from extraction.template.domain.exceptions import VersionMismatchError
from extraction.template.domain.models import DocumentType, Template
from extraction.template.domain.ports import StoredResponse, TemplateQuery

NOW = datetime(2026, 10, 28, 9, 0, tzinfo=UTC)
ACCOUNT, OTHER = UUID(int=1), UUID(int=2)
ADMIN = Caller(actor_id="staff-1", account_id=None, is_platform_admin=True)
ORG = Caller(actor_id="user-1", account_id=ACCOUNT, is_platform_admin=False)
STRANGER = Caller(actor_id="user-2", account_id=OTHER, is_platform_admin=False)


class Clock:
    def now(self) -> datetime:
        return NOW


@dataclass
class Store:
    templates: dict[UUID, Template] = field(default_factory=dict)
    types: list[DocumentType] = field(default_factory=list)
    keys: dict[tuple[str, str], StoredResponse] = field(default_factory=dict)
    samples: dict[UUID, Sample] = field(default_factory=dict)
    runs: dict[UUID, TestRun] = field(default_factory=dict)


class _Templates:
    def __init__(self, staged: dict[UUID, Template]) -> None:
        self._staged = staged

    async def get(self, template_id: UUID) -> Template | None:
        return self._staged.get(template_id)

    async def find(self, query: TemplateQuery) -> list[Template]:
        found = [
            t
            for t in self._staged.values()
            if (t.account_id is None or t.account_id == query.account_id)
            and (query.scope != "STANDARD" or t.account_id is None)
            and (query.scope != "ORG" or t.account_id == query.account_id)
            and (query.document_type is None or t.document_type_code == query.document_type)
            and (query.text is None or query.text.lower() in t.name.lower())
            and (query.after is None or (t.name, t.id) > query.after)
        ]
        return sorted(found, key=lambda t: (t.name, t.id))[: query.limit + 1]

    async def add(self, template: Template) -> None:
        self._staged[template.id] = template

    async def save(self, template: Template, *, expected_row_version: int) -> None:
        stored = self._staged[template.id]
        if stored is not template and stored.row_version != expected_row_version:
            raise VersionMismatchError
        if (
            stored is template
            and expected_row_version != template.row_version - 1
            and expected_row_version != template.row_version
        ):
            raise VersionMismatchError
        self._staged[template.id] = template


class _Types:
    def __init__(self, staged: list[DocumentType]) -> None:
        self._staged = staged

    async def get(self, code: str, account_id: UUID | None) -> DocumentType | None:
        found = [t for t in self._staged if t.code == code and t.account_id in (None, account_id)]
        return sorted(found, key=lambda t: t.account_id is None)[0] if found else None

    async def list_visible(self, account_id: UUID | None) -> list[DocumentType]:
        return [t for t in self._staged if t.account_id in (None, account_id)]

    async def add(self, document_type: DocumentType) -> None:
        self._staged.append(document_type)

    async def save(self, document_type: DocumentType) -> None:
        self._staged[:] = [
            t for t in self._staged if (t.code, t.account_id) != (document_type.code, document_type.account_id)
        ]
        self._staged.append(document_type)


class _Keys:
    def __init__(self, staged: dict[tuple[str, str], StoredResponse]) -> None:
        self._staged = staged

    async def get(self, owner: str, key: str) -> StoredResponse | None:
        return self._staged.get((owner, key))

    async def put(self, owner: str, key: str, response: StoredResponse) -> None:
        self._staged[(owner, key)] = response


class _Samples:
    def __init__(self, staged: dict[UUID, Sample]) -> None:
        self._staged = staged

    async def add(self, sample: Sample) -> None:
        self._staged[sample.id] = sample

    async def get(self, sample_id: UUID) -> Sample | None:
        return self._staged.get(sample_id)

    async def for_version(self, template_id: UUID, version: int) -> list[Sample]:
        return [s for s in self._staged.values() if (s.template_id, s.version) == (template_id, version)]


class _Runs:
    def __init__(self, staged: dict[UUID, TestRun]) -> None:
        self._staged = staged

    async def add(self, run: TestRun) -> None:
        self._staged[run.id] = run

    async def get(self, run_id: UUID) -> TestRun | None:
        return self._staged.get(run_id)

    async def latest_completed(self, template_id: UUID, version: int) -> TestRun | None:
        done = [
            r
            for r in self._staged.values()
            if (r.template_id, r.version) == (template_id, version) and r.status is TestRunStatus.COMPLETED
        ]
        return max(done, key=lambda r: r.finished_at or NOW) if done else None


class Unit:
    """Copies the store on entry and writes back on commit, like a transaction."""

    def __init__(self, store: Store) -> None:
        self._store = store

    async def __aenter__(self) -> Self:
        self._templates = copy.deepcopy(self._store.templates)
        self._types = copy.deepcopy(self._store.types)
        self._keys = dict(self._store.keys)
        self._samples = copy.deepcopy(self._store.samples)
        self._runs = copy.deepcopy(self._store.runs)
        return self

    async def __aexit__(self, *_: object) -> None:
        return None

    @property
    def templates(self) -> _Templates:
        return _Templates(self._templates)

    @property
    def document_types(self) -> _Types:
        return _Types(self._types)

    @property
    def idempotency(self) -> _Keys:
        return _Keys(self._keys)

    @property
    def samples(self) -> _Samples:
        return _Samples(self._samples)

    @property
    def test_runs(self) -> _Runs:
        return _Runs(self._runs)

    async def commit(self) -> None:
        self._store.templates, self._store.types, self._store.keys = self._templates, self._types, self._keys
        self._store.samples, self._store.runs = self._samples, self._runs


def standard_types() -> list[DocumentType]:
    return [
        DocumentType(
            code=code,
            account_id=None,
            name=code,
            category="ID",
            classifier_hints={},
            default_template_id=None,
            active=True,
        )
        for code in ("PAN_CARD", "AADHAAR_CARD", "DRIVING_LICENCE")
    ]
