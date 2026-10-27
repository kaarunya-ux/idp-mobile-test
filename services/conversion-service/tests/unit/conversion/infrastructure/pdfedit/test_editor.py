"""The PDF editor on real files: page assembly, every operation, redaction that really removes, splits, passwords."""

from collections.abc import Callable
from pathlib import Path
from typing import Any

import pikepdf
import pypdfium2 as pdfium
import pytest

from conversion.conversion.domain.edit_plan import Operation
from conversion.conversion.domain.edit_plan_parser import parse_edit_plan
from conversion.conversion.domain.exceptions import (
    InvalidPlanError,
    LimitExceededError,
    PasswordRequiredError,
    WrongPasswordError,
)
from conversion.conversion.domain.ports import EditJob
from conversion.conversion.infrastructure.pdfedit.editor import PikepdfEditor, operation_types

MakeTextPdf = Callable[..., Path]
editor = PikepdfEditor(max_pages=50, redaction_dpi=100)


def _edit(documents: dict[str, Path], plan: dict[str, Any], out: Path, images: dict[str, Path] | None = None):
    out.mkdir(exist_ok=True)
    job = EditJob(plan=parse_edit_plan({"schemaVersion": 1, **plan}), documents=documents, images=images or {})
    return editor.edit(job, out)


def _texts(path: Path, password: str | None = None) -> list[str]:
    document = pdfium.PdfDocument(path, password=password)
    try:
        texts = []
        for page in document:
            text_page = page.get_textpage()
            texts.append(text_page.get_text_range().strip())
            text_page.close()
            page.close()
        return texts
    finally:
        document.close()


def _pages(path: Path) -> list[tuple[tuple[int, int], int]]:
    with pikepdf.open(path) as pdf:
        return [
            ((round(float(p.mediabox[2])), round(float(p.mediabox[3]))), int(p.obj.get("/Rotate", 0)))
            for p in pdf.pages
        ]


def test_has_a_step_for_every_operation_type() -> None:
    assert operation_types() == set(Operation.__value__.__args__)


def test_assembles_pages_in_the_plan_order_with_rotation_blanks_and_pages_from_another_document(
    make_text_pdf: MakeTextPdf, tmp_path: Path
) -> None:
    own = make_text_pdf("own.pdf", ["one", "two", "three"])
    other = make_text_pdf("other.pdf", ["borrowed"], size=(100, 100))
    plan = {
        "pages": [
            {"source": {"documentId": "self", "page": 3}, "rotate": 90},
            {"source": {"documentId": "self", "page": 1}},
            {"blank": {"size": "A4", "orientation": "LANDSCAPE"}},
            {"source": {"documentId": "doc-2", "page": 1}},
            {"source": {"documentId": "self", "page": 1}, "rotate": 180},  # the same page twice
        ]
    }

    (output,), pages = _edit({"self": own, "doc-2": other}, plan, tmp_path / "out")

    assert pages == 5
    assert _texts(output) == ["three", "one", "", "borrowed", "one"]
    assert _pages(output) == [((300, 200), 90), ((300, 200), 0), ((842, 595), 0), ((100, 100), 0), ((300, 200), 180)]


@pytest.mark.parametrize(
    ("operation", "expected_text"),
    [
        (
            {"type": "ANNOTATE_TEXT", "page": 0, "box": [0.1, 0.1, 0.5, 0.2], "text": "Approved", "fontSize": 12},
            "Approved",
        ),
        ({"type": "WATERMARK", "text": "CONFIDENTIAL", "opacity": 0.2, "rotation": 45, "pages": "ALL"}, "CONFIDENTIAL"),
        (
            {"type": "PAGE_NUMBERS", "position": "BOTTOM_RIGHT", "format": "Page {n} of {total}", "startAt": 1},
            "Page 1 of 2",
        ),
    ],
)
def test_draws_text_overlays_the_reader_can_find_when_text_operations_run(
    make_text_pdf: MakeTextPdf, tmp_path: Path, operation: dict[str, Any], expected_text: str
) -> None:
    own = make_text_pdf("own.pdf", ["body", "more"])

    (output,), _ = _edit({"self": own}, {"operations": [operation]}, tmp_path / "out")

    first_page = _texts(output)[0]
    assert "body" in first_page
    assert expected_text in first_page


