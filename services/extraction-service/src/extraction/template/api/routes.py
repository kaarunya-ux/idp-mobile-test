"""The templates and document types API (IDP-03 ch. 2.10), reached through the Java gateway.

Every route needs a token (SEC-02) and states its permission (SEC-03); the services make the real check. Bodies are
one service call (RTR-02). Not here yet: suggestions (needs ai-gateway, build step E5), samples, test runs and the
mobile bundle (step E6).
"""

from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, Query, Request, Response, status

from extraction.core import permissions as perm
from extraction.core.security import Principal, require_permission
from extraction.template.api import mappers
from extraction.template.api.schemas import (
    CreateTemplateRequest,
    DocumentTypeListResponse,
    DocumentTypePatchRequest,
    DocumentTypeRequest,
    DocumentTypeResponse,
    ImportTemplateRequest,
    PublishRequest,
    SaveDraftRequest,
    TemplatePageResponse,
    TemplateResponse,
    TemplateVersionResponse,
)
from extraction.template.application.commands import (
    ImportTemplateCommand,
    ListTemplatesCommand,
    SaveDraftCommand,
    VersionActionCommand,
)
from extraction.template.application.services.document_type_service import DocumentTypeService
from extraction.template.application.services.template_service import TemplateService
from extraction.template.domain.models import VersionState

templates = APIRouter(prefix="/api/v1/templates", tags=["Templates"])
document_types = APIRouter(prefix="/api/v1/document-types", tags=["Document types"])

type IdempotencyKey = Annotated[str, Header(alias="Idempotency-Key", min_length=8, max_length=128)]
type Reader = Annotated[Principal, Depends(require_permission(perm.TEMPLATE_READ))]
type Writer = Annotated[Principal, Depends(require_permission(perm.TEMPLATE_WRITE))]
type Publisher = Annotated[Principal, Depends(require_permission(perm.TEMPLATE_PUBLISH))]
_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": "Problem details (RFC 9457)"} for code in (400, 401, 403, 404, 409, 412, 422, 503)
}


def provide_template_service(request: Request) -> TemplateService:
    service: TemplateService = request.app.state.template_service  # built once in create_app (DI-03)
    return service


def provide_document_type_service(request: Request) -> DocumentTypeService:
    service: DocumentTypeService = request.app.state.document_type_service
    return service


type Templates = Annotated[TemplateService, Depends(provide_template_service)]
type DocumentTypes = Annotated[DocumentTypeService, Depends(provide_document_type_service)]


# --- Templates ------------------------------------------------------------------------------------------------


@templates.get("", summary="List templates", response_model=TemplatePageResponse, responses=_ERRORS)
async def list_templates(  # noqa: PLR0913
    *,
    principal: Reader,
    service: Templates,
    scope: Annotated[str | None, Query(pattern="^(STANDARD|ORG)$")] = None,
    document_type: Annotated[str | None, Query(alias="documentType", max_length=64)] = None,
    q: Annotated[str | None, Query(max_length=120)] = None,
    state: VersionState | None = None,
    cursor: Annotated[str | None, Query(max_length=512)] = None,
    limit: Annotated[int, Query(ge=1, le=200)] = 50,
) -> TemplatePageResponse:
    command = ListTemplatesCommand(
        caller=mappers.caller_of(principal),
        scope=scope,
        document_type=document_type,
        text=q,
        state=state,
        cursor=cursor,
        limit=limit,
    )
    page = await service.list(command)
    items = [mappers.to_template_response(t) for t in page.items]
    return TemplatePageResponse(items=items, next_cursor=page.next_cursor, has_more=page.next_cursor is not None)


@templates.post(
    "",
    summary="Create a template",
    response_model=TemplateResponse,
    status_code=status.HTTP_201_CREATED,
    responses=_ERRORS,
)
async def create_template(
    request: CreateTemplateRequest, key: IdempotencyKey, principal: Writer, service: Templates, response: Response
) -> TemplateResponse:
    template = await service.create(mappers.to_create_command(request, key, principal))
    response.headers["Location"], response.headers["ETag"] = f"/api/v1/templates/{template.id}", mappers.etag(template)
    return mappers.to_template_response(template)


@templates.post(
    "/import", summary="Import a template", response_model=TemplateResponse, status_code=201, responses=_ERRORS
)
async def import_template(
    request: ImportTemplateRequest, key: IdempotencyKey, principal: Writer, service: Templates
) -> TemplateResponse:
    command = ImportTemplateCommand(
        caller=mappers.caller_of(principal), idempotency_key=key, exported=request.model_dump(by_alias=True)
    )
    return mappers.to_template_response(await service.import_(command))


@templates.get("/{template_id}", summary="Get a template", response_model=TemplateResponse, responses=_ERRORS)
async def get_template(
    template_id: UUID, principal: Reader, service: Templates, response: Response
) -> TemplateResponse:
    template = await service.get(mappers.caller_of(principal), template_id)
    response.headers["ETag"] = mappers.etag(template)
    return mappers.to_template_response(template)


