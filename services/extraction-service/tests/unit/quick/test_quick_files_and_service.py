"""The synchronous extract's file checks (FIL-03, FIL-05) and its use case: OCR first, the generic read only when no
template matched, and the workspace always discarded."""

from collections.abc import Sequence
from uuid import UUID

import pytest

from extraction.pipeline.domain.custom_template import CUSTOM_SCOPE, CustomField
from extraction.pipeline.domain.exceptions import AiUnavailableError, ImageBlurredError
from extraction.pipeline.domain.standard_templates import STANDARD_TEMPLATES
from extraction.pipeline.domain.template import TemplateDefinition
from extraction.quick.application.quick_extract import QuickExtractService
from extraction.quick.application.template_matcher import TemplateMatcher
from extraction.quick.domain.detection import DetectedBy, UserTemplate
from extraction.quick.domain.exceptions import (
    BusyError,
    FieldsInvalidError,
    FileTooLargeError,
    FileTypeUnsupportedError,
    FileUnreadableError,
)
from extraction.quick.domain.files import check_uploads, kind_of
from extraction.quick.domain.lines import LinesField, LinesReading
from extraction.quick.domain.models import (
    ExtractedField,
    FileKind,
    GenericMode,
    PipelineOutcome,
    QuickCaller,
    QuickResult,
    ReadBy,
    StagedFile,
    UploadLimits,
)
from idp_ocr_engines.errors import OcrEngineError
from idp_ocr_engines.model import Box, OcrPage, OcrWord

PDF, JPEG, PNG = b"%PDF-1.7 made up", b"\xff\xd8\xff\xe0 made up", b"\x89PNG\r\n\x1a\n made up"
WEBP = b"RIFF\x10\x00\x00\x00WEBPVP8 made up"
LIMITS = UploadLimits(max_file_bytes=100, max_total_bytes=150)
CALLER = QuickCaller(account_id=UUID("0192f0aa-0000-7000-8000-000000000001"), plan="PERSONAL")
BY_OCR = QuickResult(
    document_type="pan_card",
    document_confidence=0.97,
    fields=[ExtractedField(key="pan", label="PAN", type="string", value="ABCPE1234F", confidence=0.97)],
    read_by=ReadBy.OCR,
)
READ_NOTHING = QuickResult(  # a template the page isn't: none of its fields found
    document_type="pan_card",
    document_confidence=0.9,
    fields=[
        ExtractedField(key="pan", label="PAN", type="string", value=None, confidence=None),
        ExtractedField(key="name", label="Name", type="string", value="", confidence=None),
    ],
    read_by=ReadBy.OCR,
)
BY_AI = QuickResult(document_type="invoice", document_confidence=0.8, fields=[], read_by=ReadBy.AI, model_id="vl")
BY_AI_WITH_EMAIL = QuickResult(
    document_type="business_card",
    document_confidence=0.9,
    fields=[ExtractedField(key="email", label="Email", type="string", value="a.b@mock.com", confidence=0.99)],
    read_by=ReadBy.AI,
    model_id="vl",
)
PAGES = [StagedFile(bucket="idp-derived", key=f"acct/quick/1/page-{n}.png") for n in range(3)]


@pytest.mark.parametrize(
    ("content", "kind"), [(PDF, FileKind.PDF), (JPEG, FileKind.JPEG), (PNG, FileKind.PNG), (WEBP, FileKind.WEBP)]
)
def test_reads_the_kind_from_the_content(content: bytes, kind: FileKind) -> None:
    assert kind_of(content) is kind


def test_accepts_a_front_and_back_picture() -> None:
    assert check_uploads(JPEG, PNG, LIMITS) == [FileKind.JPEG, FileKind.PNG]


def test_accepts_a_webp_picture() -> None:
    assert check_uploads(WEBP, None, LIMITS) == [FileKind.WEBP]


