"""Reading the edit plan: the IDP-03 ch. 3.5 example reads in full; anything malformed is CNV-422-PLAN-001."""

from typing import Any

import pytest

from conversion.conversion.domain.edit_plan import (
    BlankPage,
    CompressPreset,
    PageSize,
    PlaceImage,
    Protect,
    SourcePage,
    SplitMode,
    Watermark,
)
from conversion.conversion.domain.edit_plan_parser import parse_edit_plan
from conversion.conversion.domain.exceptions import InvalidPlanError, LimitExceededError

SPEC_EXAMPLE: dict[str, Any] = {
    "schemaVersion": 1,
    "password": None,
    "pages": [
        {"source": {"documentId": "self", "page": 2}, "rotate": 90},
        {"source": {"documentId": "self", "page": 1}, "rotate": 0},
        {"blank": {"size": "A4", "orientation": "PORTRAIT"}},
        {"source": {"documentId": "0192c", "page": 1}, "rotate": 270},
    ],
    "operations": [
        {
            "type": "ANNOTATE_TEXT",
            "page": 0,
            "box": [0.1, 0.1, 0.3, 0.05],
            "text": "Approved",
            "fontSize": 12,
            "color": "#1F2937",
        },
        {"type": "HIGHLIGHT", "page": 0, "boxes": [[0.1, 0.3, 0.5, 0.02]], "color": "#FDE047"},
        {"type": "INK", "page": 1, "paths": [[[0.1, 0.1], [0.12, 0.13]]], "width": 2, "color": "#DC2626"},
        {"type": "SIGNATURE", "page": 3, "box": [0.6, 0.85, 0.25, 0.08], "imageKey": "idp-derived/acc/sig.png"},
        {"type": "WATERMARK", "text": "CONFIDENTIAL", "opacity": 0.15, "rotation": 45, "pages": "ALL"},
        {"type": "PAGE_NUMBERS", "position": "BOTTOM_CENTER", "format": "Page {n} of {total}", "startAt": 1},
        {"type": "REDACT_AREA", "page": 0, "boxes": [[0.4, 0.5, 0.2, 0.03]]},
        {"type": "REDACT_TEXT", "query": "ABCDE1234F", "matchCase": False},
        {"type": "FILL_FORM", "fields": {"applicant_name": "Asha Rao"}, "flatten": True},
        {"type": "REPLACE_TEXT", "page": 0, "find": "2025", "replace": "2026"},
        {"type": "INSERT_IMAGE", "page": 2, "box": [0.1, 0.1, 0.3, 0.2], "imageKey": "idp-derived/acc/logo.png"},
        {"type": "METADATA", "title": "Signed contract", "author": None},
        {"type": "PROTECT", "userPassword": "enc-u", "ownerPassword": "enc-o", "allowPrint": True, "allowCopy": False},
        {"type": "COMPRESS", "preset": "BALANCED"},
    ],
    "output": {"split": {"mode": "RANGES", "ranges": ["1-3", "4"]}},
}


def test_reads_every_page_and_operation_when_the_plan_is_the_spec_example() -> None:
    plan = parse_edit_plan(SPEC_EXAMPLE)

    assert plan.pages is not None
    assert plan.pages[0] == SourcePage(document_id="self", page=2, rotate=90)
    assert plan.pages[2] == BlankPage(size=PageSize.A4, landscape=False)
    assert len(plan.operations) == 14
    assert plan.document_ids == {"self", "0192c"}
    assert plan.image_keys == {"idp-derived/acc/sig.png", "idp-derived/acc/logo.png"}
    assert isinstance(plan.operations[3], PlaceImage)
    assert isinstance(plan.operations[4], Watermark)
    assert plan.operations[4].pages is None
    assert plan.operations[12] == Protect(
        user_password="enc-u", owner_password="enc-o", allow_print=True, allow_copy=False
    )
    assert plan.operations[13].preset is CompressPreset.BALANCED  # type: ignore[union-attr]
    assert plan.split is not None
    assert plan.split.mode is SplitMode.RANGES


def test_keeps_the_pages_unchanged_when_the_plan_lists_none() -> None:
    plan = parse_edit_plan({"schemaVersion": 1, "operations": []})

    assert (plan.pages, plan.operations, plan.split, plan.password) == (None, (), None, None)


@pytest.mark.parametrize(
    ("plan", "message"),
    [
        (None, "must be an object"),
        ({"schemaVersion": 2}, "version 1"),
        ({"schemaVersion": 1, "pages": [{"source": {"documentId": "self", "page": 1}, "rotate": 45}]}, "rotation"),
        ({"schemaVersion": 1, "pages": [{"source": {"documentId": "self", "page": 0}}]}, "1 or more"),
        ({"schemaVersion": 1, "operations": [{"type": "EXPLODE"}]}, "Unknown operation"),
        (
            {"schemaVersion": 1, "operations": [{"type": "HIGHLIGHT", "page": 0, "boxes": [[0.5, 0, 0.6, 0.1]]}]},
            "fit on the page",
        ),
        ({"schemaVersion": 1, "operations": [{"type": "HIGHLIGHT", "page": 0, "boxes": []}]}, "at least one box"),
        (
            {"schemaVersion": 1, "operations": [{"type": "INK", "page": 0, "paths": [[[0.1]]], "color": "#000"}]},
            "point",
        ),
        (
            {
                "schemaVersion": 1,
                "operations": [{"type": "ANNOTATE_TEXT", "page": 0, "box": [0, 0, 1, 1], "text": "x", "color": "red"}],
            },
            "colour",
        ),
        (
            {
                "schemaVersion": 1,
                "operations": [{"type": "ANNOTATE_TEXT", "page": -1, "box": [0, 0, 1, 1], "text": "x"}],
            },
            "page index",
        ),
        ({"schemaVersion": 1, "operations": [{"type": "WATERMARK", "text": "x", "pages": "1-"}]}, "Page range"),
        ({"schemaVersion": 1, "operations": [{"type": "COMPRESS", "preset": "MAXIMUM"}]}, "preset"),
        ({"schemaVersion": 1, "output": {"split": {"mode": "RANGES", "ranges": []}}}, "at least one range"),
    ],
)
def test_reports_cnv_422_plan_001_when_the_plan_is_malformed(plan: object, message: str) -> None:
    with pytest.raises(InvalidPlanError, match=message) as caught:
        parse_edit_plan(plan)

    assert caught.value.code == "CNV-422-PLAN-001"


def test_refuses_with_cnv_422_limit_001_when_the_plan_has_too_many_operations() -> None:
    operations = [{"type": "COMPRESS"}] * 501

    with pytest.raises(LimitExceededError):
        parse_edit_plan({"schemaVersion": 1, "operations": operations})
