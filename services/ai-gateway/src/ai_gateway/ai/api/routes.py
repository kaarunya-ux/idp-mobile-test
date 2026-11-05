"""The internal API (IDP-03 ch. 4.2): POST only, a service token on every call, one service call per route (RTR-02).

``table-repair`` is not built yet: extraction-service has no table reading to repair (its tables arrive with the
generic layouts, F-EX-12).
"""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request

from ai_gateway.ai.api import mappers
from ai_gateway.ai.api.schemas import (
    ClassifyRequest,
    ClassifyResponse,
    FieldReadBatchResponse,
    FieldReadRequest,
    FieldReadSingleResponse,
    GenericReadRequest,
    GenericReadResponse,
    PageAnalysisRequest,
    PageAnalysisResponse,
    PageLayoutRequest,
    PageLayoutResponse,
    PageReadRequest,
    PageReadResponse,
    TemplateSuggestRequest,
    TemplateSuggestResponse,
)
from ai_gateway.ai.application.services.analyse_page import AnalysePageService
from ai_gateway.ai.application.services.classify_page import ClassifyPageService
from ai_gateway.ai.application.services.read_document import ReadDocumentService
from ai_gateway.ai.application.services.read_fields import ReadFieldsService
from ai_gateway.ai.application.services.read_layout import ReadLayoutService
from ai_gateway.ai.application.services.read_page import ReadPageService
from ai_gateway.ai.application.services.suggest_template import SuggestTemplateService
from ai_gateway.ai.domain.models import FieldReadQuery
from ai_gateway.core.security import ServicePrincipal, current_service

router = APIRouter(prefix="/internal/v1/ai", tags=["AI"])

type Service = Annotated[ServicePrincipal, Depends(current_service)]
_ERRORS: dict[int | str, dict[str, Any]] = {
    code: {"description": "Problem details (RFC 9457)"} for code in (400, 401, 403, 422, 429, 500, 502, 503)
}


def _service[T](name: str, kind: type[T]) -> Any:
    def provide(request: Request) -> T:
        service: T = getattr(request.app.state.ai, name)  # built once in bootstrap (DI-01)
        return service

    return Depends(provide)


type FieldReader = Annotated[ReadFieldsService, _service("read_fields", ReadFieldsService)]
type Classifier = Annotated[ClassifyPageService, _service("classify", ClassifyPageService)]
type Analyser = Annotated[AnalysePageService, _service("analyse", AnalysePageService)]
type Suggester = Annotated[SuggestTemplateService, _service("suggest", SuggestTemplateService)]
type PageReader = Annotated[ReadPageService, _service("read_page", ReadPageService)]
type DocumentReader = Annotated[ReadDocumentService, _service("read_document", ReadDocumentService)]
type LayoutReader = Annotated[ReadLayoutService, _service("read_layout", ReadLayoutService)]


@router.post(
    "/field-read",
    summary="Read fields from page images",
    response_model=FieldReadBatchResponse | FieldReadSingleResponse,
    responses=_ERRORS,
)
async def field_read(
    body: FieldReadRequest, principal: Service, service: FieldReader
) -> FieldReadBatchResponse | FieldReadSingleResponse:
    caller = mappers.caller_of(body, principal)
    if body.crop is not None and body.field is not None:
        single = await service.read_one(
            caller, mappers.image_ref(body.crop), mappers.field_spec(body.field), body.readings
        )
        return mappers.to_single_response(single)
    query = FieldReadQuery(
        document_type=body.document_type,
        pages=[mappers.image_ref(p) for p in body.pages or ()],
        fields=[mappers.field_spec(f) for f in body.fields or ()],
        readings={f.key: f.readings for f in body.fields or () if f.readings},
    )
    return mappers.to_batch_response(await service.read(caller, query))


@router.post("/classify", summary="Classify a page", response_model=ClassifyResponse, responses=_ERRORS)
async def classify(body: ClassifyRequest, principal: Service, service: Classifier) -> ClassifyResponse:
    answer = await service.classify(mappers.caller_of(body, principal), mappers.image_ref(body.page), body.candidates)
    return mappers.to_classify_response(answer)


@router.post("/page-analysis", summary="Deep analysis", response_model=PageAnalysisResponse, responses=_ERRORS)
async def page_analysis(body: PageAnalysisRequest, principal: Service, service: Analyser) -> PageAnalysisResponse:
    answer = await service.analyse(
        mappers.caller_of(body, principal),
        [mappers.image_ref(p) for p in body.pages],
        body.current_result,
        body.question,
    )
    return mappers.to_page_analysis_response(answer)


@router.post(
    "/template-suggest", summary="Suggest a template", response_model=TemplateSuggestResponse, responses=_ERRORS
)
async def template_suggest(
    body: TemplateSuggestRequest, principal: Service, service: Suggester
) -> TemplateSuggestResponse:
    words = [{"text": w.text, "box": list(w.box)} for w in body.ocr_words]
    answer = await service.suggest(mappers.caller_of(body, principal), mappers.image_ref(body.page), words)
    return mappers.to_template_suggest_response(answer)


@router.post("/page-read", summary="Read a whole page as blocks", response_model=PageReadResponse, responses=_ERRORS)
async def page_read(body: PageReadRequest, principal: Service, service: PageReader) -> PageReadResponse:
    answer = await service.read(mappers.caller_of(body, principal), mappers.image_ref(body.page))
    return mappers.to_page_read_response(answer)


@router.post(
    "/generic-read",
    summary="Read any document: its type and every field",
    response_model=GenericReadResponse,
    responses=_ERRORS,
)
async def generic_read(body: GenericReadRequest, principal: Service, service: DocumentReader) -> GenericReadResponse:
    pages = [mappers.image_ref(p) for p in body.pages]
    return mappers.to_generic_read_response(await service.read(mappers.caller_of(body, principal), pages))


@router.post(
    "/page-layout",
    summary="A page's structure over OCR's numbered lines",
    response_model=PageLayoutResponse,
    responses=_ERRORS,
)
async def page_layout(body: PageLayoutRequest, principal: Service, service: LayoutReader) -> PageLayoutResponse:
    lines = [(line.n, line.text, line.box) for line in body.lines]
    answer = await service.read(mappers.caller_of(body, principal), mappers.image_ref(body.page), lines)
    return mappers.to_page_layout_response(answer)
