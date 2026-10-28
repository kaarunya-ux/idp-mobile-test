"""Between the API bodies and the application's types; the bodies never go further than this (CLAUDE.md)."""

from typing import Any

from ai_gateway.ai.api.schemas import (
    AnalysedFieldBody,
    CallerBody,
    ClassifyResponse,
    FieldBody,
    FieldReadBatchResponse,
    FieldReadSingleResponse,
    FieldValueBody,
    ImageRefBody,
    PageAnalysisResponse,
    SuggestedColumnBody,
    SuggestedFieldBody,
    SuggestedTableBody,
    TemplateSuggestResponse,
    TokensBody,
)
from ai_gateway.ai.application.answer_models import ClassifyAnswer, PageAnalysisAnswer, TemplateSuggestAnswer
from ai_gateway.ai.domain.models import Answer, Caller, FieldSpec, FieldValue, ImageRef, Plan, Priority, Provenance
from ai_gateway.core.security import ServicePrincipal


def caller_of(body: CallerBody, service: ServicePrincipal) -> Caller:
    plan = body.plan or (Plan.CORPORATE if body.priority is Priority.PRIORITY else Plan.PERSONAL)
    return Caller(client_id=service.client_id, account_id=body.account_id, priority=body.priority, plan=plan)


def image_ref(body: ImageRefBody) -> ImageRef:
    return ImageRef(bucket=body.bucket, key=body.key)


def field_spec(body: FieldBody) -> FieldSpec:
    return FieldSpec(key=body.key, label=body.label, type=body.type, rule=body.rule)


def _provenance(provenance: Provenance) -> dict[str, Any]:
    return {
        "model": provenance.model,
        "prompt_version": provenance.prompt_version,
        "latency_ms": provenance.latency_ms,
        "tokens": TokensBody(input=provenance.tokens.input, output=provenance.tokens.output),
    }


def to_batch_response(answer: Answer[list[FieldValue]]) -> FieldReadBatchResponse:
    fields = [FieldValueBody(key=v.key, value=v.value) for v in answer.value]
    return FieldReadBatchResponse(fields=fields, **_provenance(answer.provenance))


def to_single_response(answer: Answer[FieldValue]) -> FieldReadSingleResponse:
    found = answer.value
    return FieldReadSingleResponse(
        value=found.value, confidence=found.confidence, reason=found.reason, **_provenance(answer.provenance)
    )


def to_classify_response(answer: Answer[ClassifyAnswer]) -> ClassifyResponse:
    return ClassifyResponse(
        document_type=answer.value.document_type, confidence=answer.value.confidence, **_provenance(answer.provenance)
    )


def to_page_analysis_response(answer: Answer[PageAnalysisAnswer]) -> PageAnalysisResponse:
    found = answer.value
    return PageAnalysisResponse(
        fields=[AnalysedFieldBody(**f.model_dump()) for f in found.fields],
        summary=found.summary,
        warnings=found.warnings,
        **_provenance(answer.provenance),
    )


def to_template_suggest_response(answer: Answer[TemplateSuggestAnswer]) -> TemplateSuggestResponse:
    found = answer.value
    return TemplateSuggestResponse(
        fields=[SuggestedFieldBody(**f.model_dump()) for f in found.fields],
        tables=[
            SuggestedTableBody(
                name=t.name, box=t.box, columns=[SuggestedColumnBody(key=c.key, label=c.label) for c in t.columns]
            )
            for t in found.tables
        ],
        **_provenance(answer.provenance),
    )
