"""Inputs of the template use cases. The caller (actor and account) always comes from the token (SEC-05)."""

from dataclasses import dataclass
from typing import Any
from uuid import UUID

from extraction.template.domain.models import VersionState


@dataclass(frozen=True, slots=True, kw_only=True)
class Caller:
    actor_id: str
    account_id: UUID | None
    is_platform_admin: bool

    @property
    def owner(self) -> str:
        """Who an Idempotency-Key belongs to: the account, or the staff member."""
        return str(self.account_id) if self.account_id else f"actor:{self.actor_id}"


@dataclass(frozen=True, slots=True, kw_only=True)
class CreateTemplateCommand:
    caller: Caller
    idempotency_key: str
    standard: bool  # only platform staff create standard templates
    name: str
    description: str | None
    document_type: str
    definition: dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class SaveDraftCommand:
    caller: Caller
    template_id: UUID
    number: int
    expected_row_version: int  # from If-Match
    definition: dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class VersionActionCommand:
    """New version, publish, restore, clone, archive: the template, the version and the caller's key."""

    caller: Caller
    idempotency_key: str
    template_id: UUID
    number: int | None = None
    override_reason: str | None = None  # publish without a passing test run


@dataclass(frozen=True, slots=True, kw_only=True)
class ListTemplatesCommand:
    caller: Caller
    scope: str | None
    document_type: str | None
    text: str | None
    state: VersionState | None
    cursor: str | None
    limit: int


@dataclass(frozen=True, slots=True, kw_only=True)
class ImportTemplateCommand:
    caller: Caller
    idempotency_key: str
    exported: dict[str, Any]


@dataclass(frozen=True, slots=True, kw_only=True)
class DocumentTypeCommand:
    caller: Caller
    standard: bool
    code: str
    name: str | None
    category: str | None
    classifier_hints: dict[str, Any] | None
    default_template_id: UUID | None
    active: bool | None
