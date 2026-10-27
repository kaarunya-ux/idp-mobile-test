"""Merging PDFs: every page in input order, copied as is; locked, damaged or oversized inputs reported with a code."""

from collections.abc import Callable
from pathlib import Path

import pikepdf
import pytest

from conversion.conversion.domain.exceptions import CorruptPdfError, LimitExceededError, PasswordRequiredError
from conversion.conversion.infrastructure.converters.pdf_merge import PikepdfMerger

MakePdf = Callable[..., Path]


def _page_sizes(path: Path) -> list[tuple[int, int]]:
    with pikepdf.open(path) as pdf:
        return [(round(float(page.mediabox[2])), round(float(page.mediabox[3]))) for page in pdf.pages]


def test_writes_every_page_in_input_order_when_pdfs_are_merged(make_pdf: MakePdf, tmp_path: Path) -> None:
    first = make_pdf("first.pdf", sizes=[(200, 100), (210, 100)])
    second = make_pdf("second.pdf", sizes=[(100, 300)])
    output = tmp_path / "merged.pdf"

    pages = PikepdfMerger(max_pages=10).merge([first, second, first], output)

    assert pages == 5
    assert _page_sizes(output) == [(200, 100), (210, 100), (100, 300), (200, 100), (210, 100)]


def test_reports_cnv_422_pdf_001_when_an_input_is_encrypted(make_pdf: MakePdf, tmp_path: Path) -> None:
    locked = make_pdf("locked.pdf", sizes=[(200, 100)], password="secret")

    with pytest.raises(PasswordRequiredError):
        PikepdfMerger(max_pages=10).merge([make_pdf("ok.pdf", sizes=[(200, 100)]), locked], tmp_path / "out.pdf")


def test_reports_cnv_422_pdf_001_when_an_input_opens_without_a_password_but_is_encrypted(
    make_pdf: MakePdf, tmp_path: Path
) -> None:
    restricted = make_pdf("restricted.pdf", sizes=[(200, 100)])
    with pikepdf.open(restricted, allow_overwriting_input=True) as pdf:  # owner password only: no copying
        pdf.save(
            restricted, encryption=pikepdf.Encryption(user="", owner="owner", allow=pikepdf.Permissions(extract=False))
        )

    with pytest.raises(PasswordRequiredError):
        PikepdfMerger(max_pages=10).merge([restricted], tmp_path / "out.pdf")


def test_reports_cnv_422_pdf_002_when_an_input_is_damaged(tmp_path: Path) -> None:
    damaged = tmp_path / "damaged.pdf"
    damaged.write_bytes(b"%PDF-1.7 this is not a pdf")

    with pytest.raises(CorruptPdfError):
        PikepdfMerger(max_pages=10).merge([damaged], tmp_path / "out.pdf")


def test_refuses_with_cnv_422_limit_001_when_the_merge_would_have_too_many_pages(
    make_pdf: MakePdf, tmp_path: Path
) -> None:
    three = make_pdf("three.pdf", sizes=[(200, 100)] * 3)

    with pytest.raises(LimitExceededError, match="more than 5"):
        PikepdfMerger(max_pages=5).merge([three, three], tmp_path / "out.pdf")
