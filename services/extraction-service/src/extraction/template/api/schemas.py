"""Request and response bodies of the templates API (IDP-03 ch. 2.10). ``*Request``/``*Response`` stay in ``api``
(OBJ-01); JSON is camelCase, as in the Java services (MSV-05)."""

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field
from pydantic.alias_generators import to_camel


class _Body(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="forbid", frozen=True)


class CreateTemplateRequest(_Body):
    name: str = Field(min_length=1, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    document_type: str = Field(min_length=2, max_length=64)
    definition: dict[str, Any]  # schema v1 (IDP-03 ch. 2.5), checked by the service
    scope: str = Field(default="ORG", pattern="^(ORG|STANDARD)$")


class SaveDraftRequest(_Body):
    definition: dict[str, Any]


class PublishRequest(_Body):
    override_reason: str | None = Field(default=None, max_length=500)  # needed until test runs exist


class ImportTemplateRequest(_Body):
    format: str
    name: str | None = Field(default=None, max_length=120)
    description: str | None = Field(default=None, max_length=2000)
    document_type: str
    definition: dict[str, Any]


class DocumentTypeRequest(_Body):
    code: str = Field(min_length=2, max_length=64)
    name: str = Field(min_length=1, max_length=120)
    category: str = Field(default="OTHER", max_length=32)
    classifier_hints: dict[str, Any] = Field(default_factory=dict)
    default_template_id: UUID | None = None
    scope: str = Field(default="ORG", pattern="^(ORG|STANDARD)$")


class DocumentTypePatchRequest(_Body):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    category: str | None = Field(default=None, max_length=32)
    classifier_hints: dict[str, Any] | None = None
    default_template_id: UUID | None = None
    active: bool | None = None
    scope: str = Field(default="ORG", pattern="^(ORG|STANDARD)$")


class VersionSummaryResponse(_Body):
    number: int
    state: str
    published_at: datetime | None
    published_by: str | None
    publish_reason: str | None


class TemplateResponse(_Body):
    id: UUID
    scope: str
    document_type: str
    name: str
    description: str | None
    status: str
    current_version: int | None
    created_by: str
    created_at: datetime
    updated_at: datetime
    versions: list[VersionSummaryResponse]


class TemplateVersionResponse(_Body):
    template_id: UUID
    number: int
    state: str
    definition: dict[str, Any]
    accuracy: dict[str, Any] | None
    published_at: datetime | None


class TemplatePageResponse(_Body):
    items: list[TemplateResponse]
    next_cursor: str | None
    has_more: bool


class DocumentTypeResponse(_Body):
    code: str
    scope: str
    name: str
    category: str
    classifier_hints: dict[str, Any]
    default_template_id: UUID | None
    active: bool


class DocumentTypeListResponse(_Body):
    items: list[DocumentTypeResponse]
