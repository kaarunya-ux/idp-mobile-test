"""Use cases for templates (IDP-03 ch. 2.9 and 2.10). The real authorisation check is here (SEC-03).

Who may do what:

=======================  ====================================================================================
Template                 Rule
=======================  ====================================================================================
standard (no account)    everyone reads it; only platform staff change it
an account's own         only that account sees or changes it; anyone else gets 404, as if it didn't exist
=======================  ====================================================================================

Writes that aren't idempotent (create, new version, publish, restore, clone, import) take an ``Idempotency-Key``:
the same key again returns the first result; the same key with a different request is a 409.
"""

import base64
import binascii
import hashlib
import json
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from extraction.core.clock import Clock
from extraction.core.errors import PermissionDeniedError
from extraction.core.ids import new_id
from extraction.pipeline.domain.evaluation import report_passes
from extraction.template.application.commands import (
    Caller,
    CreateTemplateCommand,
    ImportTemplateCommand,
    ListTemplatesCommand,
    SaveDraftCommand,
    VersionActionCommand,
)
from extraction.template.domain.definition_json import check_definition
from extraction.template.domain.designer import definition_hash
from extraction.template.domain.exceptions import (
    DefinitionInvalidError,
    DocumentTypeNotFoundError,
    IdempotencyKeyReusedError,
    TemplateNotFoundError,
    TemplateStateError,
)
from extraction.template.domain.models import Template, TemplateVersion
from extraction.template.domain.ports import StoredResponse, TemplateQuery, TemplateUnitOfWorkPort

EXPORT_FORMAT = "idp-template-export.v1"
_STANDARD_WRITE = "template:standard-write"

type UnitFactory = Callable[[], TemplateUnitOfWorkPort]


@dataclass(frozen=True, slots=True, kw_only=True)
class TemplatePage:
    items: Sequence[Template]
    next_cursor: str | None


