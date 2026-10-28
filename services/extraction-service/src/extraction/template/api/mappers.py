"""Requests -> commands and domain -> responses. The caller always comes from the token (SEC-05)."""

from typing import Any
from uuid import UUID

from extraction.core.security import Principal
from extraction.template.api.schemas import (
    AddSamplesRequest,
    CreateTemplateRequest,
    DocumentTypePatchRequest,
    DocumentTypeRequest,
    DocumentTypeResponse,
    SampleFileBody,
    SampleResponse,
    SuggestionsResponse,
    TemplateResponse,
    TemplateVersionResponse,
    TestRunResponse,
    VersionSummaryResponse,
)
from extraction.template.application.commands import (
    AddSamplesCommand,
    Caller,
    CreateTemplateCommand,
    DocumentTypeCommand,
)
from extraction.template.domain.designer import Sample, SampleFile, TestRun
from extraction.template.domain.models import DocumentType, Template, TemplateVersion
from extraction.template.domain.ports import Suggestion


def caller_of(principal: Principal) -> Caller:
    return Caller(
        actor_id=principal.actor_id, account_id=principal.tenant_id, is_platform_admin=principal.is_platform_admin
    )


def to_create_command(request: CreateTemplateRequest, key: str, principal: Principal) -> CreateTemplateCommand:
    return CreateTemplateCommand(
        caller=caller_of(principal),
        idempotency_key=key,
        standard=request.scope == "STANDARD",
        name=request.name,
        description=request.description,
        document_type=request.document_type,
        definition=request.definition,
    )


def to_document_type_command(
    request: DocumentTypeRequest | DocumentTypePatchRequest, principal: Principal, *, code: str | None = None
) -> DocumentTypeCommand:
    data: dict[str, Any] = request.model_dump()
    return DocumentTypeCommand(
        caller=caller_of(principal),
        standard=request.scope == "STANDARD",
        code=code or str(data["code"]),
        name=data.get("name"),
        category=data.get("category"),
        classifier_hints=data.get("classifier_hints"),
        default_template_id=data.get("default_template_id"),
        active=data.get("active"),
    )


def to_template_response(template: Template) -> TemplateResponse:
    return TemplateResponse(
        id=template.id,
        scope="STANDARD" if template.is_standard else "ORG",
        document_type=template.document_type_code,
        name=template.name,
        description=template.description,
        status=template.status,
        current_version=template.current_version,
        created_by=template.created_by,
        created_at=template.created_at,
        updated_at=template.updated_at,
        versions=[
            VersionSummaryResponse(
                number=v.number,
                state=v.state,
                published_at=v.published_at,
                published_by=v.published_by,
                publish_reason=v.publish_reason,
            )
            for v in template.versions
        ],
    )


def to_version_response(template_id: UUID, version: TemplateVersion) -> TemplateVersionResponse:
    return TemplateVersionResponse(
        template_id=template_id,
        number=version.number,
        state=version.state,
        definition=version.definition,
        accuracy=version.accuracy,
        published_at=version.published_at,
    )


def to_document_type_response(document_type: DocumentType) -> DocumentTypeResponse:
    return DocumentTypeResponse(
        code=document_type.code,
        scope="STANDARD" if document_type.account_id is None else "ORG",
        name=document_type.name,
        category=document_type.category,
        classifier_hints=document_type.classifier_hints,
        default_template_id=document_type.default_template_id,
        active=document_type.active,
    )


def etag(template: Template) -> str:
    return f'"{template.row_version}"'


def parse_if_match(value: str) -> int:
    """``If-Match: "7"`` -> 7. A malformed header matches nothing, so the save is refused (412)."""
    try:
        return int(value.strip().removeprefix("W/").strip('"'))
    except ValueError:
        return -1


def to_add_samples_command(
    request: AddSamplesRequest, principal: Principal, template_id: UUID, version: int
) -> AddSamplesCommand:
    return AddSamplesCommand(
        caller=caller_of(principal),
        template_id=template_id,
        version=version,
        samples=tuple(
            (tuple(SampleFile(bucket=f.bucket, key=f.key) for f in sample.files), dict(sample.expected))
            for sample in request.samples
        ),
    )


def to_sample_response(sample: Sample) -> SampleResponse:
    return SampleResponse(
        id=sample.id,
        files=[SampleFileBody(bucket=f.bucket, key=f.key) for f in sample.files],
        expected=sample.expected,
        status=sample.status.value,
        page_key=sample.page_key,
        error_code=sample.error_code,
        created_at=sample.created_at,
    )


def to_suggestions_response(suggestion: Suggestion) -> SuggestionsResponse:
    return SuggestionsResponse(
        fields=list(suggestion.fields),
        tables=list(suggestion.tables),
        model=suggestion.model_id,
        prompt_version=suggestion.prompt_version,
    )


def to_test_run_response(run: TestRun) -> TestRunResponse:
    return TestRunResponse(
        id=run.id,
        status=run.status.value,
        samples=len(run.samples),
        report=run.report,
        error_code=run.error_code,
        created_at=run.created_at,
        finished_at=run.finished_at,
    )
