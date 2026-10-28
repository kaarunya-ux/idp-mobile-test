"""Templates, their versions, and document types (IDP-03 ch. 2.4 and 2.9).

A template has numbered versions. Only a DRAFT changes; publishing makes it the one PUBLISHED version and retires
the previous one; a RETIRED version can be restored. Templates are archived, never deleted: results refer to them.
A template with no account is a standard one, which only platform staff change.
"""

import copy
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Any
from uuid import UUID

from extraction.template.domain.exceptions import (
    DefinitionInvalidError,
    TemplateNotDraftError,
    TemplateNotFoundError,
    TemplateNotTestedError,
    TemplateStateError,
)

TEST_FRESHNESS = timedelta(hours=24)  # IDP-03 ch. 2.9: a passing test run in the last 24 hours
_CODE = re.compile(r"^[A-Z][A-Z0-9_]{1,63}$")
NAME_MAX = 120
CATEGORIES = frozenset({"ID", "FINANCE", "TAX", "LEGAL", "HEALTH", "EDUCATION", "OTHER"})


class VersionState(StrEnum):
    DRAFT = "DRAFT"
    TESTING = "TESTING"
    PUBLISHED = "PUBLISHED"
    RETIRED = "RETIRED"


class TemplateStatus(StrEnum):
    ACTIVE = "ACTIVE"
    ARCHIVED = "ARCHIVED"


@dataclass(slots=True, kw_only=True)
class TemplateVersion:
    number: int
    state: VersionState
    definition: dict[str, Any]  # schema v1 JSON, already checked by ``definition_json``
    created_at: datetime
    published_at: datetime | None = None
    published_by: str | None = None
    publish_reason: str | None = None  # the override reason, when published without a passing test
    accuracy: dict[str, Any] | None = None  # from the last test run


