"""``FILL_FORM`` and ``REPLACE_TEXT``: change what a PDF already holds, rather than drawing over it."""

from collections.abc import Mapping
from typing import Any, cast

import pikepdf
from pikepdf.form import CheckboxField, ChoiceField, DefaultAppearanceStreamGenerator, Form, TextField

from conversion.conversion.domain.exceptions import InvalidPlanError
from conversion.conversion.infrastructure.pdfedit.drawing import encode

_TRUE = frozenset({"true", "yes", "on", "1", "x"})
_SHOWING_TEXT = frozenset({"Tj", "'", '"'})


def fill_form(pdf: pikepdf.Pdf, values: Mapping[str, str], *, flatten: bool) -> None:
    """Set the named fields and draw their new appearance; ``flatten`` makes them plain page content."""
    if "/AcroForm" not in pdf.Root:
        raise InvalidPlanError("This PDF has no form to fill.")
    form = Form(pdf, DefaultAppearanceStreamGenerator)
    filled: set[str] = set()
    for name, field in form.items():
        if name not in values:
            continue
        match field:
            case TextField() | ChoiceField():
                field.value = values[name]
            case CheckboxField():
                field.checked = values[name].strip().lower() in _TRUE
            case _:
                raise InvalidPlanError(f"Form field '{name}' is a kind that can't be filled.")
        filled.add(name)
    missing = set(values) - filled
    if missing:
        raise InvalidPlanError(f"The form has no field named {', '.join(sorted(missing))}.")
    if flatten:
        pdf.flatten_annotations("all")
        if "/AcroForm" in pdf.Root:  # flattening may already have removed it
            del pdf.Root.AcroForm


def replace_text(pdf: pikepdf.Pdf, page: pikepdf.Page, find: str, replacement: str) -> None:
    """Swap the text where the page stores it as plain characters, so the change is real text.

    PDFs that store text as glyph numbers (most embedded subset fonts) can't be changed this way: the plan fails
    with ``CNV-422-PLAN-001`` instead of drawing over the old text, which would leave it in the file.
    """
    wanted, new = encode(find), encode(replacement)
    changed = 0
    instructions: list[object] = []
    for original in pikepdf.parse_content_stream(page):
        item = original
        if isinstance(original, pikepdf.ContentStreamInstruction):
            item, hit = _replaced(original, wanted, new)
            changed += hit
        instructions.append(item)
    if not changed:
        raise InvalidPlanError(f"'{find}' isn't stored as plain text on that page, so it can't be replaced.")
    page.obj.Contents = pdf.make_stream(pikepdf.unparse_content_stream(cast(Any, instructions)))  # see _draw


def _replaced(
    item: pikepdf.ContentStreamInstruction, wanted: bytes, new: bytes
) -> tuple[pikepdf.ContentStreamInstruction, int]:
    operator, operands = str(item.operator), list(item.operands)
    if operator in _SHOWING_TEXT and operands and isinstance(operands[-1], pikepdf.String):
        text = bytes(operands[-1])
        if wanted in text:
            operands[-1] = pikepdf.String(text.replace(wanted, new))
            return pikepdf.ContentStreamInstruction(operands, item.operator), 1
    if operator == "TJ" and operands and isinstance(operands[0], pikepdf.Array):
        joined = b"".join(bytes(part) for part in operands[0] if isinstance(part, pikepdf.String))
        if wanted in joined:  # the spacing adjustments go; the words are what matter
            return pikepdf.ContentStreamInstruction(
                [pikepdf.String(joined.replace(wanted, new))], pikepdf.Operator("Tj")
            ), 1
    return item, 0