def test_refuses_another_riff_file_such_as_a_wav() -> None:
    with pytest.raises(FileTypeUnsupportedError):
        check_uploads(b"RIFF\x10\x00\x00\x00WAVEfmt made up", None, LIMITS)


def test_accepts_a_pdf_alone() -> None:
    assert check_uploads(PDF, None, LIMITS) == [FileKind.PDF]


@pytest.mark.parametrize(
    ("front", "back", "error"),
    [
        (b"", None, FileUnreadableError),
        (JPEG, b"", FileUnreadableError),
        (b"GIF89a made up", None, FileTypeUnsupportedError),
        (PDF, JPEG, FileTypeUnsupportedError),  # a PDF is sent alone
        (JPEG, PDF, FileTypeUnsupportedError),
        (JPEG + b"x" * 100, None, FileTooLargeError),
        (JPEG + b"x" * 80, PNG + b"x" * 80, FileTooLargeError),  # each under the limit, both together over it
    ],
)
def test_refuses_the_upload_when_a_rule_is_broken(front: bytes, back: bytes | None, error: type[Exception]) -> None:
    with pytest.raises(error):
        check_uploads(front, back, LIMITS)


class FakeWorkspace:
    def __init__(self) -> None:
        self.opened: list[str] = []
        self.discarded: list[str] = []

    def open(self, caller: QuickCaller) -> str:
        self.opened.append(f"{caller.account_id}/quick/1")
        return self.opened[-1]

    def discard(self, folder: str) -> None:
        self.discarded.append(folder)


class FakePipeline:
    def __init__(self, result: QuickResult | None, error: Exception | None = None) -> None:
        self.result, self.error = result, error
        self.runs: list[int] = []
        self.templates: list[TemplateDefinition | None] = []

    def run(
        self, caller: QuickCaller, contents: Sequence[bytes], folder: str, template: TemplateDefinition | None = None
    ) -> PipelineOutcome:
        self.runs.append(len(contents))
        self.templates.append(template)
        if self.error is not None:
            raise self.error
        return PipelineOutcome(result=self.result, pages=PAGES)

    def stage(self, contents: Sequence[bytes], folder: str) -> Sequence[StagedFile]:
        self.staged = len(contents)
        return PAGES


class FakeReader:
    def __init__(self, error: Exception | None = None, result: QuickResult = BY_AI) -> None:
        self.error, self.result = error, result
        self.lines: list[Sequence[str]] = []
        self.pages: list[Sequence[StagedFile]] = []

    def read(self, caller: QuickCaller, pages: Sequence[StagedFile]) -> QuickResult:
        self.pages.append(pages)
        if self.error is not None:
            raise self.error
        return self.result

    def read_lines(self, caller: QuickCaller, lines: Sequence[str]) -> LinesReading:
        self.lines.append(lines)
        field = LinesField(key="email", label="Email", type="string", lines=(0,))
        return LinesReading(
            document_type="business_card", document_confidence=0.9, fields=[field], model_id="smart", prompt_version="l"
        )


class FakeWords:
    def __init__(self, pages: Sequence[OcrPage] = (), error: Exception | None = None) -> None:
        self.pages, self.error = pages, error
        self.read: list[int] = []

    def read_words(self, contents: Sequence[bytes]) -> Sequence[OcrPage]:
        self.read.append(len(contents))
        if self.error is not None:
            raise self.error
        return self.pages


def _service(
    pipeline: FakePipeline,
    reader: FakeReader,
    workspace: FakeWorkspace,
    words: FakeWords | None = None,
    mode: GenericMode = GenericMode.PICTURE,
) -> QuickExtractService:
    return QuickExtractService(
        workspace=workspace,
        pipeline=pipeline,
        reader=reader,
        words=words or FakeWords(),
        limits=LIMITS,
        generic_mode=mode,
    )


class FakeTemplates:
    def templates_for(self, caller: QuickCaller) -> Sequence[TemplateDefinition]:
        return STANDARD_TEMPLATES


