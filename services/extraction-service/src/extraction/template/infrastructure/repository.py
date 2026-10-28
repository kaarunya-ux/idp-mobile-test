"""Templates, document types and idempotency records on PostgreSQL, async (standard ch. 8 and 11; TX-01)."""

from collections.abc import Sequence
from types import TracebackType
from typing import Self
from uuid import UUID

from sqlalchemy import Select, or_, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from extraction.core.clock import Clock
from extraction.core.db import AsyncSessionFactory
from extraction.core.ids import new_id
from extraction.template.domain.designer import Sample, SampleFile, SampleStatus, TestRun, TestRunStatus
from extraction.template.domain.exceptions import VersionMismatchError
from extraction.template.domain.models import (
    DocumentType,
    Template,
    TemplateStatus,
    TemplateVersion,
    VersionState,
)
from extraction.template.domain.ports import StoredResponse, TemplateQuery
from extraction.template.infrastructure.orm import (
    DocumentTypeOrm,
    IdempotencyRecordOrm,
    TemplateOrm,
    TemplateSampleOrm,
    TemplateTestRunOrm,
    TemplateVersionOrm,
)


class SqlTemplateRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, template_id: UUID) -> Template | None:
        row = await self._session.get(TemplateOrm, template_id, with_for_update=True)
        return _template(row) if row is not None else None

    async def find(self, query: TemplateQuery) -> Sequence[Template]:
        statement = select(TemplateOrm).where(
            TemplateOrm.status == TemplateStatus.ACTIVE,
            or_(TemplateOrm.account_id.is_(None), TemplateOrm.account_id == query.account_id),
        )
        if query.scope == "STANDARD":
            statement = statement.where(TemplateOrm.account_id.is_(None))
        elif query.scope == "ORG":
            statement = statement.where(TemplateOrm.account_id == query.account_id)
        if query.document_type:
            statement = statement.where(TemplateOrm.document_type_code == query.document_type)
        if query.text:
            statement = statement.where(TemplateOrm.name.ilike(f"%{_escape_like(query.text)}%", escape="\\"))
        if query.state is not None:
            statement = statement.where(TemplateOrm.versions.any(TemplateVersionOrm.state == query.state.value))
        if query.after is not None:
            name, last_id = query.after
            statement = statement.where(
                or_(TemplateOrm.name > name, (TemplateOrm.name == name) & (TemplateOrm.id > last_id))
            )
        rows = await self._session.scalars(statement.order_by(TemplateOrm.name, TemplateOrm.id).limit(query.limit + 1))
        return [_template(row) for row in rows]

    async def add(self, template: Template) -> None:
        row = TemplateOrm(id=template.id)
        _fill(row, template)
        self._session.add(row)

    async def save(self, template: Template, *, expected_row_version: int) -> None:
        # the row lock from get() plus this check make concurrent saves fail cleanly instead of overwriting
        changed = await self._session.execute(
            update(TemplateOrm)
            .where(TemplateOrm.id == template.id, TemplateOrm.version == expected_row_version)
            .values(version=template.row_version)
            .execution_options(synchronize_session=False)
        )
        if changed.rowcount != 1:  # type: ignore[attr-defined]  # CursorResult exposes rowcount for UPDATE
            raise VersionMismatchError
        row = await self._session.get(TemplateOrm, template.id)
        if row is None:
            raise VersionMismatchError
        _fill(row, template)


class SqlDocumentTypeRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def get(self, code: str, account_id: UUID | None) -> DocumentType | None:
        rows = await self._session.scalars(
            select(DocumentTypeOrm).where(
                DocumentTypeOrm.code == code,
                or_(DocumentTypeOrm.account_id.is_(None), DocumentTypeOrm.account_id == account_id),
            )
        )
        found = sorted(rows, key=lambda r: r.account_id is None)  # the account's own type wins over the standard
        return _document_type(found[0]) if found else None

    async def list_visible(self, account_id: UUID | None) -> Sequence[DocumentType]:
        rows = await self._session.scalars(
            select(DocumentTypeOrm)
            .where(or_(DocumentTypeOrm.account_id.is_(None), DocumentTypeOrm.account_id == account_id))
            .order_by(DocumentTypeOrm.code)
        )
        return [_document_type(row) for row in rows]

    async def add(self, document_type: DocumentType) -> None:
        row = DocumentTypeOrm(id=new_id())
        _fill_type(row, document_type)
        self._session.add(row)

    async def save(self, document_type: DocumentType) -> None:
        rows = await self._session.scalars(
            select(DocumentTypeOrm).where(
                DocumentTypeOrm.code == document_type.code,
                DocumentTypeOrm.account_id.is_(None)
                if document_type.account_id is None
                else DocumentTypeOrm.account_id == document_type.account_id,
            )
        )
        row = rows.one()
        _fill_type(row, document_type)


