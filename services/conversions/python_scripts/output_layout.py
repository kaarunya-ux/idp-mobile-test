"""
output_layout.py -- the layout part of the appearance step, applied to the Word and Excel files test_pdf.py writes.

The appearance step copies the source's own fonts, sizes, bold and alignment onto the file (pdf_look.py for a digital
page, ocr_layout.py for a scan). This adds the layout fixes that step does not cover, without touching any of that:

    Word    the page numbers the source printed at the end of each page ("Page 2 of 5", "- 2 -") become a real
            footer in their own font, size and alignment; the empty page-break paragraphs between pages become
            "page break before" on whatever starts the next page, so a page whose text just fills the last one
            never prints a blank page; a table's header row repeats when the table runs onto another page
    Excel   the header row is frozen

Each fix leaves the file as it was when it does not apply (no page numbers, no table, no header row).
"""

import re

PAGE_NUMBER = re.compile(r"^(?P<page>page\s*)?\d+(?P<of>\s*(of|/)\s*\d+)?$|^[-–—]\s*\d+\s*[-–—]$", re.I)


def _text(el) -> str:
    """The text of a paragraph or table element, from its <w:t> nodes only."""
    return "".join(t.text or "" for t in el.xpath(".//w:t")).strip()


def _is_page_break(el) -> bool:
    """A paragraph that holds a page break and nothing else (how build_docx separates the source's pages)."""
    return el.tag.endswith("}p") and bool(el.xpath('.//w:br[@w:type="page"]')) and not _text(el)


def _starts_page(el) -> bool:
    """A paragraph, or a table whose first paragraph, carries "page break before": the first thing on a page."""
    from docx.oxml.ns import qn
    para = el if el.tag.endswith("}p") else (el.xpath(".//w:tc[1]/w:p[1]") or [None])[0]
    return para is not None and bool(para.xpath("./w:pPr/w:pageBreakBefore"))


def _trailing_number(el):
    """(page number text, run index) when the paragraph ends with a page number on a line of its own inside it
    (after a line break), else None."""
    from docx.text.paragraph import Paragraph
    from docx.oxml.ns import qn
    for node in reversed(el.xpath(".//w:t")):
        text = (node.text or "").strip()
        if not text:
            continue
        prev = node.getprevious()
        while prev is not None and not (prev.tag.endswith("}br") or prev.tag.endswith("}t")):
            prev = prev.getprevious()
        first_in_run = node.getparent().index(node) == 0 or all(
            c.tag.endswith(("}rPr", "}br")) for c in list(node.getparent())[:node.getparent().index(node)])
        after_break = prev is not None and prev.tag.endswith("}br") or first_in_run
        if PAGE_NUMBER.match(text) and after_break:
            return text, node
        return None
    return None


def _field(par, code: str) -> None:
    """Add a Word field (PAGE, NUMPAGES) to a paragraph."""
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    run = par.add_run()
    for kind in ("begin", "instr", "separate", "text", "end"):
        if kind == "instr":
            node = OxmlElement("w:instrText")
            node.set(qn("xml:space"), "preserve")
            node.text = f" {code} "
        elif kind == "text":
            node = OxmlElement("w:t")
            node.text = "1"
        else:
            node = OxmlElement("w:fldChar")
            node.set(qn("w:fldCharType"), kind)
        run._r.append(node)