def test_answers_from_ocr_and_never_asks_the_generic_read_when_a_template_matched() -> None:
    pipeline, reader, workspace = FakePipeline(BY_OCR), FakeReader(), FakeWorkspace()

    assert _service(pipeline, reader, workspace).extract(CALLER, JPEG, PNG) is BY_OCR
    assert (pipeline.runs, reader.pages) == ([2], [])
    assert workspace.discarded == workspace.opened


def test_asks_the_generic_read_with_the_first_two_pages_when_no_template_matched() -> None:
    reader, workspace = FakeReader(), FakeWorkspace()

    assert _service(FakePipeline(None), reader, workspace).extract(CALLER, PDF, None) == BY_AI
    assert reader.pages == [PAGES[:2]]
    assert workspace.discarded == workspace.opened


@pytest.mark.parametrize(
    ("pipeline", "reader"),
    [
        (FakePipeline(None, ImageBlurredError("Page 1 is blurred.")), FakeReader()),
        (FakePipeline(None), FakeReader(BusyError())),
    ],
)
def test_discards_the_workspace_when_the_call_fails(pipeline: FakePipeline, reader: FakeReader) -> None:
    workspace = FakeWorkspace()

    with pytest.raises((ImageBlurredError, BusyError)):
        _service(pipeline, reader, workspace).extract(CALLER, JPEG, None)
    assert workspace.discarded == workspace.opened


def test_opens_no_workspace_when_the_upload_is_refused() -> None:
    workspace = FakeWorkspace()

    with pytest.raises(FileTypeUnsupportedError):
        _service(FakePipeline(BY_OCR), FakeReader(), workspace).extract(CALLER, PDF, JPEG)
    assert workspace.opened == []


def test_sends_the_pages_straight_to_the_generic_read_without_running_ocr() -> None:
    pipeline, reader, workspace = FakePipeline(BY_OCR), FakeReader(), FakeWorkspace()

    assert _service(pipeline, reader, workspace).extract_with_ai(CALLER, JPEG, PNG) == BY_AI
    assert (pipeline.runs, pipeline.staged, reader.pages) == ([], 2, [PAGES[:2]])
    assert workspace.discarded == workspace.opened


def _word(text: str, x: float, y: float) -> OcrWord:
    return OcrWord(text=text, box=Box(x=x, y=y, width=0.1, height=0.05), confidence=0.9, line_id=0, block_id=0)


EMAIL_PAGE = OcrPage(width=1000, height=600, words=(_word("Email:", 0.1, 0.4), _word("a.b@mock.com", 0.25, 0.4)))


def test_gives_each_generic_value_the_box_where_ocr_found_it() -> None:
    reader, words = FakeReader(result=BY_AI_WITH_EMAIL), FakeWords([EMAIL_PAGE])

    found = _service(FakePipeline(None), reader, FakeWorkspace(), words).extract_with_ai(CALLER, JPEG, PNG)

    (field,) = found.fields
    assert field.box is not None
    box = field.box.box
    assert (field.box.page, box.x, box.y, box.width, box.height) == pytest.approx((1, 0.25, 0.4, 0.1, 0.05))
    assert words.read == [2]  # front and back, as uploaded


def test_sends_the_generic_values_without_boxes_when_ocr_fails() -> None:
    reader, words = FakeReader(result=BY_AI_WITH_EMAIL), FakeWords(error=OcrEngineError("engine down"))

    found = _service(FakePipeline(None), reader, FakeWorkspace(), words).extract_with_ai(CALLER, JPEG, None)

    assert found == BY_AI_WITH_EMAIL


