"""``PDF_EDIT`` with pikepdf and PDFium (IDP-03 ch. 3.5): ``PdfEditorPort``.

Order of work:

1. Open the document (with the plan's password if it has one) and the documents it takes pages from.
2. Fill forms first: field values belong to the document, not to page positions, so they survive any reordering.
3. Assemble the pages in the plan's order, each with its absolute rotation; omitted pages are gone.
4. Apply the other operations in the plan's order. Metadata, compression and the password are recorded and used
   when saving.
5. Check that no redacted search term is left, split if asked, and save each part.
"""

import io
from collections.abc import Callable, Mapping, Sequence
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pikepdf

from conversion.conversion.domain.edit_plan import (
    PAGE_SIZES_PT,
    SELF,
    AnnotateText,
    BlankPage,
    Compress,
    FillForm,
    Highlight,
    Ink,
    Metadata,
    Operation,
    PageNumbers,
    PageSpec,
    PlaceImage,
    Protect,
    RedactArea,
    RedactText,
    ReplaceText,
    Split,
    Watermark,
)
from conversion.conversion.domain.exceptions import (
    CorruptPdfError,
    InvalidPlanError,
    LimitExceededError,
    PasswordRequiredError,
    WrongPasswordError,
)
from conversion.conversion.domain.ports import EditJob
from conversion.conversion.infrastructure.pdfedit import drawing, forms, output, redaction

EDITED_FILE_STEM = "edited"


class _Edit:
    """One plan being applied: the document, and what the saving step needs to know."""

    def __init__(self, pdf: pikepdf.Pdf, images: Mapping[str, Path], redaction_dpi: int) -> None:
        self.pdf = pdf
        self.images = images
        self.redaction_dpi = redaction_dpi
        self.protect: Protect | None = None
        self.compress: Compress | None = None
        self.redacted_terms: list[tuple[str, bool]] = []

    def page(self, index: int) -> pikepdf.Page:
        if index >= len(self.pdf.pages):
            raise InvalidPlanError(f"Page {index} doesn't exist; the edited PDF has {len(self.pdf.pages)} pages.")
        return self.pdf.pages[index]


class PikepdfEditor:
    def __init__(self, *, max_pages: int, redaction_dpi: int) -> None:
        self._max_pages = max_pages
        self._redaction_dpi = redaction_dpi

    def edit(self, job: EditJob, output_dir: Path) -> tuple[tuple[Path, ...], int]:
        plan = job.plan
        with ExitStack() as stack:
            pdf = stack.enter_context(_open_document(job.documents[SELF], plan.password))
            others = {
                key: stack.enter_context(_open_source(path)) for key, path in job.documents.items() if key != SELF
            }
            for op in plan.operations:
                if isinstance(op, FillForm):
                    forms.fill_form(pdf, op.fields, flatten=op.flatten)
            if plan.pages is not None:
                _assemble(pdf, others, plan.pages)
            if len(pdf.pages) > self._max_pages:
                raise LimitExceededError(f"The edited PDF would have more than {self._max_pages:,} pages.")
            edit = _Edit(pdf, job.images, self._redaction_dpi)
            for op in plan.operations:
                _APPLY[type(op)](edit, op)
            return self._save(edit, plan.split, output_dir), len(pdf.pages)

    def _save(self, edit: _Edit, split: Split | None, output_dir: Path) -> tuple[Path, ...]:
        pdf = edit.pdf
        for page in pdf.pages:
            if drawing.WRAPPED in page.obj:
                del page.obj[drawing.WRAPPED]
        if edit.compress is not None:
            output.compress_images(pdf, edit.compress.preset)
        redaction.verify(_unencrypted_bytes(pdf), edit.redacted_terms)
        groups = output.page_groups(pdf, split)
        if len(groups) == 1 and len(groups[0]) == len(pdf.pages):
            path = output_dir / f"{EDITED_FILE_STEM}.pdf"
            output.save(pdf, path, protect=edit.protect, compress=edit.compress is not None)
            return (path,)
        paths = []
        for number, group in enumerate(groups, start=1):
            path = output_dir / f"{EDITED_FILE_STEM}-part-{number:02d}.pdf"
            with pikepdf.new() as part:
                part.pages.extend(pdf.pages[i] for i in group)
                output.save(part, path, protect=edit.protect, compress=edit.compress is not None)
            paths.append(path)
        return tuple(paths)