def _page_numbers_to_footer(doc) -> int:
    """Move the page numbers into Word's own footer as PAGE / NUMPAGES fields, keeping the number's look. Returns how
    many were moved; nothing is touched unless most pages end with one."""
    from docx.shared import Cm, Pt
    from docx.text.paragraph import Paragraph
    body = doc.element.body
    chunks, cur = [], []
    for el in list(body.iterchildren()):
        if el.tag.endswith("}sectPr"):
            continue
        if _is_page_break(el):
            chunks.append(cur)
            cur = []
        elif _starts_page(el) and cur:
            chunks.append(cur)
            cur = [el]
        else:
            cur.append(el)
    chunks.append(cur)
    found = []                                      # (paragraph, number text, the <w:t> to cut, or None for the whole paragraph)
    for chunk in chunks:
        texts = [(el, _text(el)) for el in chunk if el.tag.endswith("}p")]
        texts = [(el, t) for el, t in texts if t]
        if not texts:
            continue
        el, t = texts[-1]
        if PAGE_NUMBER.match(t):
            found.append((el, t, None))
        else:
            tail = _trailing_number(el)
            if tail:
                found.append((el, tail[0], tail[1]))
    if not found or len(found) * 2 < len([c for c in chunks if c]):
        return 0
    first = PAGE_NUMBER.match(found[0][1])
    template = (["Page "] if first.group("page") else []) + ["PAGE"] + ([" of ", "NUMPAGES"] if first.group("of") else [])
    para = Paragraph(found[0][0], doc._body)
    runs = [r for r in para.runs if r.text.strip()]

    def look_of(run):
        return (para.alignment, run.font.name if run else None, run.font.size if run else None,
                run.font.bold if run else None, run.font.italic if run else None,
                run.font.color.rgb if run is not None and run.font.color and run.font.color.type else None)
    look = look_of(runs[-1] if runs else None)                 # the number's own look
    word_look = look_of(runs[0]) if len(runs) > 1 and runs[0].text.strip().lower() == "page" else look   # "Page " alone
    # The footer must fit inside the bottom margin the file already has. If it needs more room, Word / LibreOffice grow
    # the margin, the body shrinks and the last line of each full page spills onto a page of its own. So the footer is
    # placed as near the page edge as its height allows, and the bottom margin is left alone.
    size_pt = (look[2].pt if look[2] else 11.0)
    for el, _t, node in found:
        if node is None:
            body.remove(el)
        else:                                       # the number is the last line of a longer paragraph: cut that line
            run_el = node.getparent()
            prev = node.getprevious()
            run_el.remove(node)
            if prev is not None and prev.tag.endswith("}br"):
                run_el.remove(prev)
    for sec in doc.sections:
        room_pt = sec.bottom_margin / 12700 - 1.35 * size_pt           # EMU -> pt, less the footer line's height
        sec.footer_distance = Pt(max(room_pt, 2.0))
        sec.footer.is_linked_to_previous = False
        par = sec.footer.paragraphs[0] if sec.footer.paragraphs else sec.footer.add_paragraph()
        for r in list(par.runs):
            r._r.getparent().remove(r._r)
        par.alignment = look[0]
        for part in template:
            if part in ("PAGE", "NUMPAGES"):
                _field(par, part)
            else:
                par.add_run(part)
        for r in par.runs:
            use = word_look if r.text == "Page " else look
            r.font.name, r.font.size, r.font.bold, r.font.italic = use[1], use[2], use[3], use[4]
            if use[5] is not None:
                r.font.color.rgb = use[5]
    return len(found)


def _breaks_to_page_break_before(doc) -> int:
    """Replace the empty page-break paragraphs with "page break before" on whatever starts the next page."""
    from docx.table import Table
    from docx.text.paragraph import Paragraph
    body = doc.element.body
    children = [el for el in body.iterchildren() if not el.tag.endswith("}sectPr")]
    pending, changed = False, 0
    for el in children:
        if _is_page_break(el):
            body.remove(el)
            pending = True
            changed += 1
        elif pending:
            pending = False
            if el.tag.endswith("}p"):
                Paragraph(el, doc._body).paragraph_format.page_break_before = True
            elif el.tag.endswith("}tbl"):
                Table(el, doc._body).cell(0, 0).paragraphs[0].paragraph_format.page_break_before = True
    return changed


def fix_docx_layout(path: str) -> list:
    """Apply the Word layout fixes in place; returns one note per fix that applied."""
    from docx import Document
    from docx.oxml import OxmlElement

    doc = Document(path)
    notes = []
    moved = _page_numbers_to_footer(doc)
    if moved:
        notes.append(f"{moved} page number(s) moved into the footer, in their own look")
    if _breaks_to_page_break_before(doc):
        notes.append("page breaks set on the first line of each page (no blank page)")
    repeated = 0
    for table in doc.tables:
        if table.rows and not table.rows[0]._tr.get_or_add_trPr().xpath("./w:tblHeader"):
            table.rows[0]._tr.get_or_add_trPr().append(OxmlElement("w:tblHeader"))
            repeated += 1
    if repeated:
        notes.append(f"header row repeats across pages in {repeated} table(s)")
    doc.save(path)
    return notes


def fix_xlsx_layout(path: str) -> list:
    """Freeze the header row of each sheet that has one; returns one note per sheet changed."""
    import openpyxl

    wb = openpyxl.load_workbook(path)
    notes = []
    for ws in wb:
        if ws.freeze_panes:
            continue
        rows = [[c.value for c in r] for r in ws.iter_rows()]
        width = max((sum(v is not None for v in r) for r in rows), default=0)
        header = next((i for i, r in enumerate(rows, 1) if sum(v is not None for v in r) == width and width > 1
                       and all(isinstance(v, str) for v in r if v is not None)), None)
        if header:
            ws.freeze_panes = ws.cell(row=header + 1, column=1)
            notes.append(f"sheet {ws.title!r}: header row {header} frozen")
    if notes:
        wb.save(path)
    return notes
