"""The quick extract's view of the templates (``TemplateListPort``) and of ai-gateway's ``classify``
(``TemplateClassifierPort``), over the pipeline's catalog and classifier (ADR 0016)."""

from collections.abc import Sequence

from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.ports import AiCallContext, AiClassifierPort, TemplateCatalogPort
from extraction.pipeline.domain.template import TemplateDefinition
from extraction.quick.domain.models import QuickCaller, StagedFile


class CatalogTemplates:
    def __init__(self, catalog: TemplateCatalogPort) -> None:
        self._catalog = catalog

    def templates_for(self, caller: QuickCaller) -> Sequence[TemplateDefinition]:
        return self._catalog.templates_for(caller.account_id, None)


class GatewayTemplateClassifier:
    """On the INTERACTIVE lane: a person waits for the answer."""

    def __init__(self, classifier: AiClassifierPort) -> None:
        self._classifier = classifier

    def classify(self, caller: QuickCaller, page: StagedFile, codes: Sequence[str]) -> tuple[str | None, float]:
        ref = ObjectRef(bucket=page.bucket, key=page.key)
        context = AiCallContext(
            account_id=caller.account_id, priority=True, page_refs=(ref,), interactive=True, plan=caller.plan
        )
        answer = self._classifier.classify(ref, codes, context)
        return answer.document_type, answer.confidence