class SqlIdempotencyStore:
    def __init__(self, session: AsyncSession, clock: Clock) -> None:
        self._session = session
        self._clock = clock

    async def get(self, owner: str, key: str) -> StoredResponse | None:
        row = await self._session.get(IdempotencyRecordOrm, (owner, key))
        return StoredResponse(request_hash=row.request_hash, body=row.response) if row else None

    async def put(self, owner: str, key: str, response: StoredResponse) -> None:
        self._session.add(
            IdempotencyRecordOrm(
                owner=owner,
                key=key,
                request_hash=response.request_hash,
                response=response.body,
                created_at=self._clock.now(),
            )
        )


async def version_id(session: AsyncSession, template_id: UUID, version: int) -> UUID | None:
    found = await session.scalar(
        select(TemplateVersionOrm.id).where(
            TemplateVersionOrm.template_id == template_id, TemplateVersionOrm.number == version
        )
    )
    return found


class SqlSampleRepository:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, sample: Sample) -> None:
        version = await version_id(self._session, sample.template_id, sample.version)
        if version is None:
            raise VersionMismatchError
        self._session.add(
            TemplateSampleOrm(
                id=sample.id,
                template_version_id=version,
                files=[{"bucket": f.bucket, "key": f.key} for f in sample.files],
                expected=dict(sample.expected),
                status=sample.status,
                page_key=sample.page_key,
                words_key=sample.words_key,
                error_code=sample.error_code,
                created_at=sample.created_at,
            )
        )

    async def get(self, sample_id: UUID) -> Sample | None:
        row = await self._session.execute(_samples().where(TemplateSampleOrm.id == sample_id))
        found = row.first()
        return _sample(found[0], found[1], found[2]) if found else None

    async def for_version(self, template_id: UUID, version: int) -> Sequence[Sample]:
        rows = await self._session.execute(
            _samples()
            .where(TemplateVersionOrm.template_id == template_id, TemplateVersionOrm.number == version)
            .order_by(TemplateSampleOrm.created_at, TemplateSampleOrm.id)
        )
        return [_sample(row[0], row[1], row[2]) for row in rows]


class SqlTestRunRepository:
    __test__ = False

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def add(self, run: TestRun) -> None:
        version = await version_id(self._session, run.template_id, run.version)
        if version is None:
            raise VersionMismatchError
        self._session.add(
            TemplateTestRunOrm(
                id=run.id,
                template_version_id=version,
                status=run.status,
                samples=run.samples,
                report=run.report,
                definition_hash=run.definition_hash,
                error_code=run.error_code,
                created_at=run.created_at,
                finished_at=run.finished_at,
            )
        )

    async def get(self, run_id: UUID) -> TestRun | None:
        found = (await self._session.execute(_runs().where(TemplateTestRunOrm.id == run_id))).first()
        return _run(found[0], found[1], found[2]) if found else None

    async def latest_completed(self, template_id: UUID, version: int) -> TestRun | None:
        found = (
            await self._session.execute(
                _runs()
                .where(
                    TemplateVersionOrm.template_id == template_id,
                    TemplateVersionOrm.number == version,
                    TemplateTestRunOrm.status == TestRunStatus.COMPLETED,
                )
                .order_by(TemplateTestRunOrm.finished_at.desc())
                .limit(1)
            )
        ).first()
        return _run(found[0], found[1], found[2]) if found else None


