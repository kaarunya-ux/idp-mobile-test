"""The second document set: every expected value is on its document, and the pictures do not change between builds."""

import hashlib
import io
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "prompt-quality"))
import documents  # noqa: E402


def _hash(image) -> str:  # noqa: ANN001
    buffer = io.BytesIO()
    image.save(buffer, "PNG")
    return hashlib.sha256(buffer.getvalue()).hexdigest()


def test_prints_every_expected_value_of_a_business_document_on_the_document() -> None:
    for doc in documents.businesses():
        printed = " ".join(w["text"] for w in doc.ocr_words).lower()
        for key, _label, _kind, value in doc.fields:
            assert value.lower().split()[0] in printed, (doc.name, key)


def test_gives_the_analysis_test_a_wrong_value_and_a_missing_field_that_both_exist() -> None:
    for doc in documents.businesses():
        keys = {k for k, _, _, _ in doc.fields}
        assert doc.wrong[0] in keys
        assert doc.missing in keys
        assert doc.wrong[0] != doc.missing


def test_draws_the_same_pictures_every_time() -> None:
    first = [_hash(p.image) for p in documents.pages()] + [_hash(c.image) for c in documents.cards()]
    second = [_hash(p.image) for p in documents.pages()] + [_hash(c.image) for c in documents.cards()]

    assert first == second


def test_keeps_the_page_kinds_the_judging_relies_on() -> None:
    assert {p.kind for p in documents.pages()} == {"digital", "scan", "hostile"}
    assert any(p.tables for p in documents.pages())
    assert all(p.headings for p in documents.pages())


def test_uses_an_aadhaar_number_that_passes_the_check_digit() -> None:
    number = documents.aadhaar_number().replace(" ", "")

    assert len(number) == 12
    assert documents._verhoeff_ok(number)  # noqa: SLF001
