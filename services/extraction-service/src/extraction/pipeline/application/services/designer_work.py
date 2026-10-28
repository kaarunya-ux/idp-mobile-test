"""Use cases: the template designer's work in a pipeline worker (IDP-03 ch. 2.9).

``prepare_sample`` turns a sample document into its first page (PNG) and its words (JSON) in ``idp-templates``, so
suggestions and test runs don't render it again. A digital PDF's words come from its text layer; anything else is
read by engine A. The quality gate applies: a blurred sample is refused with its reason, as a document would be.

``run_test`` reads every sample with the one tested version (named, so no classifying; AI not used, so the score is
the template's own) and scores the results against the expected values (``evaluation.score_samples``).
"""

import json
from collections.abc import Callable
from uuid import UUID

import structlog

from extraction.core.clock import Clock
from extraction.core.errors import DomainError
from extraction.pipeline.application.commands import ExtractDocumentCommand, PrepareDocumentCommand
from extraction.pipeline.application.services.extract_document import ExtractDocumentService
from extraction.pipeline.application.services.prepare_document import PrepareDocumentService
from extraction.pipeline.application.services.run_extraction import readable
from extraction.pipeline.domain.evaluation import SampleOutcome, score_samples
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.ports import (
    DesignerWorkStorePort,
    DigitalTextReaderPort,
    ObjectStoragePort,
    OfficeConverterPort,
    PageImageStorePort,
    ProgressReporterPort,
    TestSample,
)
from extraction.pipeline.domain.stages import Stage
from extraction.pipeline.domain.template import TemplateDefinition
from idp_ocr_engines.model import OcrPage
from idp_ocr_engines.port import OcrEnginePort

log = structlog.get_logger(__name__)

_ENGLISH = ("en",)
WORDS_CONTENT_TYPE = "application/json"

type PrepareFactory = Callable[[ProgressReporterPort], PrepareDocumentService]
type ExtractFactory = Callable[[ProgressReporterPort, list[TemplateDefinition]], ExtractDocumentService]
type DefinitionParser = Callable[..., TemplateDefinition]


class _Silent:
    """Designer work reports no progress: it isn't a job item."""

    def report(self, stage: Stage, percent: int) -> None:
        return None


class DesignerWorkService:
    # every collaborator is a port, a factory or a name
    def __init__(  # noqa: PLR0913
        self,
        store: DesignerWorkStorePort,
        storage: ObjectStoragePort,
        pages: PageImageStorePort,
        *,
        prepare: PrepareFactory,
        extract: ExtractFactory,
        engine: OcrEnginePort,
        digital: DigitalTextReaderPort,
        parse: DefinitionParser,
        templates_bucket: str,
        clock: Clock,
        office: OfficeConverterPort | None = None,
    ) -> None:
        self._office = office
        self._store = store
        self._storage = storage
        self._pages = pages
        self._prepare = prepare
        self._extract = extract
        self._engine = engine
        self._digital = digital
        self._parse = parse
        self._bucket = templates_bucket
        self._clock = clock

    def prepare_sample(self, sample_id: UUID) -> None:
        work = self._store.pending_sample(sample_id)
        if work is None:
            return
        try:
            content = readable(self._storage.read_bytes(work.files[0]), self._office)
            page = self._prepare(_Silent()).prepare(PrepareDocumentCommand(content=content)).pages[0].clean
            text = self._digital.read_first_page(content) or self._engine.read_page(page, _ENGLISH)
        except DomainError as error:
            self._store.sample_failed(sample_id, error.code)
            log.warning("sample_failed", sample_id=str(sample_id), code=error.code)
            return
        base = f"{work.account_id or 'standard'}/{work.template_id}/{work.version}/samples/{sample_id}"
        page_ref = ObjectRef(bucket=self._bucket, key=f"{base}/page-0.png")
        words_ref = ObjectRef(bucket=self._bucket, key=f"{base}/words.json")
        self._pages.save_page(page_ref, page)
        self._storage.write_bytes(words_ref, json.dumps(_words(text), ensure_ascii=False).encode(), WORDS_CONTENT_TYPE)
        self._store.sample_ready(sample_id, page_key=page_ref.key, words_key=words_ref.key)
        log.info("sample_ready", sample_id=str(sample_id), words=len(text.words))

    def run_test(self, run_id: UUID) -> None:
        work = self._store.start_test_run(run_id)
        if work is None:
            return
        try:
            template = self._parse(
                dict(work.definition),
                template_id=str(work.template_id),
                version=work.version,
                scope="STANDARD" if work.account_id is None else "ACCOUNT",
            )
        except DomainError as error:
            self._store.fail_test_run(run_id, error.code)
            return
        outcomes = [self._read(sample, template) for sample in work.samples]
        self._store.finish_test_run(run_id, score_samples(template, outcomes))
        log.info("test_run_finished", run_id=str(run_id), samples=len(outcomes))

    def _read(self, sample: TestSample, template: TemplateDefinition) -> SampleOutcome:
        try:
            contents = [readable(self._storage.read_bytes(f), self._office) for f in sample.files]
            prepared = [self._prepare(_Silent()).prepare(PrepareDocumentCommand(content=c)) for c in contents]
            command = ExtractDocumentCommand(
                pages=tuple(p for r in prepared for p in r.pages),
                qualities=tuple(q for r in prepared for q in r.qualities),
                digital_page=self._digital.read_first_page(contents[0]),
                template_forced=True,
            )
            result = self._extract(_Silent(), [template]).extract(command)
        except DomainError as error:
            return SampleOutcome(
                sample_id=sample.sample_id, expected=sample.expected, result=None, error_code=error.code
            )
        return SampleOutcome(sample_id=sample.sample_id, expected=sample.expected, result=result)


def _words(page: OcrPage) -> list[dict[str, object]]:
    return [
        {"text": w.text, "box": [round(w.box.x, 4), round(w.box.y, 4), round(w.box.width, 4), round(w.box.height, 4)]}
        for w in page.words
    ]