def _open_document(path: Path, password: str | None) -> pikepdf.Pdf:
    try:
        pdf = pikepdf.open(path, password=password or "")
    except pikepdf.PasswordError as exc:
        raise (WrongPasswordError() if password else PasswordRequiredError()) from exc
    except pikepdf.PdfError as exc:
        raise CorruptPdfError from exc
    if pdf.is_encrypted and not password:  # opens without a password, but its owner restricted it
        pdf.close()
        raise PasswordRequiredError
    return pdf


def _open_source(path: Path) -> pikepdf.Pdf:
    return _open_document(path, None)


def _assemble(pdf: pikepdf.Pdf, others: Mapping[str, pikepdf.Pdf], pages: Sequence[PageSpec]) -> None:
    """Append the plan's pages after the existing ones, then remove the existing ones."""
    existing = len(pdf.pages)
    for spec in pages:
        if isinstance(spec, BlankPage):
            width, height = PAGE_SIZES_PT[spec.size]
            pdf.add_blank_page(page_size=(height, width) if spec.landscape else (width, height))
            continue
        source = pdf if spec.document_id == SELF else others[spec.document_id]
        available = existing if source is pdf else len(source.pages)
        if spec.page > available:
            raise InvalidPlanError(f"Document {spec.document_id} has no page {spec.page}.")
        pdf.pages.append(source.pages[spec.page - 1])  # a copy, even when a page is used twice
        pdf.pages[-1].rotate(spec.rotate, relative=False)
    del pdf.pages[:existing]


def _unencrypted_bytes(pdf: pikepdf.Pdf) -> bytes:
    buffer = io.BytesIO()
    pdf.save(buffer)
    return buffer.getvalue()


# --- Operations -----------------------------------------------------------------------------------------------


def _annotate(edit: _Edit, op: AnnotateText) -> None:
    drawing.annotate_text(edit.pdf, edit.page(op.page), op)


def _highlight(edit: _Edit, op: Highlight) -> None:
    drawing.highlight(edit.pdf, edit.page(op.page), op)


def _ink(edit: _Edit, op: Ink) -> None:
    drawing.ink(edit.pdf, edit.page(op.page), op)


def _place_image(edit: _Edit, op: PlaceImage) -> None:
    drawing.place_image(edit.pdf, edit.page(op.page), op, edit.images[op.image_key])


def _watermark(edit: _Edit, op: Watermark) -> None:
    count = len(edit.pdf.pages)
    for index in op.pages.select(count) if op.pages else range(count):
        drawing.watermark(edit.pdf, edit.pdf.pages[index], op)


def _page_numbers(edit: _Edit, op: PageNumbers) -> None:
    total = len(edit.pdf.pages)
    for index, page in enumerate(edit.pdf.pages):
        drawing.page_number(edit.pdf, page, op, op.start_at + index, total)


def _redact_area(edit: _Edit, op: RedactArea) -> None:
    edit.page(op.page)
    redaction.redact(edit.pdf, {op.page: op.boxes}, dpi=edit.redaction_dpi)


def _redact_text(edit: _Edit, op: RedactText) -> None:
    found = redaction.find_text(edit.pdf, op.query, match_case=op.match_case)
    if found:
        redaction.redact(edit.pdf, found, dpi=edit.redaction_dpi)
    edit.redacted_terms.append((op.query, op.match_case))


def _replace_text(edit: _Edit, op: ReplaceText) -> None:
    forms.replace_text(edit.pdf, edit.page(op.page), op.find, op.replace)


def _metadata(edit: _Edit, op: Metadata) -> None:
    output.set_metadata(edit.pdf, op.changes)


def _protect(edit: _Edit, op: Protect) -> None:
    edit.protect = op


def _compress(edit: _Edit, op: Compress) -> None:
    edit.compress = op


def _already_done(_edit: _Edit, _op: FillForm) -> None:
    """Forms are filled before the pages are assembled (see the module docstring)."""


_APPLY: Mapping[type, Callable[[_Edit, Any], None]] = {
    AnnotateText: _annotate,
    Highlight: _highlight,
    Ink: _ink,
    PlaceImage: _place_image,
    Watermark: _watermark,
    PageNumbers: _page_numbers,
    RedactArea: _redact_area,
    RedactText: _redact_text,
    FillForm: _already_done,
    ReplaceText: _replace_text,
    Metadata: _metadata,
    Protect: _protect,
    Compress: _compress,
}


def operation_types() -> frozenset[type[Operation]]:  # for the test that every operation type has a step
    return frozenset(_APPLY)
