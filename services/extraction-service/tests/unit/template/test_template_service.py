"""Template use cases: who sees and changes what, idempotent writes, paging, export and import."""

import pytest

from extraction.core.errors import PermissionDeniedError
from extraction.pipeline.domain.standard_templates import PAN_CARD
from extraction.template.application.commands import (
    CreateTemplateCommand,
    ImportTemplateCommand,
    ListTemplatesCommand,
    SaveDraftCommand,
    VersionActionCommand,
)
from extraction.template.application.services.template_service import TemplateService
from extraction.template.domain.definition_json import to_json
from extraction.template.domain.exceptions import (
    DocumentTypeNotFoundError,
    IdempotencyKeyReusedError,
    TemplateNotFoundError,
    VersionMismatchError,
)
from extraction.template.domain.models import VersionState
from support.template_fakes import ADMIN, ORG, STRANGER, Clock, Store, Unit, standard_types


@pytest.fixture
def store() -> Store:
    return Store(types=standard_types())


@pytest.fixture
def service(store: Store) -> TemplateService:
    return TemplateService(lambda: Unit(store), Clock())


def _create(caller=ORG, key="key-00001", *, standard=False, name="My PAN", document_type="PAN_CARD", definition=None):
    return CreateTemplateCommand(
        caller=caller,
        idempotency_key=key,
        standard=standard,
        name=name,
        description=None,
        document_type=document_type,
        definition=definition or to_json(PAN_CARD),
    )


async def test_creates_an_organisation_template_as_a_draft(service: TemplateService) -> None:
    template = await service.create(_create())

    assert (template.account_id, template.versions[0].state) == (ORG.account_id, VersionState.DRAFT)


async def test_returns_the_first_result_when_the_same_key_is_sent_again(service, store) -> None:
    first = await service.create(_create())
    again = await service.create(_create())

    assert again.id == first.id
    assert len(store.templates) == 1


async def test_refuses_the_same_key_for_a_different_request(service) -> None:
    await service.create(_create())

    with pytest.raises(IdempotencyKeyReusedError):
        await service.create(_create(name="Another name"))


async def test_lets_only_platform_staff_create_or_change_standard_templates(service) -> None:
    with pytest.raises(PermissionDeniedError):
        await service.create(_create(standard=True))
    standard = await service.create(_create(ADMIN, standard=True))

    with pytest.raises(PermissionDeniedError):
        await service.new_version(
            VersionActionCommand(caller=ORG, idempotency_key="key-00002", template_id=standard.id)
        )
    assert (await service.get(ORG, standard.id)).id == standard.id  # but everyone reads it


async def test_hides_another_accounts_template_as_if_it_did_not_exist(service) -> None:
    mine = await service.create(_create())

    with pytest.raises(TemplateNotFoundError):
        await service.get(STRANGER, mine.id)
    with pytest.raises(TemplateNotFoundError):
        await service.publish(
            VersionActionCommand(
                caller=STRANGER, idempotency_key="key-00003", template_id=mine.id, number=1, override_reason="x"
            )
        )


async def test_refuses_an_unknown_document_type(service) -> None:
    definition = {**to_json(PAN_CARD), "documentType": "VOTER_ID"}

    with pytest.raises(DocumentTypeNotFoundError):
        await service.create(_create(document_type="VOTER_ID", definition=definition))


async def test_publishes_restores_and_archives_through_the_lifecycle(service, store) -> None:
    template = await service.create(_create())
    action = lambda key, n=None, why=None: VersionActionCommand(  # noqa: E731
        caller=ORG, idempotency_key=key, template_id=template.id, number=n, override_reason=why
    )

    await service.publish(action("key-p1", 1, "checked"))
    await service.new_version(action("key-n2"))
    await service.publish(action("key-p2", 2, "checked"))
    restored = await service.restore(action("key-r1", 1))
    archived = await service.archive(action("-"))

    assert [(v.number, v.state) for v in restored.versions] == [(1, VersionState.PUBLISHED), (2, VersionState.RETIRED)]
    assert archived.status == "ARCHIVED"


async def test_saves_a_draft_only_against_the_version_the_caller_saw(service) -> None:
    template = await service.create(_create())
    save = lambda version: SaveDraftCommand(  # noqa: E731
        caller=ORG, template_id=template.id, number=1, expected_row_version=version, definition=to_json(PAN_CARD)
    )

    saved = await service.save_draft(save(template.row_version))

    assert saved.row_version == template.row_version + 1
    with pytest.raises(VersionMismatchError):
        await service.save_draft(save(template.row_version))  # stale ETag


async def test_pages_through_templates_with_a_cursor(service) -> None:
    for n in range(3):
        await service.create(_create(key=f"key-page-{n}", name=f"T{n}"))
    listing = lambda cursor: ListTemplatesCommand(  # noqa: E731
        caller=ORG, scope=None, document_type=None, text=None, state=None, cursor=cursor, limit=2
    )

    first = await service.list(listing(None))
    second = await service.list(listing(first.next_cursor))

    assert [t.name for t in first.items] == ["T0", "T1"]
    assert ([t.name for t in second.items], second.next_cursor) == (["T2"], None)


async def test_clones_a_standard_template_and_round_trips_it_through_export_and_import(service) -> None:
    standard = await service.create(_create(ADMIN, standard=True, name="PAN standard"))
    clone = await service.clone(VersionActionCommand(caller=ORG, idempotency_key="key-clone", template_id=standard.id))
    exported = await service.export(ORG, clone.id)

    imported = await service.import_(ImportTemplateCommand(caller=ORG, idempotency_key="key-import", exported=exported))

    assert (clone.account_id, imported.account_id) == (ORG.account_id, ORG.account_id)
    assert imported.versions[0].definition == to_json(PAN_CARD)
