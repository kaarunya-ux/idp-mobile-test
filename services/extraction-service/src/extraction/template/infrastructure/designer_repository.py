"""The template designer's samples and test runs on PostgreSQL, async (IDP-03 ch. 2.9; standard ch. 8)."""

from collections.abc import Sequence
from uuid import UUID

from sqlalchemy import Select, select
from sqlalchemy.ext.asyncio import AsyncSession

from extraction.template.domain.designer import Sample, SampleFile, SampleStatus, TestRun, TestRunStatus
from extraction.template.domain.exceptions import VersionMismatchError
from extraction.template.infrastructure.orm import TemplateSampleOrm, TemplateTestRunOrm, TemplateVersionOrm


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
