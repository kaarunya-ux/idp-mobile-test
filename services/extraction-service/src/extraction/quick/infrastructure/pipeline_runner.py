"""``DocumentPipelinePort`` on the extraction pipeline: the same services the queued path's worker runs (ADR 0009).

The quality gate, both OCR engines, the template's fields, and the AI on the crops of the fields the engines left
unsure, all on ai-gateway's INTERACTIVE lane. The first two prepared pages are stored in the call's workspace folder,
for the AI to read by key (a template's classify, and the generic read when no template matches). A PDF longer than
``QUICK__MAX_PAGES`` is refused before any work: long documents go the queued way. ``stage`` only turns the files
into page images and stores them, for the straight-to-the-model extract.
"""

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any

import pypdfium2 as pdfium

from extraction.pipeline.application.commands import ExtractDocumentCommand, PrepareDocumentCommand
from extraction.pipeline.application.services.run_extraction import PipelineFactory
from extraction.pipeline.domain.exceptions import TooManyPagesError
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.models import PreparedPage
from extraction.pipeline.domain.ports import AiCallContext, PageImageStorePort, PageLoaderPort
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.domain.template import TemplateDefinition
from extraction.quick.domain.files import kind_of
from extraction.quick.domain.models import (
    ExtractedField,
    FileKind,
    PipelineOutcome,
    QuickCaller,
    QuickResult,
    ReadBy,
    StagedFile,
)

_PAGES_FOR_AI = 2  # a card's front and back, or a PDF's first two pages
_TYPES = {"DATE": "date", "NUMBER": "number"}  # every other template field type is text


class _NoProgress:
    """Nobody follows the stages of a synchronous call: the answer comes on the request."""

    def report(self, stage: Stage, percent: int) -> None:
        return None


class PipelineDocumentRunner:
    def __init__(
        self,
        pipeline: PipelineFactory,
        loader: PageLoaderPort,
        pages: PageImageStorePort,
        *,
        bucket: str,
        max_pages: int,
    ) -> None:
        self._pipeline = pipeline
        self._loader = loader
        self._pages = pages
        self._bucket = bucket
        self._max_pages = max_pages

    def run(
        self,
        caller: QuickCaller,
        contents: Sequence[bytes],
        folder: str,
        template: TemplateDefinition | None = None,
    ) -> PipelineOutcome:
        self._refuse_long_pdfs(contents)
        progress = _NoProgress()
        prepare = self._pipeline.prepare_service(progress)
        prepared = [prepare.prepare(PrepareDocumentCommand(content=content)) for content in contents]
        pages = _renumbered([page for result in prepared for page in result.pages])
        stored = [self._store(page, folder) for page in pages[:_PAGES_FOR_AI]]
        context = AiCallContext(
            account_id=caller.account_id,
            priority=True,
            page_refs=tuple(stored),
            crop_folder=ObjectRef(bucket=self._bucket, key=f"{folder}/crops"),
            interactive=True,
            plan=caller.plan,
        )
        command = ExtractDocumentCommand(
            pages=pages,
            qualities=tuple(quality for result in prepared for quality in result.qualities),
            digital_page=self._pipeline.digital_reader.read_first_page(contents[0]),
            ai_context=context,
            template_forced=template is not None,
        )
        templates = (
            [template] if template is not None else self._pipeline.catalog.templates_for(caller.account_id, None)
        )
        result = self._pipeline.extract_service(progress, templates).extract(command)
        return PipelineOutcome(
            result=quick_result(result), pages=[StagedFile(bucket=r.bucket, key=r.key) for r in stored]
        )

    def stage(self, contents: Sequence[bytes], folder: str) -> Sequence[StagedFile]:
        self._refuse_long_pdfs(contents)
        images = [image for content in contents for image in self._loader.load_pages(content)][:_PAGES_FOR_AI]
        stored = []
        for index, image in enumerate(images):
            ref = ObjectRef(bucket=self._bucket, key=f"{folder}/page-{index}.png")
            self._pages.save_page(ref, image)
            stored.append(StagedFile(bucket=ref.bucket, key=ref.key))
        return stored

    def _refuse_long_pdfs(self, contents: Sequence[bytes]) -> None:
        for content in contents:
            if kind_of(content) is not FileKind.PDF:
                continue
            try:
                document = pdfium.PdfDocument(content)
            except pdfium.PdfiumError:
                continue  # the loader refuses it with its own reason
            count = len(document)
            document.close()
            if count > self._max_pages:
                raise TooManyPagesError(
                    f"The PDF has {count} pages; at most {self._max_pages} are read at once. Send it as a job."
                )

    def _store(self, page: PreparedPage, folder: str) -> ObjectRef:
        ref = ObjectRef(bucket=self._bucket, key=f"{folder}/page-{page.page_index}.png")
        self._pages.save_page(ref, page.clean)
        return ref


def quick_result(result: Mapping[str, Any]) -> QuickResult | None:
    """The pipeline's result (IDP-00 ch. 7.8) as the route's answer, or None when no template matched."""
    if not result.get("template"):
        return None
    document = result["documentType"]
    models = result["provenance"]["models"]
    return QuickResult(
        document_type=str(document["code"]).lower(),
        document_confidence=float(document["confidence"]),
        fields=[_field(found) for found in result["fields"]],
        read_by=ReadBy.OCR_AND_AI if models else ReadBy.OCR,
        model_id=str(models[0]["id"]) if models else None,
        prompt_version=str(models[0]["promptVersion"]) if models else None,
    )


def _field(found: Mapping[str, Any]) -> ExtractedField:
    value = found["value"]
    return ExtractedField(
        key=str(found["key"]),
        label=str(found["label"]),
        type=_TYPES.get(str(found["type"]), "string"),
        value=str(value) if value is not None else None,
        confidence=float(found["confidence"]) if value is not None else None,
        status=str(found["status"]),
        source=str(found["source"]) if found["source"] else None,
    )


def _renumbered(pages: Sequence[PreparedPage]) -> tuple[PreparedPage, ...]:
    """Pages of the back picture continue the numbering of the front, as on the queued path."""
    return tuple(page if page.page_index == i else replace(page, page_index=i) for i, page in enumerate(pages))
