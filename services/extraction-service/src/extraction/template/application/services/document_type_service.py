"""Use cases for document types (IDP-03 ch. 2.4 and 2.10): standard ones (staff) and an organisation's own."""

from collections.abc import Callable, Sequence
from uuid import UUID

from extraction.core.errors import PermissionDeniedError
from extraction.template.application.commands import Caller, DocumentTypeCommand
from extraction.template.domain.exceptions import DocumentTypeExistsError, DocumentTypeNotFoundError, TemplateStateError
from extraction.template.domain.models import DocumentType
from extraction.template.domain.ports import TemplateUnitOfWorkPort

_STANDARD_WRITE = "template:standard-write"

type UnitFactory = Callable[[], TemplateUnitOfWorkPort]


class DocumentTypeService:
    def __init__(self, units: UnitFactory) -> None:
        self._units = units

    async def list(self, caller: Caller) -> Sequence[DocumentType]:
        """Active types: standard ones and the caller's account's own."""
        async with self._units() as unit:
            return [t for t in await unit.document_types.list_visible(caller.account_id) if t.active]

    async def create(self, command: DocumentTypeCommand) -> DocumentType:
        account = self._owner(command)
        document_type = DocumentType(
            code=command.code,
            account_id=account,
            name=command.name or command.code,
            category=command.category or "OTHER",
            classifier_hints=command.classifier_hints or {},
            default_template_id=command.default_template_id,
            active=True if command.active is None else command.active,
        )
        async with self._units() as unit:
            existing = await unit.document_types.get(command.code, account)
            if existing is not None and existing.account_id == account:
                raise DocumentTypeExistsError(command.code)
            await unit.document_types.add(document_type)
            await unit.commit()
        return document_type

    async def update(self, command: DocumentTypeCommand) -> DocumentType:
        account = self._owner(command)
        async with self._units() as unit:
            found = await unit.document_types.get(command.code, account)
            if found is None or found.account_id != account:
                raise DocumentTypeNotFoundError(command.code)
            updated = DocumentType(
                code=found.code,
                account_id=found.account_id,
                name=command.name if command.name is not None else found.name,
                category=command.category if command.category is not None else found.category,
                classifier_hints=command.classifier_hints
                if command.classifier_hints is not None
                else found.classifier_hints,
                default_template_id=command.default_template_id or found.default_template_id,
                active=found.active if command.active is None else command.active,
            )
            await unit.document_types.save(updated)
            await unit.commit()
        return updated

    @staticmethod
    def _owner(command: DocumentTypeCommand) -> UUID | None:
        caller: Caller = command.caller
        if command.standard:
            if not caller.is_platform_admin:
                raise PermissionDeniedError(_STANDARD_WRITE)
            return None
        if caller.account_id is None:
            raise TemplateStateError("Your sign-in has no account yet; finish onboarding first.")
        return caller.account_id