def test_sends_only_the_ocr_lines_and_no_picture_in_lines_mode() -> None:
    pipeline, reader, words = FakePipeline(None), FakeReader(), FakeWords([EMAIL_PAGE])
    service = _service(pipeline, reader, FakeWorkspace(), words, GenericMode.LINES)

    found = service.extract_with_ai(CALLER, JPEG, None)

    assert reader.lines == [["Email: a.b@mock.com"]]
    assert reader.pages == []
    assert (found.document_type, [f.value for f in found.fields]) == ("business_card", ["a.b@mock.com"])
    assert not hasattr(pipeline, "staged")  # no page image was stored


def test_reads_the_picture_in_lines_mode_when_ocr_finds_no_text() -> None:
    pipeline, reader = FakePipeline(None), FakeReader()
    service = _service(pipeline, reader, FakeWorkspace(), FakeWords([]), GenericMode.LINES)

    assert service.extract_with_ai(CALLER, JPEG, None) == BY_AI
    assert (reader.lines, reader.pages) == ([], [PAGES[:2]])


def test_discards_the_workspace_when_the_generic_read_fails() -> None:
    workspace = FakeWorkspace()

    with pytest.raises(BusyError):
        _service(FakePipeline(None), FakeReader(BusyError()), workspace).extract_with_ai(CALLER, JPEG, None)
    assert workspace.discarded == workspace.opened


def test_reads_a_custom_templates_keys_with_a_template_built_from_them() -> None:
    pipeline, workspace = FakePipeline(BY_OCR), FakeWorkspace()
    keys = [CustomField(key="card_number", label="Card No"), CustomField(key="holder", label="Name")]

    assert _service(pipeline, FakeReader(), workspace).extract_custom(CALLER, JPEG, None, keys) is BY_OCR
    template = pipeline.templates[0]
    assert template is not None
    assert (template.scope, [f.key for f in template.fields]) == (CUSTOM_SCOPE, ["card_number", "holder"])
    assert workspace.discarded == workspace.opened


@pytest.mark.parametrize(
    "keys",
    [
        [],
        [CustomField(key="a", label="A"), CustomField(key="a", label="Again")],  # the same key twice
        [CustomField(key=f"k{n}", label="L") for n in range(51)],
    ],
)
def test_refuses_custom_keys_that_are_none_repeated_or_too_many(keys: list[CustomField]) -> None:
    workspace = FakeWorkspace()

    with pytest.raises(FieldsInvalidError):
        _service(FakePipeline(BY_OCR), FakeReader(), workspace).extract_custom(CALLER, JPEG, None, keys)
    assert workspace.opened == []


# --- /extract/auto: find the template, ours or the user's, then extract with it (ADR 0016) -----------------------

PAN_PAGE = OcrPage(
    width=1000,
    height=630,
    words=tuple(_word(t, 0.1 * i, 0.1) for i, t in enumerate(["INCOME", "TAX", "GOVT.", "Permanent", "Account"])),
)
GREEN_PAGE = OcrPage(
    width=1000,
    height=1000,
    words=tuple(_word(t, 0.1 * i, 0.1) for i, t in enumerate(["Surname", "MOCK", "USCIS#", "000-000-001"])),
)
UNKNOWN_PAGE = OcrPage(width=1000, height=630, words=(_word("Government", 0.1, 0.1), _word("of", 0.2, 0.1)))
AADHAAR_HINT = OcrPage(  # 2 of Aadhaar's 4 keywords: evidence, but under 85%, so the AI is asked
    width=1000,
    height=630,
    words=tuple(_word(t, 0.1 * i, 0.1) for i, t in enumerate(["Government", "of", "India", "DOB:", "01/01/1990"])),
)
GREEN_CARD = UserTemplate(
    id="backend-7",
    name="Green Card",
    fields=(CustomField(key="surname", label="Surname"), CustomField(key="uscis_number", label="USCIS#")),
    field_ids={"surname": "f1", "uscis_number": "f2"},
)


