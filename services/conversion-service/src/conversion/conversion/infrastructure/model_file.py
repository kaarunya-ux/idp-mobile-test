"""The document model as a file, so steps can hand it to each other (IDP-03 ch. 3.4).

Steps pass files, not objects: in step 4 the steps of one conversion may run in different worker processes. The
model is stored as versioned JSON and validated by Pydantic when read back (OBJ-01: DTOs stay in this module).
"""

from pathlib import Path
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from conversion.conversion.domain.document_model import (
    Alignment,
    Block,
    DocumentModel,
    Page,
    Table,
    TextBlock,
    TextKind,
)

MODEL_SUFFIX = ".model.json"


class _Dto(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class _TextBlockDto(_Dto):
    type: Literal["text"] = "text"
    text: str
    kind: TextKind
    level: int
    alignment: Alignment
    bold: bool


class _TableDto(_Dto):
    type: Literal["table"] = "table"
    rows: list[list[str]]
    header_rows: int


class _PageDto(_Dto):
    name: str | None
    blocks: list[Annotated[_TextBlockDto | _TableDto, Field(discriminator="type")]]


class _DocumentModelDto(_Dto):
    schema_version: Literal[1] = 1
    pages: list[_PageDto]


def model_path(output_dir: Path, source: Path) -> Path:
    """Where a reader writes the model of ``source``: ``<output_dir>/<source stem>.model.json``."""
    return output_dir / f"{source.stem}{MODEL_SUFFIX}"


def source_stem(model_file: Path) -> str:
    """The original file's stem, so writers name their output after it: ``letter.model.json`` -> ``letter``."""
    name = model_file.name
    return name.removesuffix(MODEL_SUFFIX) if name.endswith(MODEL_SUFFIX) else model_file.stem


def write_model(model: DocumentModel, path: Path) -> None:
    dto = _DocumentModelDto(
        pages=[_PageDto(name=page.name, blocks=[_to_dto(b) for b in page.blocks]) for page in model.pages]
    )
    path.write_text(dto.model_dump_json(), encoding="utf-8")


def read_model(path: Path) -> DocumentModel:
    dto = _DocumentModelDto.model_validate_json(path.read_text(encoding="utf-8"))
    return DocumentModel(
        pages=tuple(Page(name=page.name, blocks=tuple(_to_block(b) for b in page.blocks)) for page in dto.pages)
    )


def _to_dto(block: Block) -> _TextBlockDto | _TableDto:
    if isinstance(block, Table):
        return _TableDto(rows=[list(row) for row in block.rows], header_rows=block.header_rows)
    return _TextBlockDto(
        text=block.text, kind=block.kind, level=block.level, alignment=block.alignment, bold=block.bold
    )


def _to_block(dto: _TextBlockDto | _TableDto) -> Block:
    if isinstance(dto, _TableDto):
        return Table(rows=tuple(tuple(row) for row in dto.rows), header_rows=dto.header_rows)
    return TextBlock(text=dto.text, kind=dto.kind, level=dto.level, alignment=dto.alignment, bold=dto.bold)
