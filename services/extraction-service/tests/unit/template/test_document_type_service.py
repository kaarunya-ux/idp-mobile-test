"""Document types: staff manage the standard ones; an organisation adds and changes its own."""

import pytest

from extraction.core.errors import PermissionDeniedError
from extraction.template.application.commands import DocumentTypeCommand
from extraction.template.application.services.document_type_service import DocumentTypeService
from extraction.template.domain.exceptions import DocumentTypeExistsError, DocumentTypeNotFoundError, TemplateStateError
from support.template_fakes import ADMIN, ORG, STRANGER, Store, Unit, standard_types


@pytest.fixture
def service() -> DocumentTypeService:
    store = Store(types=standard_types())
    return DocumentTypeService(lambda: Unit(store))


def _command(caller=ORG, *, code="INVOICE", standard=False, **changes) -> DocumentTypeCommand:
    values = {
        "name": "Invoice",
        "category": "FINANCE",
        "classifier_hints": None,
        "default_template_id": None,
        "active": None,
    }
    values.update(changes)
    return DocumentTypeCommand(caller=caller, standard=standard, code=code, **values)


async def test_adds_an_organisation_type_that_only_that_organisation_sees(service) -> None:
    await service.create(_command())

    mine = {t.code for t in await service.list(ORG)}
    theirs = {t.code for t in await service.list(STRANGER)}

    assert "INVOICE" in mine
    assert "INVOICE" not in theirs


async def test_refuses_a_second_type_with_the_same_code_in_one_organisation(service) -> None:
    await service.create(_command())

    with pytest.raises(DocumentTypeExistsError):
        await service.create(_command())


async def test_changes_and_deactivates_an_organisation_type(service) -> None:
    await service.create(_command())

    updated = await service.update(_command(name="Supplier invoice", category=None, active=False))

    assert (updated.name, updated.category, updated.active) == ("Supplier invoice", "FINANCE", False)
    assert "INVOICE" not in {t.code for t in await service.list(ORG)}  # inactive types aren't listed


async def test_lets_only_platform_staff_change_standard_types(service) -> None:
    with pytest.raises(PermissionDeniedError):
        await service.update(_command(code="PAN_CARD", standard=True))
    with pytest.raises(DocumentTypeNotFoundError):
        await service.update(_command(code="PAN_CARD"))  # not the organisation's own
    updated = await service.update(_command(ADMIN, code="PAN_CARD", standard=True, name="PAN card (India)"))

    assert updated.name == "PAN card (India)"


async def test_needs_an_account_for_an_organisation_type(service) -> None:
    with pytest.raises(TemplateStateError, match="no account"):
        await service.create(_command(ADMIN))