@templates.get(
    "/{template_id}/versions/{number}",
    summary="Get a template version",
    response_model=TemplateVersionResponse,
    responses=_ERRORS,
)
async def get_version(template_id: UUID, number: int, principal: Reader, service: Templates) -> TemplateVersionResponse:
    template, version = await service.get_version(mappers.caller_of(principal), template_id, number)
    return mappers.to_version_response(template.id, version)


@templates.put(
    "/{template_id}/versions/{number}",
    summary="Save a draft definition",
    response_model=TemplateResponse,
    responses=_ERRORS,
)
async def save_draft(
    template_id: UUID,
    number: int,
    request: SaveDraftRequest,
    if_match: Annotated[str, Header(alias="If-Match", max_length=32)],
    principal: Writer,
    service: Templates,
) -> TemplateResponse:
    command = SaveDraftCommand(
        caller=mappers.caller_of(principal),
        template_id=template_id,
        number=number,
        expected_row_version=mappers.parse_if_match(if_match),
        definition=request.definition,
    )
    return mappers.to_template_response(await service.save_draft(command))


@templates.post(
    "/{template_id}/versions",
    summary="New draft version",
    response_model=TemplateResponse,
    status_code=201,
    responses=_ERRORS,
)
async def new_version(
    template_id: UUID, key: IdempotencyKey, principal: Writer, service: Templates
) -> TemplateResponse:
    command = VersionActionCommand(caller=mappers.caller_of(principal), idempotency_key=key, template_id=template_id)
    return mappers.to_template_response(await service.new_version(command))


@templates.post(
    "/{template_id}/versions/{number}/publish",
    summary="Publish a version",
    response_model=TemplateResponse,
    responses=_ERRORS,
)
async def publish(
    template_id: UUID,
    number: int,
    request: PublishRequest,
    key: IdempotencyKey,
    principal: Publisher,
    service: Templates,
) -> TemplateResponse:
    command = VersionActionCommand(
        caller=mappers.caller_of(principal),
        idempotency_key=key,
        template_id=template_id,
        number=number,
        override_reason=request.override_reason,
    )
    return mappers.to_template_response(await service.publish(command))


@templates.post(
    "/{template_id}/versions/{number}/restore",
    summary="Roll back to a retired version",
    response_model=TemplateResponse,
    responses=_ERRORS,
)
async def restore(
    template_id: UUID, number: int, key: IdempotencyKey, principal: Publisher, service: Templates
) -> TemplateResponse:
    command = VersionActionCommand(
        caller=mappers.caller_of(principal), idempotency_key=key, template_id=template_id, number=number
    )
    return mappers.to_template_response(await service.restore(command))


@templates.post(
    "/{template_id}/clone",
    summary="Clone into your organisation",
    response_model=TemplateResponse,
    status_code=201,
    responses=_ERRORS,
)
async def clone(template_id: UUID, key: IdempotencyKey, principal: Writer, service: Templates) -> TemplateResponse:
    command = VersionActionCommand(caller=mappers.caller_of(principal), idempotency_key=key, template_id=template_id)
    return mappers.to_template_response(await service.clone(command))


@templates.get(
    "/{template_id}/export", summary="Export a template", response_model=ImportTemplateRequest, responses=_ERRORS
)
async def export(template_id: UUID, principal: Writer, service: Templates) -> ImportTemplateRequest:
    exported = await service.export(mappers.caller_of(principal), template_id)
    return ImportTemplateRequest.model_validate(exported)


@templates.delete("/{template_id}", summary="Archive a template", status_code=204, responses=_ERRORS)
async def archive(template_id: UUID, principal: Writer, service: Templates) -> Response:
    await service.archive(
        VersionActionCommand(caller=mappers.caller_of(principal), idempotency_key="-", template_id=template_id)
    )
    return Response(status_code=status.HTTP_204_NO_CONTENT)


# --- Document types -------------------------------------------------------------------------------------------


@document_types.get("", summary="List document types", response_model=DocumentTypeListResponse, responses=_ERRORS)
async def list_document_types(principal: Reader, service: DocumentTypes) -> DocumentTypeListResponse:
    found = await service.list(mappers.caller_of(principal))
    return DocumentTypeListResponse(items=[mappers.to_document_type_response(t) for t in found])


@document_types.post(
    "", summary="Add a document type", response_model=DocumentTypeResponse, status_code=201, responses=_ERRORS
)
async def create_document_type(
    request: DocumentTypeRequest, principal: Writer, service: DocumentTypes
) -> DocumentTypeResponse:
    created = await service.create(mappers.to_document_type_command(request, principal))
    return mappers.to_document_type_response(created)


@document_types.patch(
    "/{code}", summary="Change a document type", response_model=DocumentTypeResponse, responses=_ERRORS
)
async def update_document_type(
    code: str, request: DocumentTypePatchRequest, principal: Writer, service: DocumentTypes
) -> DocumentTypeResponse:
    updated = await service.update(mappers.to_document_type_command(request, principal, code=code))
    return mappers.to_document_type_response(updated)