def test_draws_on_the_page_as_shown_when_the_page_is_rotated(make_text_pdf: MakeTextPdf, tmp_path: Path) -> None:
    own = make_text_pdf("own.pdf", ["body"])
    plan = {
        "pages": [{"source": {"documentId": "self", "page": 1}, "rotate": 90}],
        "operations": [{"type": "ANNOTATE_TEXT", "page": 0, "box": [0.05, 0.05, 0.9, 0.2], "text": "Top"}],
    }

    (output,), _ = _edit({"self": own}, plan, tmp_path / "out")

    document = pdfium.PdfDocument(output)
    page = document[0]
    text_page = page.get_textpage()
    hit = text_page.search("Top").get_next()
    assert hit is not None
    left, bottom, _right, _top = text_page.get_rect(0) if text_page.count_rects(*hit) else (0, 0, 0, 0)
    text_page.close()
    page.close()
    document.close()
    # shown as 200 wide x 300 tall; "Top" sits near the top-left of what the user sees, which is near the
    # bottom-left of the unrotated page (x small, y small in its own space)
    assert left < 60
    assert bottom < 100


def test_adds_drawing_and_image_content_when_highlight_ink_and_image_operations_run(
    make_text_pdf: MakeTextPdf, make_image, tmp_path: Path
) -> None:
    own = make_text_pdf("own.pdf", ["body"])
    signature = make_image("sig.png", size=(80, 40), mode="RGBA", color=(0, 0, 255, 128))
    operations = [
        {"type": "HIGHLIGHT", "page": 0, "boxes": [[0.1, 0.4, 0.5, 0.1]], "color": "#FDE047"},
        {"type": "INK", "page": 0, "paths": [[[0.1, 0.1], [0.2, 0.3], [0.3, 0.1]]], "width": 2, "color": "#DC2626"},
        {"type": "SIGNATURE", "page": 0, "box": [0.6, 0.7, 0.3, 0.2], "imageKey": "idp-derived/acc/sig.png"},
    ]

    (output,), _ = _edit(
        {"self": own}, {"operations": operations}, tmp_path / "out", {"idp-derived/acc/sig.png": signature}
    )

    with pikepdf.open(output) as pdf:
        page = pdf.pages[0]
        images = list(page.get_images().values())
        has_mask = ["/SMask" in image for image in images]  # read while the PDF is open
        content = b"".join(stream.read_bytes() for stream in page.obj.Contents)
    assert has_mask == [True]  # one image, and the signature keeps its transparency
    assert b" re" in content  # the highlight box
    assert b" l" in content  # the ink stroke
    assert _texts(output) == ["body"]


def test_removes_every_trace_of_a_term_and_keeps_other_pages_as_text_when_text_is_redacted(
    make_text_pdf: MakeTextPdf, tmp_path: Path
) -> None:
    own = make_text_pdf("own.pdf", ["PAN ABCDE1234F here", "nothing secret"])

    (output,), _ = _edit(
        {"self": own},
        {"operations": [{"type": "REDACT_TEXT", "query": "abcde1234f", "matchCase": False}]},
        tmp_path / "out",
    )

    assert _texts(output) == ["", "nothing secret"]  # the redacted page is now a picture
    assert b"ABCDE1234F" not in output.read_bytes()


def test_turns_the_page_into_a_picture_when_an_area_is_redacted(make_text_pdf: MakeTextPdf, tmp_path: Path) -> None:
    own = make_text_pdf("own.pdf", ["visible", "kept"])

    (output,), _ = _edit(
        {"self": own},
        {"operations": [{"type": "REDACT_AREA", "page": 0, "boxes": [[0.0, 0.4, 0.6, 0.2]]}]},
        tmp_path / "out",
    )

    assert _texts(output) == ["", "kept"]
    assert _pages(output)[0] == ((300, 200), 0)


def test_fills_and_flattens_a_form_field_when_the_plan_fills_the_form(
    make_text_pdf: MakeTextPdf, tmp_path: Path
) -> None:
    own = make_text_pdf("form.pdf", ["Applicant"], form_field="applicant_name")
    fill = {"type": "FILL_FORM", "fields": {"applicant_name": "Asha Rao"}, "flatten": True}

    (output,), _ = _edit({"self": own}, {"operations": [fill]}, tmp_path / "out")

    with pikepdf.open(output) as pdf:
        assert "/AcroForm" not in pdf.Root
    assert "Asha Rao" in _texts(output)[0]


def test_reports_cnv_422_plan_001_when_a_form_field_does_not_exist(make_text_pdf: MakeTextPdf, tmp_path: Path) -> None:
    own = make_text_pdf("form.pdf", ["Applicant"], form_field="applicant_name")

    with pytest.raises(InvalidPlanError, match="no field named"):
        _edit({"self": own}, {"operations": [{"type": "FILL_FORM", "fields": {"nope": "x"}}]}, tmp_path / "out")


def test_replaces_stored_text_so_the_new_words_are_real_text(make_text_pdf: MakeTextPdf, tmp_path: Path) -> None:
    own = make_text_pdf("own.pdf", ["Valid until 2025"])
    replace = {"type": "REPLACE_TEXT", "page": 0, "find": "2025", "replace": "2026"}

    (output,), _ = _edit({"self": own}, {"operations": [replace]}, tmp_path / "out")

    assert _texts(output) == ["Valid until 2026"]


