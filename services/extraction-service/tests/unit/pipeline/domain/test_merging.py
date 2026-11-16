"""Recheck and deep-analysis merges, and the run row helpers."""

from extraction.pipeline.domain.merging import merge_analysis, merge_recheck, weak_keys
from extraction.pipeline.domain.runs import decision_counts, stage_durations


def _field(key: str, value: str | None, status: str = "ACCEPTED", confidence: float = 0.95, **extra) -> dict:
    return {"key": key, "value": value, "status": status, "confidence": confidence, **extra}


DEVICE = {
    "documentType": {"code": "PAN_CARD"},
    "fields": [_field("pan_number", "ABCDE1234F"), _field("name", "TEST PERSN", "NEEDS_REVIEW", 0.4)],
}
SERVER = {
    "documentType": {"code": "PAN_CARD"},
    "fields": [
        _field("pan_number", "ABCDE1234X"),
        _field("name", "TEST PERSON", rule={"id": "name", "passed": True}),
        _field("dob", "01/01/1990", "NEEDS_REVIEW", 0.5, rule={"id": "date", "passed": False}),
    ],
    "validation": {"failedRules": ["date"]},
}


def test_picks_unsettled_or_low_confidence_fields_when_none_are_named() -> None:
    assert weak_keys(DEVICE, asked=[], review_confidence=0.85) == ["name"]
    assert weak_keys(DEVICE, asked=["pan_number", "pan_number"], review_confidence=0.85) == ["pan_number"]


def test_replaces_only_weak_fields_adds_new_ones_and_lists_what_changed() -> None:
    merged = merge_recheck(DEVICE, SERVER, asked=[], review_confidence=0.85)

    assert [(f["key"], f["value"]) for f in merged["fields"]] == [
        ("pan_number", "ABCDE1234F"),
        ("name", "TEST PERSON"),
        ("dob", "01/01/1990"),
    ]
    assert merged["recheck"] == {"source": "ON_DEVICE", "checked": ["name"], "changed": ["name"]}
    assert merged["validation"] == {"fieldsTotal": 3, "accepted": 2, "needsReview": 1, "failedRules": ["date"]}


def test_takes_the_server_result_when_the_document_type_differs() -> None:
    merged = merge_recheck(
        DEVICE, SERVER | {"documentType": {"code": "AADHAAR_CARD"}}, asked=[], review_confidence=0.85
    )

    assert merged["fields"] == SERVER["fields"]
    assert merged["recheck"]["documentTypeChanged"] is True


def test_adds_analysis_without_overwriting_accepted_fields() -> None:
    analysis = {"fields": [{"key": "pan_number", "value": "X"}, {"key": "name", "value": "Y"}], "summary": "S"}

    merged = merge_analysis(DEVICE, analysis, model="auto", prompt_version="page-analysis.v1", question=None)

    assert merged["fields"] == DEVICE["fields"]
    assert [f["key"] for f in merged["analysis"]["fields"]] == ["name"]
    assert merged["provenance"]["models"] == [{"id": "auto", "promptVersion": "page-analysis.v1"}]


def test_times_each_stage_until_the_next_one_starts() -> None:
    assert stage_durations([("READ", 1.0), ("PREPARE", 0.5)], finished=1.25) == {"PREPARE": 500, "READ": 250}


def test_counts_fields_by_source_and_status() -> None:
    result = {"fields": [{"source": "OCR_AGREE", "status": "ACCEPTED"}, {"source": None, "status": "NEEDS_REVIEW"}]}

    assert decision_counts(result) == {"OCR_AGREE/ACCEPTED": 1, "NONE/NEEDS_REVIEW": 1}
