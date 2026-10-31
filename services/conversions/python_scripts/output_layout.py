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
from typing import NamedTuple

MAX_SPACE_PT = 36.0          # the most space a paragraph may carry before or after it

_SECT_PR, _DIGITS = "}sectPr", r"(\d+)"          # an element tag ending, and a run of digits to split on

_PAGE_OF = re.compile(r"^(?P<page>page\s*)?\d+(?P<of>\s*(of|/)\s*\d+)?$", re.I)
_PAGE_DASHED = re.compile(r"^[-–—]\s*\d+\s*[-–—]$")


class PageNumberMatch(NamedTuple):
    """What a printed page number says: whether it has the word "Page" and whether it gives the total."""

    has_word: bool
    has_total: bool


class _PageNumber:
    """Matches a printed page number: "7", "Page 7", "Page 7 of 12", "7 / 12" or "- 7 -"."""

    @staticmethod
    def match(text: str) -> PageNumberMatch | None:
        found = _PAGE_OF.match(text)
        if found:
            return PageNumberMatch(has_word=bool(found["page"]), has_total=bool(found["of"]))
        return PageNumberMatch(has_word=False, has_total=False) if _PAGE_DASHED.match(text) else None


PAGE_NUMBER = _PageNumber()


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
        while prev is not None and not prev.tag.endswith(("}br", "}t")):
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