def test_reports_cnv_422_plan_001_when_the_text_to_replace_is_not_on_the_page(
    make_text_pdf: MakeTextPdf, tmp_path: Path
) -> None:
    own = make_text_pdf("own.pdf", ["Valid until 2025"])

    with pytest.raises(InvalidPlanError, match="can't be replaced"):
        _edit({"self": own}, {"operations": [{"type": "REPLACE_TEXT", "page": 0, "find": "1999"}]}, tmp_path / "out")


def test_sets_metadata_compresses_and_protects_when_saving(make_text_pdf: MakeTextPdf, tmp_path: Path) -> None:
    own = make_text_pdf("own.pdf", ["body"])
    operations = [
        {"type": "METADATA", "title": "Signed contract", "author": None},
        {"type": "COMPRESS", "preset": "BALANCED"},
        {"type": "PROTECT", "userPassword": "open", "ownerPassword": "owner", "allowPrint": True, "allowCopy": False},
    ]

    (output,), _ = _edit({"self": own}, {"operations": operations}, tmp_path / "out")

    with pytest.raises(pikepdf.PasswordError):
        pikepdf.open(output)
    with pikepdf.open(output, password="open") as pdf:
        assert str(pdf.docinfo["/Title"]) == "Signed contract"
        assert pdf.allow.extract is False
        assert pdf.allow.print_highres is True


@pytest.mark.parametrize(
    ("split", "expected"),
    [
        ({"mode": "RANGES", "ranges": ["1-2", "4"]}, [["a", "b"], ["d"]]),
        ({"mode": "EVERY", "pages": 3}, [["a", "b", "c"], ["d"]]),
    ],
)
def test_writes_one_file_per_part_when_the_plan_splits_the_output(
    make_text_pdf: MakeTextPdf, tmp_path: Path, split: dict[str, Any], expected: list[list[str]]
) -> None:
    own = make_text_pdf("own.pdf", ["a", "b", "c", "d"])

    outputs, pages = _edit({"self": own}, {"output": {"split": split}}, tmp_path / "out")

    assert [_texts(path) for path in outputs] == expected
    assert [path.name for path in outputs] == [f"edited-part-0{n}.pdf" for n in range(1, len(expected) + 1)]
    assert pages == 4


def test_opens_a_locked_pdf_with_the_plan_password_and_saves_it_unlocked(
    make_text_pdf: MakeTextPdf, tmp_path: Path
) -> None:
    own = make_text_pdf("own.pdf", ["secret body"])
    with pikepdf.open(own, allow_overwriting_input=True) as pdf:
        pdf.save(own, encryption=pikepdf.Encryption(user="pw", owner="owner-pw"))

    (output,), _ = _edit({"self": own}, {"password": "pw"}, tmp_path / "out")

    assert _texts(output) == ["secret body"]
    with pytest.raises(WrongPasswordError):
        _edit({"self": own}, {"password": "wrong"}, tmp_path / "out2")
    with pytest.raises(PasswordRequiredError):
        _edit({"self": own}, {}, tmp_path / "out3")


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        ({"pages": [{"source": {"documentId": "self", "page": 9}}]}, "no page 9"),
        ({"operations": [{"type": "ANNOTATE_TEXT", "page": 4, "box": [0, 0, 0.1, 0.1], "text": "x"}]}, "doesn't exist"),
        ({"operations": [{"type": "ANNOTATE_TEXT", "page": 0, "box": [0, 0, 0.1, 0.1], "text": "नाम"}]}, "Latin"),
    ],
)
def test_reports_cnv_422_plan_001_when_the_plan_does_not_fit_the_document(
    make_text_pdf: MakeTextPdf, tmp_path: Path, plan: dict[str, Any], message: str
) -> None:
    own = make_text_pdf("own.pdf", ["one"])

    with pytest.raises(InvalidPlanError, match=message):
        _edit({"self": own}, plan, tmp_path / "out")


def test_refuses_with_cnv_422_limit_001_when_the_edit_makes_too_many_pages(
    make_text_pdf: MakeTextPdf, tmp_path: Path
) -> None:
    own = make_text_pdf("own.pdf", ["one"])
    pages = [{"source": {"documentId": "self", "page": 1}}] * 3

    with pytest.raises(LimitExceededError):
        PikepdfEditor(max_pages=2, redaction_dpi=100).edit(
            EditJob(plan=parse_edit_plan({"schemaVersion": 1, "pages": pages}), documents={"self": own}, images={}),
            tmp_path,
        )
