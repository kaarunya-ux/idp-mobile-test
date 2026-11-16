"""The designer's worker side: preparing a sample and running a test, on fakes."""

import json
from uuid import uuid4

from extraction.core.clock import SystemClock
from extraction.pipeline.application.services.designer_work import DesignerWorkService
from extraction.pipeline.domain.evaluation import SampleOutcome, canonical, report_passes, score_samples
from extraction.pipeline.domain.exceptions import ImageBlurredError
from extraction.pipeline.domain.jobs import ObjectRef
from extraction.pipeline.domain.ports import SampleWork, TestRunWork, TestSample
from extraction.pipeline.domain.standard_templates import AADHAAR_CARD, PAN_CARD
from extraction.template.domain.definition_json import parse_definition, to_json
from idp_ocr_engines.model import Box, OcrPage, OcrWord
from support.job_fakes import Storage
from support.pipeline_fakes import FakeExtract, FakePrepare

ACCOUNT, TEMPLATE = uuid4(), uuid4()
FILE = ObjectRef(bucket="idp-originals", key=f"{ACCOUNT}/doc/file-1")


class Store:
    def __init__(self, *, sample: SampleWork | None = None, run: TestRunWork | None = None) -> None:
        self.sample, self.run = sample, run
        self.ready: dict = {}
        self.failed: list = []
        self.finished: dict = {}

    def pending_sample(self, sample_id):
        return self.sample

    def sample_ready(self, sample_id, *, page_key, words_key):
        self.ready = {"page": page_key, "words": words_key}

    def sample_failed(self, sample_id, code):
        self.failed.append(code)

    def start_test_run(self, run_id):
        return self.run

    def finish_test_run(self, run_id, report):
        self.finished = dict(report)

    def fail_test_run(self, run_id, code):
        self.failed.append(code)


class Engine:
    engine_id, engine_version = "ocr-a", "test"

    def read_page(self, image, languages):
        return OcrPage(
            width=4,
            height=3,
            words=(
                OcrWord(
                    text="Name", box=Box(x=0.1, y=0.2, width=0.3, height=0.1), confidence=0.9, line_id=0, block_id=0
                ),
            ),
        )


class NoText:
    def read_first_page(self, content):
        return None


def _service(store: Store, storage: Storage, *, prepare_error=None) -> DesignerWorkService:
    return DesignerWorkService(
        store,
        storage,
        storage,
        prepare=lambda progress: FakePrepare(progress, error=prepare_error),
        extract=lambda progress, templates: FakeExtract(progress),
        engine=Engine(),
        digital=NoText(),
        parse=parse_definition,
        templates_bucket="idp-templates",
        clock=SystemClock(),
    )


def _sample() -> SampleWork:
    return SampleWork(id=uuid4(), template_id=TEMPLATE, version=1, account_id=ACCOUNT, files=(FILE,))


def test_stores_the_first_page_and_its_words_when_a_sample_is_prepared() -> None:
    storage, sample = Storage(), _sample()
    storage.objects[(FILE.bucket, FILE.key)] = (b"%PDF-fake", "application/pdf")
    store = Store(sample=sample)

    _service(store, storage).prepare_sample(sample.id)

    base = f"{ACCOUNT}/{TEMPLATE}/1/samples/{sample.id}"
    assert store.ready == {"page": f"{base}/page-0.png", "words": f"{base}/words.json"}
    words = json.loads(storage.objects[("idp-templates", f"{base}/words.json")][0])
    assert words == [{"text": "Name", "box": [0.1, 0.2, 0.3, 0.1]}]
    assert f"{base}/page-0.png" in storage.pages


def test_marks_the_sample_failed_with_the_quality_code_when_the_photo_is_blurred() -> None:
    storage, sample = Storage(), _sample()
    storage.objects[(FILE.bucket, FILE.key)] = (b"%PDF-fake", "application/pdf")
    store = Store(sample=sample)

    _service(store, storage, prepare_error=ImageBlurredError("blurred")).prepare_sample(sample.id)

    assert store.failed == ["EXT-422-QUALITY-001"]
    assert store.ready == {}


def test_does_nothing_when_the_sample_is_already_prepared() -> None:
    store = Store(sample=None)

    _service(store, Storage()).prepare_sample(uuid4())

    assert (store.ready, store.failed) == ({}, [])


def test_scores_every_sample_and_records_a_missing_file_as_a_failed_sample() -> None:
    storage = Storage()
    storage.objects[(FILE.bucket, FILE.key)] = (b"%PDF-fake", "application/pdf")
    missing = ObjectRef(bucket="idp-originals", key=f"{ACCOUNT}/doc/gone")
    run = TestRunWork(
        id=uuid4(),
        template_id=TEMPLATE,
        version=1,
        account_id=ACCOUNT,
        definition=to_json(PAN_CARD),
        samples=[
            TestSample(sample_id="s1", files=(FILE,), expected={"pan_number": "ABCDE1234F"}),
            TestSample(sample_id="s2", files=(missing,), expected={"pan_number": "ABCDE1234F"}),
        ],
    )
    store = Store(run=run)

    _service(store, storage).run_test(run.id)

    assert store.finished["samples"] == 2
    assert store.finished["failedSamples"] == [{"sampleId": "s2", "code": "EXT-422-FILE-001"}]
    assert store.finished["fields"]["pan_number"]["total"] == 1


def test_fails_the_run_when_the_definition_no_longer_parses() -> None:
    run = TestRunWork(id=uuid4(), template_id=TEMPLATE, version=1, account_id=None, definition={}, samples=[])
    store = Store(run=run)

    _service(store, Storage()).run_test(run.id)

    assert store.failed == ["EXT-422-DEFINITION-001"]


def _result(**values: str | None) -> dict:
    return {"fields": [{"key": k, "value": v, "normalizedValue": v} for k, v in values.items()]}


def test_ignores_case_spaces_and_punctuation_and_compares_masked_fields_by_their_last_digits() -> None:
    outcomes = [
        SampleOutcome(
            sample_id="a",
            expected={"aadhaar_number": "1234 5678 9012", "name": "test person"},
            result=_result(aadhaar_number="XXXX XXXX 9012", name="TEST-PERSON"),
        ),
        SampleOutcome(
            sample_id="b",
            expected={"aadhaar_number": "1111 2222 3333", "name": "OTHER"},
            result=_result(aadhaar_number="XXXX XXXX 9999", name="OTHER"),
        ),
    ]

    report = score_samples(AADHAAR_CARD, outcomes)

    assert report["fields"]["aadhaar_number"]["accuracy"] == 0.5
    assert report["fields"]["name"]["accuracy"] == 1.0
    assert report["fields"]["aadhaar_number"]["confusions"][0]["sampleId"] == "b"
    assert canonical(" ab-1 ") == "AB1"


def test_passes_only_when_every_required_field_meets_the_minimum() -> None:
    passing = {"fields": {"a": {"required": True, "accuracy": 0.9}, "b": {"required": False, "accuracy": None}}}
    untested = {"fields": {"a": {"required": True, "accuracy": None}}}

    assert report_passes(passing, min_accuracy=0.9)
    assert not report_passes(untested, min_accuracy=0.9)
    assert not report_passes({"fields": {}}, min_accuracy=0.9)
