"""When a page is too poor to read, and which error says so (IDP-03 ch. 2.6 stage 2, ch. 2.12)."""

import pytest

from extraction.core.error_codes import ErrorCode
from extraction.pipeline.domain.exceptions import QualityRejectedError, quality_error_for
from extraction.pipeline.domain.quality import QualityIssue, QualityScores, QualityThresholds, list_quality_issues

THRESHOLDS = QualityThresholds(
    blur_min=100.0,
    brightness_min=0.25,
    brightness_max=0.90,
    glare_max=0.04,
    min_short_side_px=900,
    require_document_edges=True,
)


def _scores(**changes: float | int | bool) -> QualityScores:
    values: dict[str, float | int | bool] = {
        "blur": 500.0,
        "brightness": 0.6,
        "glare": 0.0,
        "short_side_px": 1200,
        "edges_found": True,
    }
    values.update(changes)
    return QualityScores(**values)  # type: ignore[arg-type]  # the dict holds exactly the dataclass fields


def test_passes_the_page_when_every_score_is_inside_its_limit() -> None:
    assert list_quality_issues(_scores(), THRESHOLDS) == ()


@pytest.mark.parametrize(
    ("changes", "issue"),
    [
        ({"blur": 99.9}, QualityIssue.BLURRED),
        ({"brightness": 0.24}, QualityIssue.TOO_DARK),
        ({"brightness": 0.91}, QualityIssue.TOO_BRIGHT),
        ({"glare": 0.041}, QualityIssue.GLARE),
        ({"short_side_px": 899}, QualityIssue.LOW_RESOLUTION),
        ({"edges_found": False}, QualityIssue.EDGES_NOT_FOUND),
    ],
)
def test_refuses_the_page_when_one_score_is_just_outside_its_limit(
    changes: dict[str, float | int | bool], issue: QualityIssue
) -> None:
    assert list_quality_issues(_scores(**changes), THRESHOLDS) == (issue,)


@pytest.mark.parametrize(
    "changes",
    [{"blur": 100.0}, {"brightness": 0.25}, {"brightness": 0.90}, {"glare": 0.04}, {"short_side_px": 900}],
)
def test_passes_the_page_when_a_score_sits_exactly_on_its_limit(changes: dict[str, float | int | bool]) -> None:
    assert list_quality_issues(_scores(**changes), THRESHOLDS) == ()


def test_lists_every_failed_check_in_code_order_when_several_fail() -> None:
    issues = list_quality_issues(_scores(blur=1.0, glare=0.5, short_side_px=100, edges_found=False), THRESHOLDS)

    assert issues == (
        QualityIssue.BLURRED,
        QualityIssue.GLARE,
        QualityIssue.LOW_RESOLUTION,
        QualityIssue.EDGES_NOT_FOUND,
    )


def test_ignores_missing_edges_when_the_setting_does_not_require_them() -> None:
    relaxed = QualityThresholds(
        blur_min=100.0,
        brightness_min=0.25,
        brightness_max=0.90,
        glare_max=0.04,
        min_short_side_px=900,
        require_document_edges=False,
    )

    assert list_quality_issues(_scores(edges_found=False), relaxed) == ()


@pytest.mark.parametrize(
    ("issue", "code"),
    [
        (QualityIssue.BLURRED, ErrorCode.QUALITY_BLURRED),
        (QualityIssue.TOO_DARK, ErrorCode.QUALITY_TOO_DARK),
        (QualityIssue.TOO_BRIGHT, ErrorCode.QUALITY_GLARE),
        (QualityIssue.GLARE, ErrorCode.QUALITY_GLARE),
        (QualityIssue.LOW_RESOLUTION, ErrorCode.QUALITY_RESOLUTION),
        (QualityIssue.EDGES_NOT_FOUND, ErrorCode.QUALITY_EDGES_NOT_FOUND),
    ],
)
def test_maps_each_issue_to_its_error_code_when_a_page_is_refused(issue: QualityIssue, code: ErrorCode) -> None:
    error = quality_error_for(issue, 0)

    assert isinstance(error, QualityRejectedError)
    assert error.code is code


def test_counts_pages_from_one_in_the_message_when_the_third_page_is_refused() -> None:
    assert quality_error_for(QualityIssue.BLURRED, 2).detail.startswith("Page 3 ")
