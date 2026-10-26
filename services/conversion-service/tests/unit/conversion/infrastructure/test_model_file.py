"""The model file round-trips exactly and refuses anything malformed (OBJ-01, DB-09)."""

from pathlib import Path

import pydantic
import pytest

from conversion.conversion.domain.document_model import (
    Alignment,
    DocumentModel,
    Page,
    Table,
    TextBlock,
    TextKind,
)
from conversion.conversion.infrastructure.model_file import model_path, read_model, source_stem, write_model


def test_reads_back_the_same_model_when_written(tmp_path: Path) -> None:
    model = DocumentModel(
        pages=(
            Page(
                name="Sales",
                blocks=(
                    TextBlock(text="नमस्ते", kind=TextKind.HEADING, level=2, alignment=Alignment.CENTER, bold=True),
                    Table(rows=(("item", "qty"), ("Paper", "10")), header_rows=1),
                ),
            ),
            Page(blocks=()),
        )
    )
    path = tmp_path / "doc.model.json"

    write_model(model, path)

    assert read_model(path) == model


def test_refuses_the_file_when_it_holds_unexpected_fields(tmp_path: Path) -> None:
    path = tmp_path / "doc.model.json"
    path.write_text('{"schema_version": 1, "pages": [], "extra": true}', encoding="utf-8")

    with pytest.raises(pydantic.ValidationError):  # a corrupt intermediate file is our bug, so it isn't disguised
        read_model(path)


def test_names_model_files_after_the_original_when_given_a_source(tmp_path: Path) -> None:
    path = model_path(tmp_path, Path("in/letter.docx"))

    assert path == tmp_path / "letter.model.json"
    assert source_stem(path) == "letter"
    assert source_stem(Path("other.json")) == "other"