def _key(text: str) -> str:
    """A line with its numbers blanked, so "Page 2" and "Page 3" are the same running line."""
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def _running_footer_to_footer(doc) -> int:
    """Move the line(s) every page repeats at its end (a report name, "Page 2", "Confidential") into Word's footer.
    They are the same line on most pages once the numbers are blanked, so they are found by repetition, not by what they
    say. A number equal to the page's own number becomes the PAGE field, and one equal to the page count NUMPAGES.
    Returns how many lines were moved; nothing is touched unless most pages end with the same line."""
    from docx.enum.text import WD_ALIGN_PARAGRAPH
    from docx.shared import Pt
    from docx.text.paragraph import Paragraph
    body = doc.element.body
    chunks, cur = [], []
    for el in body.iterchildren():
        if el.tag.endswith(_SECT_PR):
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
    chunks = [c for c in chunks if c]
    if len(chunks) < 2:
        return 0
    paras = [[el for el in c if el.tag.endswith("}p") and _text(el)] for c in chunks]
    counts = {}
    for ps in paras:
        for k in {_key(_text(el)) for el in ps if len(_text(el)) <= 120}:
            counts[k] = counts.get(k, 0) + 1
    need = max(2, -(-len(chunks) * 6 // 10))                  # in at least 60% of the pages
    trailing = []                                             # per chunk: the trailing paragraphs that repeat
    for ps in paras:
        run = []
        for el in reversed(ps):
            t = _text(el)
            if len(t) <= 120 and counts.get(_key(t), 0) >= need:
                run.insert(0, el)
            else:
                break
        trailing.append(run)
    if sum(1 for r in trailing if r) < need:
        return 0
    first = next(r for r in trailing if r)
    page_no = trailing.index(first) + 1
    total = len(chunks)
    for sec in doc.sections:
        sec.footer.is_linked_to_previous = False
        for extra in sec.footer.paragraphs[1:]:
            extra._p.getparent().remove(extra._p)
        height_pt = 0.0
        for n, el in enumerate(first):
            src = Paragraph(el, doc._body)
            run = next((r for r in reversed(src.runs) if r.text.strip()), None)
            par = sec.footer.paragraphs[0] if n == 0 else sec.footer.add_paragraph()
            for r in par.runs:
                r._r.getparent().remove(r._r)
            par.alignment = src.alignment
            par.paragraph_format.space_before = par.paragraph_format.space_after = Pt(0)
            for part in re.split(_DIGITS, _text(el)):
                if not part:
                    continue
                if part.isdigit() and int(part) == page_no:
                    _field(par, "PAGE")
                elif part.isdigit() and int(part) == total and total != page_no:
                    _field(par, "NUMPAGES")
                else:
                    par.add_run(part)
            size = run.font.size if run is not None and run.font.size else None
            for r in par.runs:
                if run is not None:
                    r.font.name, r.font.size, r.font.bold, r.font.italic = run.font.name, size, run.font.bold, run.font.italic
                    if run.font.color and run.font.color.type:
                        r.font.color.rgb = run.font.color.rgb
            height_pt += 1.35 * (size.pt if size else 11.0)
        sec.footer_distance = Pt(max(sec.bottom_margin / 12700 - height_pt, 2.0))
    removed = 0
    for run in trailing:
        for el in run:
            body.remove(el)
            removed += 1
    return removed


def _page_numbers_to_footer(doc) -> int:
    """Move the page numbers into Word's own footer as PAGE / NUMPAGES fields, keeping the number's look. Returns how
    many were moved; nothing is touched unless most pages end with one."""
    from docx.shared import Cm, Pt
    from docx.text.paragraph import Paragraph
    body = doc.element.body
    chunks, cur = [], []
    for el in body.iterchildren():
        if el.tag.endswith(_SECT_PR):
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
    template = (["Page "] if first.has_word else []) + ["PAGE"] + ([" of ", "NUMPAGES"] if first.has_total else [])
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
        for r in par.runs:
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
    children = [el for el in body.iterchildren() if not el.tag.endswith(_SECT_PR)]
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


def set_running_lines(path: str, footer_rows, header_rows, total_pages: int) -> int:
    """Write the running footer / header the PDF repeats on its pages into the Word file's own footer / header.
    A row of several parts (a report name on the left, "Page 2" on the right) becomes one paragraph with tab stops, so
    each part keeps its side. A number equal to the page's own number becomes the PAGE field, one equal to the page
    count NUMPAGES. The look (font, size, bold, colour) is the PDF's. Returns how many rows were written."""
    from docx import Document
    from docx.enum.text import WD_TAB_ALIGNMENT
    from docx.shared import Pt, RGBColor

    def first_rows(rows_per_page):
        for n, rows in enumerate(rows_per_page, 1):
            if rows:
                return n, rows
        return 0, []

    doc = Document(path)
    written = 0
    for side, rows_per_page in (("footer", footer_rows), ("header", header_rows)):
        page_no, rows = first_rows(rows_per_page)
        if not rows:
            continue
        for sec in doc.sections:
            area = sec.footer if side == "footer" else sec.header
            area.is_linked_to_previous = False
            for extra in area.paragraphs[1:]:
                extra._p.getparent().remove(extra._p)
            width = sec.page_width - sec.left_margin - sec.right_margin
            height_pt = 0.0
            for n, row in enumerate(rows):
                par = area.paragraphs[0] if n == 0 else area.add_paragraph()
                for r in par.runs:
                    r._r.getparent().remove(r._r)
                par.style = doc.styles["Normal"]                  # not "Footer": its own centre / right tab stops would win
                par.paragraph_format.space_before = par.paragraph_format.space_after = Pt(0)
                page_w = (sec.page_width) / 12700.0
                for k, (text, x0, x1, font, size, bold, rgb) in enumerate(row):
                    if k:                                           # a tab to this part's side of the line
                        right = x1 > 0.7 * page_w and x0 > 0.5 * page_w
                        stop = width if right else int(width / 2)
                        par.paragraph_format.tab_stops.add_tab_stop(
                            stop, WD_TAB_ALIGNMENT.RIGHT if right else WD_TAB_ALIGNMENT.CENTER)
                        par.add_run("\t")
                    for part in re.split(_DIGITS, text):
                        if not part:
                            continue
                        if part.isdigit() and int(part) == page_no and side == "footer":
                            _field(par, "PAGE")
                        elif part.isdigit() and int(part) == total_pages and total_pages != page_no:
                            _field(par, "NUMPAGES")
                        else:
                            par.add_run(part)
                    for r in par.runs[-(len(re.split(_DIGITS, text)) + 1):]:
                        r.font.name, r.font.size = font.split("+")[-1].replace("-", " ") if font else None, Pt(size)
                        r.font.bold = bool(bold)
                        r.font.color.rgb = RGBColor((rgb >> 16) & 255, (rgb >> 8) & 255, rgb & 255)
                height_pt += 1.35 * row[0][4]
            if side == "footer":
                sec.footer_distance = Pt(max(sec.bottom_margin / 12700 - height_pt, 2.0))
            else:
                sec.header_distance = Pt(max(sec.top_margin / 12700 - height_pt, 2.0))
        written += len(rows)
    doc.save(path)
    return written


def _fit_tables_and_orientation(doc) -> list:
    """No content may run off the page: a table wider than the text column is scaled down to fit it, and a page that is
    wider than it is tall is marked landscape."""
    from docx.enum.section import WD_ORIENT
    from docx.shared import Emu
    notes = []
    for sec in doc.sections:
        if sec.page_width > sec.page_height and sec.orientation != WD_ORIENT.LANDSCAPE:
            sec.orientation = WD_ORIENT.LANDSCAPE
            notes.append("page marked landscape (it is wider than it is tall)")
    sec = doc.sections[0]
    avail = sec.page_width - sec.left_margin - sec.right_margin
    scaled = 0
    for table in doc.tables:
        widths = [c.width for c in table.rows[0].cells if c.width]
        total = sum(widths)
        if total and total > avail * 1.01:
            factor = avail / total
            for row in table.rows:
                for cell in row.cells:
                    if cell.width:
                        cell.width = Emu(int(cell.width * factor))
            scaled += 1
    if scaled:
        notes.append(f"{scaled} table(s) were wider than the page and were scaled to fit it")
    return notes


def fix_docx_layout(path: str) -> list:
    """Apply the Word layout fixes in place; returns one note per fix that applied."""
    from docx import Document
    from docx.oxml import OxmlElement
    from docx.shared import Pt

    doc = Document(path)
    notes = []
    moved = _running_footer_to_footer(doc) or _page_numbers_to_footer(doc)
    if moved:
        notes.append(f"{moved} running footer line(s) moved into the footer, in their own look")
    if _breaks_to_page_break_before(doc):
        notes.append("page breaks set on the first line of each page (no blank page)")
    notes += _fit_tables_and_orientation(doc)
    clamped = 0
    for para in doc.paragraphs:                      # a spacing measured from the page can never be a blank half page
        for attr in ("space_before", "space_after"):
            value = getattr(para.paragraph_format, attr)
            if value is not None and value.pt > MAX_SPACE_PT:
                setattr(para.paragraph_format, attr, Pt(MAX_SPACE_PT))
                clamped += 1
    if clamped:
        notes.append(f"{clamped} oversized paragraph space(s) limited to {MAX_SPACE_PT:.0f} pt")
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