class TemplateService:
    def __init__(self, units: UnitFactory, clock: Clock, *, publish_min_accuracy: float = 0.9) -> None:
        self._units = units
        self._clock = clock
        self._publish_min_accuracy = publish_min_accuracy  # IDP-03 ch. 2.9: every required field at 90 %

    # --- Reads --------------------------------------------------------------------------------------------

    async def get(self, caller: Caller, template_id: UUID) -> Template:
        async with self._units() as unit:
            return visible_template(await unit.templates.get(template_id), caller)

    async def get_version(self, caller: Caller, template_id: UUID, number: int) -> tuple[Template, TemplateVersion]:
        template = await self.get(caller, template_id)
        return template, template.version(number)

    async def list(self, command: ListTemplatesCommand) -> TemplatePage:
        query = TemplateQuery(
            account_id=command.caller.account_id,
            scope=command.scope,
            document_type=command.document_type,
            text=command.text,
            state=command.state,
            after=_decode_cursor(command.cursor),
            limit=command.limit,
        )
        async with self._units() as unit:
            found = list(await unit.templates.find(query))
        more = len(found) > command.limit
        items = found[: command.limit]
        return TemplatePage(items=items, next_cursor=_encode_cursor(items[-1]) if more and items else None)

    async def export(self, caller: Caller, template_id: UUID) -> dict[str, Any]:
        """The template's published (or newest) definition, portable to another account or environment."""
        template = await self.get(caller, template_id)
        current = template.current_version
        version = template.version(current) if current is not None else max(template.versions, key=lambda v: v.number)
        return {
            "format": EXPORT_FORMAT,
            "name": template.name,
            "description": template.description,
            "documentType": template.document_type_code,
            "definition": version.definition,
        }

    # --- Writes -------------------------------------------------------------------------------------------

    async def create(self, command: CreateTemplateCommand) -> Template:
        caller = command.caller
        if command.standard and not caller.is_platform_admin:
            raise PermissionDeniedError(_STANDARD_WRITE)
        account = None if command.standard else account_of(caller)
        definition = check_definition(command.definition, document_type=command.document_type)

        async def action(unit: TemplateUnitOfWorkPort) -> Template:
            if await unit.document_types.get(command.document_type, account) is None:
                raise DocumentTypeNotFoundError(command.document_type)
            template = Template.create(
                id=new_id(),
                account_id=account,
                document_type_code=command.document_type,
                name=command.name,
                description=command.description,
                definition=definition,
                actor=caller.actor_id,
                now=self._clock.now(),
            )
            await unit.templates.add(template)
            return template

        request = {"op": "create", "standard": command.standard, "name": command.name, "type": command.document_type}
        return await self._once(caller, command.idempotency_key, request | {"definition": definition}, action)

    async def save_draft(self, command: SaveDraftCommand) -> Template:
        async with self._units() as unit:
            template = writable_template(await unit.templates.get(command.template_id), command.caller)
            definition = check_definition(command.definition, document_type=template.document_type_code)
            template.save_draft(command.number, definition, self._clock.now())
            await unit.templates.save(template, expected_row_version=command.expected_row_version)
            await unit.commit()
        return template

    async def new_version(self, command: VersionActionCommand) -> Template:
        return await self._change(command, "new-version", lambda t: t.new_version(self._clock.now()))

    async def publish(self, command: VersionActionCommand) -> Template:
        """Publishing needs a passing test run on this exact definition from the last 24 hours, or a reason."""
        number = _number(command)
        async with self._units() as unit:
            template = visible_template(await unit.templates.get(command.template_id), command.caller)
            run = await unit.test_runs.latest_completed(template.id, number)
        definition = template.version(number).definition
        passed = (
            run is not None
            and run.definition_hash == definition_hash(definition)
            and run.report is not None
            and report_passes(run.report, min_accuracy=self._publish_min_accuracy)
        )
        tested_at = run.finished_at if passed and run is not None else None
        return await self._change(
            command,
            "publish",
            lambda t: t.publish(
                number,
                actor=command.caller.actor_id,
                now=self._clock.now(),
                tested_at=tested_at,
                override_reason=command.override_reason,
            ),
        )

    async def archive_account(self, account_id: UUID) -> int:
        """``AccountDeleted``: archive every template of the account and switch off its document types."""
        archived = 0
        async with self._units() as unit:
            query = TemplateQuery(
                account_id=account_id, scope="ORG", document_type=None, text=None, state=None, after=None, limit=10_000
            )
            for template in await unit.templates.find(query):
                before = template.row_version
                template.archive(self._clock.now())
                if template.row_version != before:
                    await unit.templates.save(template, expected_row_version=before)
                    archived += 1
            for document_type in await unit.document_types.list_visible(account_id):
                if document_type.account_id == account_id and document_type.active:
                    document_type.active = False
                    await unit.document_types.save(document_type)
            await unit.commit()
        return archived

    async def restore(self, command: VersionActionCommand) -> Template:
        number = _number(command)
        return await self._change(
            command, "restore", lambda t: t.restore(number, actor=command.caller.actor_id, now=self._clock.now())
        )

    async def archive(self, command: VersionActionCommand) -> Template:
        """Idempotent by nature (DELETE): archiving twice is the same as once."""
        async with self._units() as unit:
            template = writable_template(await unit.templates.get(command.template_id), command.caller)
            before = template.row_version
            template.archive(self._clock.now())
            await unit.templates.save(template, expected_row_version=before)
            await unit.commit()
        return template

    async def clone(self, command: VersionActionCommand) -> Template:
        """Copy a template the caller can see into the caller's account, as a new draft (IDP-03 ch. 2.10)."""
        account = account_of(command.caller)

        async def action(unit: TemplateUnitOfWorkPort) -> Template:
            source = visible_template(await unit.templates.get(command.template_id), command.caller)
            copy = source.clone(id=new_id(), account_id=account, actor=command.caller.actor_id, now=self._clock.now())
            await unit.templates.add(copy)
            return copy

        request = {"op": "clone", "template": str(command.template_id)}
        return await self._once(command.caller, command.idempotency_key, request, action)

    async def import_(self, command: ImportTemplateCommand) -> Template:
        exported = command.exported
        if exported.get("format") != EXPORT_FORMAT:
            raise DefinitionInvalidError([("format", f"must be {EXPORT_FORMAT}")])
        return await self.create(
            CreateTemplateCommand(
                caller=command.caller,
                idempotency_key=command.idempotency_key,
                standard=False,
                name=str(exported.get("name") or "Imported template")[:120],
                description=exported.get("description"),
                document_type=str(exported.get("documentType", "")),
                definition=exported.get("definition") or {},
            )
        )

    # --- Helpers ------------------------------------------------------------------------------------------

    async def _change(self, command: VersionActionCommand, op: str, change: Callable[[Template], object]) -> Template:
        async def action(unit: TemplateUnitOfWorkPort) -> Template:
            template = writable_template(await unit.templates.get(command.template_id), command.caller)
            before = template.row_version
            change(template)
            await unit.templates.save(template, expected_row_version=before)
            return template

        request = {"op": op, "template": str(command.template_id), "n": command.number, "why": command.override_reason}
        return await self._once(command.caller, command.idempotency_key, request, action)

    async def _once(
        self,
        caller: Caller,
        key: str,
        request: dict[str, Any],
        action: Callable[[TemplateUnitOfWorkPort], Awaitable[Template]],
    ) -> Template:
        """Run ``action`` once per Idempotency-Key, in one transaction with the key's record (RTR-08, TX-01)."""
        digest = hashlib.sha256(json.dumps(request, sort_keys=True, default=str).encode()).hexdigest()
        async with self._units() as unit:
            stored = await unit.idempotency.get(caller.owner, key)
            if stored is not None:
                if stored.request_hash != digest:
                    raise IdempotencyKeyReusedError
                return visible_template(await unit.templates.get(UUID(stored.body["templateId"])), caller)
            template = await action(unit)
            await unit.idempotency.put(
                caller.owner, key, StoredResponse(request_hash=digest, body={"templateId": str(template.id)})
            )
            await unit.commit()
        return template


def visible_template(template: Template | None, caller: Caller) -> Template:
    if template is None or not (template.is_standard or template.account_id == caller.account_id):
        raise TemplateNotFoundError  # another account's template looks absent (SEC-05)
    return template


def writable_template(template: Template | None, caller: Caller) -> Template:
    found = visible_template(template, caller)
    if found.is_standard and not caller.is_platform_admin:
        raise PermissionDeniedError(_STANDARD_WRITE)
    return found


def account_of(caller: Caller) -> UUID:
    if caller.account_id is None:
        raise TemplateStateError("Your sign-in has no account yet; finish onboarding first.")
    return caller.account_id


def _number(command: VersionActionCommand) -> int:
    if command.number is None:
        raise TemplateNotFoundError
    return command.number


def _encode_cursor(template: Template) -> str:
    return base64.urlsafe_b64encode(json.dumps([template.name, str(template.id)]).encode()).decode()


def _decode_cursor(cursor: str | None) -> tuple[str, UUID] | None:
    if not cursor:
        return None
    try:
        name, template_id = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        return str(name), UUID(str(template_id))
    except (binascii.Error, ValueError, TypeError):
        raise DefinitionInvalidError([("cursor", "not a cursor this API returned")]) from None
