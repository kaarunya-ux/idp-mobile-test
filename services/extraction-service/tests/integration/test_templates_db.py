"""Templates on real PostgreSQL: the async repositories, the seed, and the worker's catalog reading what was stored."""

import pytest
from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from extraction.pipeline.domain.standard_templates import PAN_CARD, STANDARD_TEMPLATES
from extraction.pipeline.infrastructure.persistence.template_catalog import SqlTemplateCatalog
from extraction.seed import seed, standard_template_id
from extraction.template.application.commands import (
    CreateTemplateCommand,
    ListTemplatesCommand,
    SaveDraftCommand,
    VersionActionCommand,
)
from extraction.template.application.services.template_service import TemplateService
from extraction.template.domain.definition_json import to_json
from extraction.template.domain.exceptions import VersionMismatchError
from extraction.template.infrastructure.repository import SqlTemplateUnitOfWork
from support.template_fakes import ORG, STRANGER, Clock


def _service(async_sessions) -> TemplateService:
    return TemplateService(lambda: SqlTemplateUnitOfWork(async_sessions, Clock()), Clock())


def _create(key: str, name: str = "My PAN") -> CreateTemplateCommand:
    return CreateTemplateCommand(
        caller=ORG,
        idempotency_key=key,
        standard=False,
        name=name,
        description=None,
        document_type="PAN_CARD",
        definition=to_json(PAN_CARD),
    )


async def test_stores_the_standard_templates_once_and_the_worker_reads_them(sessions, async_sessions) -> None:
    added = seed(sessions)

    assert (added, seed(sessions)) == (6, 0)  # three document types, three templates; then nothing
    templates = SqlTemplateCatalog(sessions).templates_for(None, None)
    assert {t.document_type for t in templates} == {t.document_type for t in STANDARD_TEMPLATES}
    pan = next(t for t in templates if t.document_type == "PAN_CARD")
    assert (pan.template_id, pan.fields) == (str(standard_template_id("standard-pan-card")), PAN_CARD.fields)


async def test_runs_the_lifecycle_through_the_async_repository(sessions, async_sessions) -> None:
    seed(sessions)
    service = _service(async_sessions)

    created = await service.create(_create("key-db-0001"))
    again = await service.create(_create("key-db-0001"))
    saved = await service.save_draft(
        SaveDraftCommand(
            caller=ORG, template_id=created.id, number=1, expected_row_version=1, definition=to_json(PAN_CARD)
        )
    )
    published = await service.publish(
        VersionActionCommand(
            caller=ORG, idempotency_key="key-db-pub", template_id=created.id, number=1, override_reason="ok"
        )
    )
    drafted = await service.new_version(
        VersionActionCommand(caller=ORG, idempotency_key="key-db-new", template_id=created.id)
    )

    assert again.id == created.id
    assert (saved.row_version, published.current_version) == (2, 1)
    assert [(v.number, v.state) for v in drafted.versions] == [(1, "PUBLISHED"), (2, "DRAFT")]
    with pytest.raises(VersionMismatchError):
        await service.save_draft(
            SaveDraftCommand(
                caller=ORG, template_id=created.id, number=2, expected_row_version=1, definition=to_json(PAN_CARD)
            )
        )
    catalog = SqlTemplateCatalog(sessions, ttl_seconds=0).templates_for(ORG.account_id, str(created.id))
    assert [t.template_id for t in catalog] == [str(created.id)]  # the job's own template, as published
    assert len(SqlTemplateCatalog(sessions, ttl_seconds=0).templates_for(STRANGER.account_id, None)) == 3  # not theirs


async def test_pages_by_name_and_hides_other_accounts(sessions, async_sessions) -> None:
    seed(sessions)
    service = _service(async_sessions)
    for n in range(3):
        await service.create(_create(f"key-page-{n}", name=f"Org template {n}"))
    listing = lambda cursor, scope=None: ListTemplatesCommand(  # noqa: E731
        caller=ORG, scope=scope, document_type="PAN_CARD", text=None, state=None, cursor=cursor, limit=2
    )

    first = await service.list(listing(None, "ORG"))
    second = await service.list(listing(first.next_cursor, "ORG"))
    others = await service.list(
        ListTemplatesCommand(
            caller=STRANGER, scope="ORG", document_type=None, text=None, state=None, cursor=None, limit=50
        )
    )

    assert [t.name for t in first.items + list(second.items)] == [f"Org template {n}" for n in range(3)]
    assert others.items == []


async def test_allows_only_one_published_version_per_template_in_the_database(sessions) -> None:
    seed(sessions)

    with pytest.raises(IntegrityError, match="one_published"), sessions.begin() as session:
        session.execute(
            text(
                "INSERT INTO template_versions (id, template_id, number, state, definition, created_at) "
                "VALUES (gen_random_uuid(), :t, 2, 'PUBLISHED', '{}', now())"
            ),
            {"t": standard_template_id("standard-pan-card")},
        )