@dataclass(slots=True, kw_only=True)
class Template:
    """Mutable on purpose (DOM-01). ``row_version`` grows with every change: the ETag for optimistic locking."""

    id: UUID
    account_id: UUID | None  # None: standard
    document_type_code: str
    name: str
    description: str | None
    status: TemplateStatus
    created_by: str
    created_at: datetime
    updated_at: datetime
    row_version: int = 1
    versions: list[TemplateVersion] = field(default_factory=list)

    @classmethod
    def create(  # noqa: PLR0913
        cls,
        *,
        id: UUID,
        account_id: UUID | None,
        document_type_code: str,
        name: str,
        description: str | None,
        definition: dict[str, Any],
        actor: str,
        now: datetime,
    ) -> "Template":
        _check_document_type(definition, document_type_code)
        draft = TemplateVersion(number=1, state=VersionState.DRAFT, definition=definition, created_at=now)
        return cls(
            id=id,
            account_id=account_id,
            document_type_code=document_type_code,
            name=name,
            description=description,
            status=TemplateStatus.ACTIVE,
            created_by=actor,
            created_at=now,
            updated_at=now,
            versions=[draft],
        )

    @property
    def is_standard(self) -> bool:
        return self.account_id is None

    @property
    def current_version(self) -> int | None:
        """The PUBLISHED version's number, if any."""
        return next((v.number for v in self.versions if v.state is VersionState.PUBLISHED), None)

    def version(self, number: int) -> TemplateVersion:
        found = next((v for v in self.versions if v.number == number), None)
        if found is None:
            raise TemplateNotFoundError
        return found

    def save_draft(self, number: int, definition: dict[str, Any], now: datetime) -> None:
        version = self.version(number)
        if version.state is not VersionState.DRAFT:
            raise TemplateNotDraftError(number)
        _check_document_type(definition, self.document_type_code)
        version.definition = definition
        self._touch(now)

    def new_version(self, now: datetime) -> TemplateVersion:
        """A new DRAFT, copied from the published version (or the newest one when none is published)."""
        self._ensure_active()
        if any(v.state is VersionState.DRAFT for v in self.versions):
            raise TemplateStateError("There is already a draft version; change that one.")
        current = self.current_version
        source = self.version(current) if current is not None else max(self.versions, key=lambda v: v.number)
        draft = TemplateVersion(
            number=max(v.number for v in self.versions) + 1,
            state=VersionState.DRAFT,
            definition=copy.deepcopy(source.definition),
            created_at=now,
        )
        self.versions.append(draft)
        self._touch(now)
        return draft

    def publish(
        self, number: int, *, actor: str, now: datetime, tested_at: datetime | None, override_reason: str | None
    ) -> None:
        self._ensure_active()
        version = self.version(number)
        if version.state not in (VersionState.DRAFT, VersionState.TESTING):
            raise TemplateStateError(
                f"Version {number} is {version.state}; only a draft or tested version is published."
            )
        tested = tested_at is not None and now - tested_at <= TEST_FRESHNESS
        if not tested and not (override_reason and override_reason.strip()):
            raise TemplateNotTestedError
        self._retire_published()
        version.state, version.published_at, version.published_by = VersionState.PUBLISHED, now, actor
        version.publish_reason = None if tested else (override_reason or "").strip()
        self._touch(now)

    def restore(self, number: int, *, actor: str, now: datetime) -> None:
        """Roll back: a RETIRED version becomes the published one again."""
        self._ensure_active()
        version = self.version(number)
        if version.state is not VersionState.RETIRED:
            raise TemplateStateError(f"Version {number} is {version.state}; only a retired version is restored.")
        self._retire_published()
        version.state, version.published_at, version.published_by = VersionState.PUBLISHED, now, actor
        self._touch(now)

    def archive(self, now: datetime) -> None:
        if self.status is not TemplateStatus.ARCHIVED:
            self.status = TemplateStatus.ARCHIVED
            self._touch(now)

    def clone(self, *, id: UUID, account_id: UUID, actor: str, now: datetime) -> "Template":
        """A copy for an organisation, starting as a DRAFT of the published (or newest) definition."""
        current = self.current_version
        source = self.version(current) if current is not None else max(self.versions, key=lambda v: v.number)
        return Template.create(
            id=id,
            account_id=account_id,
            document_type_code=self.document_type_code,
            name=self.name,
            description=self.description,
            definition=copy.deepcopy(source.definition),
            actor=actor,
            now=now,
        )

    def _retire_published(self) -> None:
        for version in self.versions:
            if version.state is VersionState.PUBLISHED:
                version.state = VersionState.RETIRED

    def _ensure_active(self) -> None:
        if self.status is TemplateStatus.ARCHIVED:
            raise TemplateStateError("The template is archived.")

    def _touch(self, now: datetime) -> None:
        self.updated_at, self.row_version = now, self.row_version + 1


@dataclass(slots=True, kw_only=True)
class DocumentType:
    """``PAN_CARD``, ``INVOICE`` and so on. Standard ones (no account) are staff-managed; organisations add their
    own."""

    code: str
    account_id: UUID | None
    name: str
    category: str
    classifier_hints: dict[str, Any]
    default_template_id: UUID | None
    active: bool

    def __post_init__(self) -> None:
        problems = []
        if not _CODE.match(self.code):
            problems.append(("code", "upper-case letters, digits and _, starting with a letter, 2 to 64 long"))
        if self.category not in CATEGORIES:
            problems.append(("category", f"one of {', '.join(sorted(CATEGORIES))}"))
        if not 1 <= len(self.name.strip()) <= NAME_MAX:
            problems.append(("name", f"1 to {NAME_MAX} characters"))
        if problems:
            raise DefinitionInvalidError(problems)


def _check_document_type(definition: dict[str, Any], code: str) -> None:
    if definition.get("documentType") != code:
        raise DefinitionInvalidError([("documentType", f"must be the template's document type, {code}")])
