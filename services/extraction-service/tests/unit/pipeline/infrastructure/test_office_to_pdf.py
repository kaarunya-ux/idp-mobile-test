"""Office documents to PDF with LibreOffice: a fake ``soffice`` for every path, and the real one when installed."""

import io
import shutil
import stat
import zipfile
from datetime import timedelta
from pathlib import Path

import pypdfium2 as pdfium
import pytest

from extraction.pipeline.application.services.run_extraction import readable
from extraction.pipeline.domain.exceptions import FileRejectedError
from extraction.pipeline.infrastructure.digital.office_to_pdf import (
    LibreOfficeNotInstalledError,
    LibreOfficePdfConverter,
)
from extraction.pipeline.infrastructure.digital.pdf_text_reader import PdfiumTextReader

_DOCUMENT = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>
<w:p><w:r><w:t>INCOME TAX DEPARTMENT</w:t></w:r></w:p>
<w:p><w:r><w:t>Permanent Account Number Card</w:t></w:r></w:p>
<w:p><w:r><w:t>ABCPE1234F</w:t></w:r></w:p>
</w:body></w:document>"""
_TYPES = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
<Default Extension="xml" ContentType="application/xml"/>
<Override PartName="/word/document.xml"
 ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>
</Types>"""
_RELS = """<?xml version="1.0" encoding="UTF-8" standalone="yes"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
<Relationship Id="rId1" Target="word/document.xml"
 Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument"/>
</Relationships>"""


def _docx() -> bytes:
    """A minimal Word document with made-up text, built by hand (no python-docx needed)."""
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _TYPES)
        archive.writestr("_rels/.rels", _RELS)
        archive.writestr("word/document.xml", _DOCUMENT)
    return buffer.getvalue()


def _pdf() -> bytes:
    document = pdfium.PdfDocument.new()
    document.new_page(200, 100)
    buffer = io.BytesIO()
    document.save(buffer)
    return buffer.getvalue()


def _fake_soffice(tmp_path: Path, script: str) -> str:
    binary = tmp_path / "fake-soffice"
    binary.write_text(f"#!/bin/sh\n{script}\n")
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return str(binary)


def test_leaves_pdfs_and_images_alone() -> None:
    converter = LibreOfficePdfConverter(binary="no-such-binary", timeout=timedelta(seconds=5))

    assert converter.to_pdf(_pdf()) is None
    assert readable(b"\x89PNG\r\n\x1a\n" + bytes(20), converter) == b"\x89PNG\r\n\x1a\n" + bytes(20)
    assert readable(b"anything", None) == b"anything"


def test_returns_the_pdf_libreoffice_writes(tmp_path: Path) -> None:
    pdf = tmp_path / "made.pdf"
    pdf.write_bytes(_pdf())
    # the last two arguments are the output folder and the input file
    script = f'for a; do out="$prev"; prev="$a"; done; mkdir -p "$out"; cp {pdf} "$out/document.pdf"'
    converter = LibreOfficePdfConverter(binary=_fake_soffice(tmp_path, script), timeout=timedelta(seconds=10))

    assert converter.to_pdf(_docx()) == pdf.read_bytes()


def test_refuses_the_file_when_libreoffice_writes_nothing(tmp_path: Path) -> None:
    converter = LibreOfficePdfConverter(binary=_fake_soffice(tmp_path, "exit 0"), timeout=timedelta(seconds=10))

    with pytest.raises(FileRejectedError):
        converter.to_pdf(_docx())


def test_refuses_the_file_when_libreoffice_takes_too_long(tmp_path: Path) -> None:
    converter = LibreOfficePdfConverter(binary=_fake_soffice(tmp_path, "sleep 30"), timeout=timedelta(seconds=5))

    with pytest.raises(FileRejectedError):
        converter.to_pdf(_docx())


def test_raises_a_deployment_error_when_libreoffice_is_missing() -> None:
    converter = LibreOfficePdfConverter(binary="no-such-soffice", timeout=timedelta(seconds=5))

    with pytest.raises(LibreOfficeNotInstalledError):
        converter.to_pdf(_docx())


@pytest.mark.skipif(shutil.which("soffice") is None, reason="LibreOffice is not installed here")
def test_reads_a_word_document_through_its_pdf_text_layer_with_the_real_libreoffice() -> None:
    converter = LibreOfficePdfConverter(binary="soffice", timeout=timedelta(seconds=120))

    pdf = converter.to_pdf(_docx())

    assert pdf is not None
    page = PdfiumTextReader(dpi=150).read_first_page(pdf)
    assert page is not None
    assert "ABCPE1234F" in page.text
    assert "Permanent Account Number" in page.text
