"""``DesignerWorkStorePort`` on ``extraction_db``: the designer's samples and test runs, as the workers change them.

Sync, for the Celery workers. Each method is one short transaction; a QUEUED test run is claimed with a row lock,
so two workers can't run the same one.
"""

from collections.abc import Mapping
from typing import Any
from uuid import UUID

from sqlalchemy import select

from extraction.core.clock import Clock
from extraction.core.db import SessionFactory
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.ports import SampleWork, TestRunWork, TestSample
from extraction.template.domain.designer import SampleStatus, TestRunStatus
from extraction.template.infrastructure.orm import (
    TemplateOrm,
    TemplateSampleOrm,
    TemplateTestRunOrm,
    TemplateVersionOrm,
)


class SqlDesignerWorkStore:
    def __init__(self, sessions: SessionFactory, clock: Clock) -> None:
        self._sessions = sessions
        self._clock = clock

    def pending_sample(self, sample_id: UUID) -> SampleWork | None:
        with self._sessions() as session:
            found = session.execute(
                select(
                    TemplateSampleOrm, TemplateVersionOrm.template_id, TemplateVersionOrm.number, TemplateOrm.account_id
                )
                .join(TemplateVersionOrm, TemplateVersionOrm.id == TemplateSampleOrm.template_version_id)
                .join(TemplateOrm, TemplateOrm.id == TemplateVersionOrm.template_id)
                .where(TemplateSampleOrm.id == sample_id, TemplateSampleOrm.status == SampleStatus.PENDING)
            ).first()
        if found is None:
            return None
        row, template_id, number, account_id = found
        return SampleWork(
            id=row.id, template_id=template_id, version=number, account_id=account_id, files=_refs(row.files)
        )

    def sample_ready(self, sample_id: UUID, *, page_key: str, words_key: str) -> None:
        with self._sessions.begin() as session:
            row = session.get(TemplateSampleOrm, sample_id, with_for_update=True)
            if row is not None:
                row.status, row.page_key, row.words_key, row.error_code = SampleStatus.READY, page_key, words_key, None

    def sample_failed(self, sample_id: UUID, code: str) -> None:
        with self._sessions.begin() as session:
            row = session.get(TemplateSampleOrm, sample_id, with_for_update=True)
            if row is not None:
                row.status, row.error_code = SampleStatus.FAILED, code

    def start_test_run(self, run_id: UUID) -> TestRunWork | None:
        with self._sessions.begin() as session:
            run = session.get(TemplateTestRunOrm, run_id, with_for_update=True)
            if run is None or run.status != TestRunStatus.QUEUED:
                return None
            version = session.get(TemplateVersionOrm, run.template_version_id)
            template = session.get(TemplateOrm, version.template_id) if version is not None else None
            if version is None or template is None:
                return None
            run.status = TestRunStatus.RUNNING
            files = {
                str(s.id): _refs(s.files)
                for s in session.scalars(
                    select(TemplateSampleOrm).where(TemplateSampleOrm.template_version_id == version.id)
                )
            }
            samples = [
                TestSample(sample_id=s["sampleId"], files=files[s["sampleId"]], expected=dict(s["expected"]))
                for s in run.samples
                if s["sampleId"] in files  # a sample deleted since the run started is left out
            ]
            return TestRunWork(
                id=run.id,
                template_id=template.id,
                version=version.number,
                account_id=template.account_id,
                definition=dict(version.definition),
                samples=samples,
            )

    def finish_test_run(self, run_id: UUID, report: Mapping[str, Any]) -> None:
        with self._sessions.begin() as session:
            run = session.get(TemplateTestRunOrm, run_id, with_for_update=True)
            if run is None:
                return
            run.status, run.report, run.finished_at = TestRunStatus.COMPLETED, dict(report), self._clock.now()
            version = session.get(TemplateVersionOrm, run.template_version_id)
            if version is not None:
                fields: Mapping[str, Mapping[str, Any]] = report.get("fields") or {}
                version.accuracy = {
                    "testRunId": str(run.id),
                    "accuracy": report.get("accuracy"),
                    "fields": {key: entry.get("accuracy") for key, entry in fields.items()},
                }

    def fail_test_run(self, run_id: UUID, code: str) -> None:
        with self._sessions.begin() as session:
            run = session.get(TemplateTestRunOrm, run_id, with_for_update=True)
            if run is not None:
                run.status, run.error_code, run.finished_at = TestRunStatus.FAILED, code, self._clock.now()


def _refs(files: list[dict[str, str]]) -> tuple[ObjectRef, ...]:
    return tuple(ObjectRef(bucket=f["bucket"], key=f["key"]) for f in files)