class SqlTemplateUnitOfWork:
    """One transaction (TX-01). Leaving the block without ``commit()`` rolls everything back (TX-02)."""

    def __init__(self, sessions: AsyncSessionFactory, clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock
        self._session: AsyncSession | None = None

    async def __aenter__(self) -> Self:
        self._session = self._sessions()
        await self._session.begin()
        return self

    async def __aexit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, traceback: TracebackType | None
    ) -> None:
        session = self._open()
        if session.in_transaction():
            await session.rollback()
        await session.close()
        self._session = None

    @property
    def templates(self) -> SqlTemplateRepository:
        return SqlTemplateRepository(self._open())

    @property
    def document_types(self) -> SqlDocumentTypeRepository:
        return SqlDocumentTypeRepository(self._open())

    @property
    def samples(self) -> SqlSampleRepository:
        return SqlSampleRepository(self._open())

    @property
    def test_runs(self) -> SqlTestRunRepository:
        return SqlTestRunRepository(self._open())

    @property
    def idempotency(self) -> SqlIdempotencyStore:
        return SqlIdempotencyStore(self._open(), self._clock)

    async def commit(self) -> None:
        await self._open().commit()

    def _open(self) -> AsyncSession:
        if self._session is None:
            raise RuntimeError("the unit of work is used outside its 'async with' block")
        return self._session


# --- Mapping --------------------------------------------------------------------------------------------------


def _template(row: TemplateOrm) -> Template:
    return Template(
        id=row.id,
        account_id=row.account_id,
        document_type_code=row.document_type_code,
        name=row.name,
        description=row.description,
        status=TemplateStatus(row.status),
        created_by=row.created_by,
        created_at=row.created_at,
        updated_at=row.updated_at,
        row_version=row.version,
        versions=[
            TemplateVersion(
                number=v.number,
                state=VersionState(v.state),
                definition=v.definition,
                created_at=v.created_at,
                published_at=v.published_at,
                published_by=v.published_by,
                publish_reason=v.publish_reason,
                accuracy=v.accuracy,
            )
            for v in row.versions
        ],
    )


def _fill(row: TemplateOrm, template: Template) -> None:
    row.account_id = template.account_id
    row.document_type_code = template.document_type_code
    row.name = template.name
    row.description = template.description
    row.current_version = template.current_version
    row.status = template.status
    row.created_by = template.created_by
    row.version = template.row_version
    row.created_at = template.created_at
    row.updated_at = template.updated_at
    by_number = {v.number: v for v in row.versions}
    for version in template.versions:
        stored = by_number.get(version.number)
        if stored is None:
            stored = TemplateVersionOrm(id=new_id(), number=version.number, created_at=version.created_at)
            row.versions.append(stored)
        stored.state = version.state
        stored.definition = version.definition
        stored.published_at = version.published_at
        stored.published_by = version.published_by
        stored.publish_reason = version.publish_reason
        stored.accuracy = version.accuracy


def _document_type(row: DocumentTypeOrm) -> DocumentType:
    return DocumentType(
        code=row.code,
        account_id=row.account_id,
        name=row.name,
        category=row.category,
        classifier_hints=row.classifier_hints,
        default_template_id=row.default_template_id,
        active=row.active,
    )


def _fill_type(row: DocumentTypeOrm, document_type: DocumentType) -> None:
    row.account_id = document_type.account_id
    row.code = document_type.code
    row.name = document_type.name
    row.category = document_type.category
    row.classifier_hints = document_type.classifier_hints
    row.default_template_id = document_type.default_template_id
    row.active = document_type.active


def _samples() -> Select[TemplateSampleOrm, UUID, int]:
    return select(TemplateSampleOrm, TemplateVersionOrm.template_id, TemplateVersionOrm.number).join(
        TemplateVersionOrm, TemplateVersionOrm.id == TemplateSampleOrm.template_version_id
    )


def _sample(row: TemplateSampleOrm, template_id: UUID, version: int) -> Sample:
    return Sample(
        id=row.id,
        template_id=template_id,
        version=version,
        files=tuple(SampleFile(bucket=f["bucket"], key=f["key"]) for f in row.files),
        expected=dict(row.expected),
        status=SampleStatus(row.status),
        created_at=row.created_at,
        page_key=row.page_key,
        words_key=row.words_key,
        error_code=row.error_code,
    )


def _runs() -> Select[TemplateTestRunOrm, UUID, int]:
    return select(TemplateTestRunOrm, TemplateVersionOrm.template_id, TemplateVersionOrm.number).join(
        TemplateVersionOrm, TemplateVersionOrm.id == TemplateTestRunOrm.template_version_id
    )


def _run(row: TemplateTestRunOrm, template_id: UUID, version: int) -> TestRun:
    return TestRun(
        id=row.id,
        template_id=template_id,
        version=version,
        status=TestRunStatus(row.status),
        definition_hash=row.definition_hash,
        samples=list(row.samples),
        created_at=row.created_at,
        report=row.report,
        finished_at=row.finished_at,
        error_code=row.error_code,
    )


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