class FakeClassifier:
    def __init__(self, answer: tuple[str | None, float] = ("AADHAAR_CARD", 0.92), error: Exception | None = None):
        self.answer, self.error = answer, error
        self.asked: list[Sequence[str]] = []

    def classify(self, caller: QuickCaller, page: StagedFile, codes: Sequence[str]) -> tuple[str | None, float]:
        self.asked.append(codes)
        if self.error is not None:
            raise self.error
        return self.answer


def _auto(
    pipeline: FakePipeline,
    page: OcrPage,
    classifier: FakeClassifier | None = None,
    workspace: FakeWorkspace | None = None,
) -> QuickExtractService:
    matcher = TemplateMatcher(templates=FakeTemplates(), classifier=classifier, minimum=0.85, min_filled=0.5)
    return QuickExtractService(
        workspace=workspace or FakeWorkspace(),
        pipeline=pipeline,
        reader=FakeReader(),
        words=FakeWords([page]),
        limits=LIMITS,
        matcher=matcher,
    )


def test_reads_with_our_template_found_by_its_keywords_without_asking_the_ai() -> None:
    pipeline, classifier = FakePipeline(BY_OCR), FakeClassifier()

    found = _auto(pipeline, PAN_PAGE, classifier).extract_auto(CALLER, JPEG, None, [GREEN_CARD])

    assert found.match is not None
    assert (found.match.candidate.name, found.match.detected_by) == ("Pan card", DetectedBy.KEYWORDS)
    assert [t.document_type for t in pipeline.templates if t] == ["PAN_CARD"]
    assert (found.result, classifier.asked) == (BY_OCR, [])


def test_reads_with_the_users_template_found_by_its_labels_named_after_it() -> None:
    pipeline = FakePipeline(BY_OCR)

    found = _auto(pipeline, GREEN_PAGE).extract_auto(CALLER, JPEG, None, [GREEN_CARD])

    assert found.match is not None
    assert (found.match.candidate.id, found.match.candidate.field_ids) == ("backend-7", GREEN_CARD.field_ids)
    template = pipeline.templates[0]
    assert template is not None
    assert [f.key for f in template.fields] == ["surname", "uscis_number"]
    assert found.result.document_type == "green_card"


def test_reads_with_the_template_the_ai_names_when_the_keywords_fall_short() -> None:
    pipeline, classifier, workspace = FakePipeline(BY_OCR), FakeClassifier(), FakeWorkspace()

    found = _auto(pipeline, AADHAAR_HINT, classifier, workspace).extract_auto(CALLER, JPEG, PNG, [GREEN_CARD])

    assert found.match is not None
    assert (found.match.candidate.template.document_type, found.match.detected_by) == ("AADHAAR_CARD", DetectedBy.AI)
    assert list(classifier.asked[0]) == ["AADHAAR_CARD"]  # only the template with evidence on the page
    assert workspace.discarded == workspace.opened


@pytest.mark.parametrize(
    "classifier",
    [
        FakeClassifier(answer=("AADHAAR_CARD", 0.6)),  # not sure enough
        FakeClassifier(error=AiUnavailableError()),  # the model can't answer: generic, not a failure
        None,  # no AI in this build
    ],
)
def test_reads_the_generic_way_when_no_template_is_found(classifier: FakeClassifier | None) -> None:
    pipeline = FakePipeline(BY_OCR)

    found = _auto(pipeline, UNKNOWN_PAGE, classifier).extract_auto(CALLER, JPEG, None, [])

    assert (found.match, found.result, pipeline.templates) == (None, BY_AI, [])


def test_reads_the_generic_way_when_the_template_found_reads_next_to_nothing() -> None:
    pipeline, classifier = FakePipeline(READ_NOTHING), FakeClassifier()

    found = _auto(pipeline, AADHAAR_HINT, classifier).extract_auto(CALLER, JPEG, None, [])

    assert [t.document_type for t in pipeline.templates if t] == ["AADHAAR_CARD"]  # tried, then given up
    assert (found.match, found.result) == (None, BY_AI)
