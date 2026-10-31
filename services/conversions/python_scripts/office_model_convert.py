"""
office_model_convert.py -- PowerPoint, Excel, CSV, PDF, Word or an image into Word / Excel / PowerPoint / CSV /
PDF / page images, read by the big model (the 35B) with the libraries checking it.

    PowerPoint (.pptx)        -> docx, xlsx, csv, pdf
    Excel (.xlsx)             -> docx, pptx, csv, pdf
    CSV (.csv)                -> docx, xlsx, pptx, pdf
    PDF, digital or scanned   -> docx, xlsx, pptx, csv, image
    Word (.docx)              -> pdf, pptx, csv, image, xlsx
    Image (.png .jpg .tif)    -> docx, pdf, csv, pptx, xlsx

HOW IT WORKS (the same pipeline word_to_xlsx.py uses, see doc_pipeline.py):
  1. LibreOffice draws the file as PDF pages (a PDF is used as it is, an image becomes a one-page PDF).
  2. the model reads every page and writes its blocks.   (the model: headings, paragraphs, tables)
  3. test_pdf.py checks each page against the PDF's own text layer, snaps the model's words to it, repairs
     missing table rows and measures fonts / alignment.   (tools: PyMuPDF, python-docx / openpyxl / python-pptx)
  4. THIS SCRIPT reads the source file itself (python-pptx, openpyxl, csv, python-docx, the PDF's text layer; for a
     scan or an image, Tesseract OCR) and the written file, and reports every word of the source that the output
     lost. An OCR reading is itself imperfect, so a scan or image is held to MIN_COVERAGE_OCR, not MIN_COVERAGE.
--to pdf from Word, and --to image from a PDF or Word file, are LibreOffice / PyMuPDF alone: nothing for a model to
read. An image to PDF is rebuilt from what the model read (via Word), not the picture placed on a page.

The output copies the source's own look (fonts, sizes, bold, alignment, measured by test_pdf.py) and then gets the
layout fixes of output_layout.py: a Word file's page numbers in a footer, no blank pages, a repeating table header;
an Excel file's header row frozen.

Usage:
    .venv/bin/python python_scripts/office_model_convert.py deck.pptx --to docx
    .venv/bin/python python_scripts/office_model_convert.py book.xlsx --to csv --pages 1-2
    .venv/bin/python python_scripts/office_model_convert.py data.csv --to pptx --base-url http://10.0.3.66:8080
Output goes to output/office_model_output/ unless --outdir is given. The model server must be running.
"""

import argparse
import csv
import os
import re
import shutil
import subprocess
import sys
import tempfile
import time
import zipfile

import paths
import doc_pipeline
from to_xlsx import cell_display
from ppt_convert import readable_headers       # white-on-pale table headers, see there

MODEL_URL = os.environ.get("IDOX_BASE_URL", "http://127.0.0.1:8080")   # the llama.cpp server that reads pages
PPTX, XLSX, DOCX, CSV, PDF = ".pptx", ".xlsx", ".docx", ".csv", ".pdf"
IMAGE_EXTS = (".png", ".jpg", ".jpeg", ".tif", ".tiff", ".bmp", ".webp")
ROUTES = {PPTX: ("docx", "xlsx", "csv", "pdf"),
          XLSX: ("docx", "pptx", "csv", "pdf"),
          CSV: ("docx", "xlsx", "pptx", "pdf"),
          PDF: ("docx", "xlsx", "pptx", "csv", "image"),
          DOCX: ("pdf", "pptx", "csv", "image", "xlsx")}
ROUTES.update({ext: ("docx", "pdf", "csv", "pptx", "xlsx") for ext in IMAGE_EXTS})
MIN_COVERAGE = 95.0                           # % of the source's words that must be in the output
MIN_COVERAGE_OCR = 85.0                       # the same, when the source itself had to be read by OCR
OCR_DPI = 200
IMAGE_DPI = 150                               # --to image


def _tokens(text: str) -> list:
    """Words and numbers, lower-cased; thousands commas and currency signs removed so $1,200.50 matches 1200.50."""
    text = re.sub(r"(?<=\d),(?=\d)", "", text.lower())
    # 01 and 1 are the same number: a date shown as 1/1/2024 must match one stored as 2024-01-01
    return [w.lstrip("0") or "0" if w.isdigit() else w for w in re.findall(r"[^\W_]+", text)]


def fit_sheets_to_page(src: str, dst: str) -> bool:
    """Copy a workbook to dst with every sheet set to one page wide, landscape. LibreOffice prints a wide sheet on
    A4 portrait across several pages, so a table's last columns land on their own page and the model reads a table in
    pieces. A workbook with charts or pictures is left alone (openpyxl would drop them): returns False then."""
    import openpyxl
    from openpyxl.worksheet.properties import PageSetupProperties

    with zipfile.ZipFile(src) as zf:
        if any(n.startswith(("xl/drawings/", "xl/charts/", "xl/media/")) for n in zf.namelist()):
            _fit_by_xml(src, dst)                    # charts and pictures: edit the sheets' page setup in place
            return True
    wb = openpyxl.load_workbook(src)
    for ws in wb:
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0               # as many pages tall as it needs
        ws.page_setup.orientation = "landscape"
        _break_between_tables(ws)
    wb.save(dst)
    return True




_AFTER_PAGE_SETUP = ("headerFooter", "rowBreaks", "colBreaks", "customProperties", "cellWatches", "ignoredErrors",
                     "smartTags", "drawing", "legacyDrawing", "legacyDrawingHF", "drawingHF", "picture", "oleObjects",
                     "controls", "webPublishItems", "tableParts", "extLst")


def _fit_by_xml(src: str, dst: str) -> None:
    """The same one-page-wide landscape setup, written straight into each sheet's XML. openpyxl cannot do it for a
    workbook with a chart or picture: saving would drop them. Nothing else in the file is touched."""
    with zipfile.ZipFile(src) as zin, zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as zout:
        for item in zin.infolist():
            data = zin.read(item.filename)
            if re.fullmatch(r"xl/worksheets/sheet\d+\.xml", item.filename):
                xml = data.decode("utf-8")
                if re.search(r"<sheetPr\b[^>]*/>", xml):
                    xml = re.sub(r"<sheetPr\b([^>]*)/>", r'<sheetPr\1><pageSetUpPr fitToPage="1"/></sheetPr>', xml, count=1)
                elif "<sheetPr" in xml:
                    if "<pageSetUpPr" in xml:
                        xml = re.sub(r"<pageSetUpPr\b[^>]*/>", '<pageSetUpPr fitToPage="1"/>', xml, count=1)
                    else:
                        xml = xml.replace("</sheetPr>", '<pageSetUpPr fitToPage="1"/></sheetPr>', 1)
                else:
                    xml = re.sub(r"(<worksheet\b[^>]*>)", r'\1<sheetPr><pageSetUpPr fitToPage="1"/></sheetPr>', xml, count=1)
                setup = '<pageSetup orientation="landscape" fitToWidth="1" fitToHeight="0"/>'
                if re.search(r"<pageSetup\b[^>]*/>", xml):
                    xml = re.sub(r"<pageSetup\b[^>]*/>", setup, xml, count=1)
                elif re.search(r"<pageMargins\b[^>]*/>", xml):
                    xml = re.sub(r"(<pageMargins\b[^>]*/>)", r"\1" + setup, xml, count=1)
                else:
                    later = [m.start() for t in _AFTER_PAGE_SETUP for m in [re.search(rf"<{t}\b", xml)] if m]
                    at = min(later) if later else xml.rfind("</worksheet>")
                    xml = xml[:at] + setup + xml[at:]
                data = xml.encode("utf-8")
            zout.writestr(item, data)


def _units(ws):
    """(first row, last row) of each table with the one-line title above it, found by the blank rows between them."""
    blocks, start = [], None
    for r in range(1, ws.max_row + 2):
        empty = r > ws.max_row or all(c.value is None for c in ws[r])
        if not empty and start is None:
            start = r
        elif empty and start is not None:
            blocks.append([start, r - 1])
            start = None
    units = []
    for first, last in blocks:
        one_line = first == last and sum(c.value is not None for c in ws[first]) == 1
        if units and units[-1][2]:                   # the line above was a title: it belongs to this table
            units[-1] = [units[-1][0], last, one_line and len(units) == 1]   # at the sheet's start every title line joins
        else:
            units.append([first, last, one_line])
    return [(a, b) for a, b, _ in units]


def _is_table(ws, first: int, last: int) -> bool:
    """A table has a row with two or more filled cells; a block of one-cell rows is notes, not a table."""
    return any(sum(c.value is not None for c in ws[r]) >= 2 for r in range(first, last + 1))


def _break_between_tables(ws) -> None:
    """A sheet taller than a page is cut wherever the page runs out, through the middle of a table, and the model then
    reads half a table on each of two pages. Two tables on one page were too much for the model as well (2 of 7 came out
    as tables), so each table starts a new page. A table taller than a page is still cut; nothing helps that."""
    from openpyxl.worksheet.pagebreak import Break

    previous_end = 0
    for first, last in _units(ws):
        if previous_end and _is_table(ws, first, last):    # one table to a page; notes stay under the table above
            ws.row_breaks.append(Break(id=previous_end))
        previous_end = last


def csv_to_text_workbook(src: str, dst: str) -> None:
    """Copy a CSV into a workbook with every cell as text, one page wide. LibreOffice opens a CSV by turning
    numbers into numbers, so 45.00 is drawn as 45 and 1080.00 as 1080: the page no longer shows what the CSV says."""
    import openpyxl
    from openpyxl.worksheet.properties import PageSetupProperties

    raw = open(src, "rb").read()
    for enc in ("utf-8-sig", "utf-16", "cp1252"):
        try:
            text = raw.decode(enc)
            break
        except UnicodeError:
            continue
    try:
        dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    wb = openpyxl.Workbook()
    ws = wb.active
    for row in csv.reader(text.splitlines(), dialect):
        ws.append(list(row))
    for line in ws.iter_rows():
        for cell in line:
            cell.number_format = "@"                  # text: shown exactly as written
            cell.data_type = "s"
    for col in ws.columns:                            # wide enough for its longest cell, or the page clips the text
        ws.column_dimensions[col[0].column_letter].width = min(80, max(len(str(c.value or "")) for c in col) * 1.2 + 3)
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.page_setup.orientation = "landscape"
    wb.save(dst)


def table_counts(src: str, out: str):
    """(tables in the source, tables in the Word output): a table cut in two shows up as too many."""
    ext = os.path.splitext(src)[1].lower()
    if ext == PPTX:
        from pptx import Presentation
        want = sum(1 for sl in Presentation(src).slides for sh in sl.shapes if getattr(sh, "has_table", False) and sh.has_table)
    elif ext == XLSX:
        import openpyxl
        want = 0
        for ws in openpyxl.load_workbook(src, data_only=True):
            tables = [(a, b) for a, b in _units(ws) if b > a and _is_table(ws, a, b)]
            want += len(tables) or (1 if ws.max_row > 1 or ws.max_column > 1 else 0)
    elif ext == CSV:
        want = 1
    else:
        want = None                                  # a PDF, Word file or image has no table list to count against
    import docx
    return want, len(docx.Document(out).tables)


def _shape_parts(shape) -> list[str]:
    """The words one shape holds: its text, its table cells and its chart's names and values."""
    parts: list[str] = []
    if shape.has_text_frame:
        parts.append(shape.text_frame.text)
    if getattr(shape, "has_table", False) and shape.has_table:
        parts.extend(c.text for row in shape.table.rows for c in row.cells)
    if getattr(shape, "has_chart", False) and shape.has_chart:
        for plot in shape.chart.plots:
            parts.extend(str(c) for c in plot.categories)
            parts.extend(s.name or "" for s in plot.series)
            parts.extend(str(v) for s in plot.series for v in s.values if v is not None)
    return parts


def _walk_shapes(shapes) -> list[str]:
    from pptx.enum.shapes import MSO_SHAPE_TYPE

    parts: list[str] = []
    for shape in shapes:
        if shape.shape_type == MSO_SHAPE_TYPE.GROUP:
            parts.extend(_walk_shapes(shape.shapes))
        else:
            parts.extend(_shape_parts(shape))
    return parts


def _pptx_text(path: str) -> str:
    from pptx import Presentation

    return "\n".join(part for slide in Presentation(path).slides for part in _walk_shapes(slide.shapes))


def _xlsx_text(path: str) -> str:
    import openpyxl
    wb = openpyxl.load_workbook(path, data_only=True)
    # as the cell is shown (45.00, not 45): the number format decides, the same way the writer applies it
    import datetime

    def shown(c):
        if isinstance(c.value, datetime.datetime):             # a date is read by its parts, not as 2024-01-01 00:00:00
            return f"{c.value.month}/{c.value.day}/{c.value.year}"
        return cell_display(c.value, c.number_format)
    return "\n".join(shown(c) for ws in wb for row in ws.iter_rows() for c in row if c.value is not None)


def _csv_text(path: str) -> str:
    # one cell per line: a raw read would let the delimiter commas look like thousands commas (24,45.00 -> 2445.00)
    with open(path, encoding="utf-8-sig", errors="replace", newline="") as fh:
        raw = fh.read()
    try:
        dialect = csv.Sniffer().sniff(raw[:4096], delimiters=",;\t|")
    except csv.Error:
        dialect = csv.excel
    return "\n".join(cell for row in csv.reader(raw.splitlines(), dialect) for cell in row)


def _docx_text(path: str) -> str:
    import docx
    d = docx.Document(path)
    cells = [c.text for t in d.tables for row in t.rows for c in row.cells]
    return "\n".join([p.text for p in d.paragraphs] + cells)


def _pdf_text(path: str) -> str:
    import pymupdf
    with pymupdf.open(path) as doc:
        return "\n".join(page.get_text() for page in doc)


def ocr_text(pdf: str) -> str:
    """Tesseract's reading of every page of a PDF: the stand-in for a text layer a scan or image does not have."""
    import pymupdf
    parts = []
    with pymupdf.open(pdf) as doc, tempfile.TemporaryDirectory() as tmp:
        for i, page in enumerate(doc):
            png = os.path.join(tmp, f"p{i}.png")
            page.get_pixmap(dpi=OCR_DPI).save(png)
            try:
                run = subprocess.run(["tesseract", png, "stdout"], capture_output=True, text=True, timeout=300)
            except FileNotFoundError:
                return ""                       # no Tesseract: the caller reports the check as skipped
            parts.append(run.stdout)
    return "\n".join(parts)


def image_to_pdf(src: str, dst: str) -> None:
    """A one-page-per-frame PDF around an image (PNG, JPG, TIFF), so the same page pipeline can read it."""
    import pymupdf
    with pymupdf.open(src) as img:
        data = img.convert_to_pdf()
    with pymupdf.open("pdf", data) as pdf:
        pdf.save(dst)


def render_pages(pdf: str, outdir: str, name: str) -> list:
    """--to image: every page as a PNG. LibreOffice / PyMuPDF only, no model."""
    import pymupdf
    written = []
    with pymupdf.open(pdf) as doc:
        for i, page in enumerate(doc, start=1):
            path = os.path.join(outdir, f"{name}_page_{i:03d}.png")
            page.get_pixmap(dpi=IMAGE_DPI).save(path)
            written.append(path)
    return written


def _out_text(path: str) -> str:
    ext = os.path.splitext(path)[1].lower()
    if ext == PDF:
        return _pdf_text(path)
    if ext == DOCX:
        return _docx_text(path)
    if ext == XLSX:
        return _xlsx_text(path)
    if ext == PPTX:
        return _pptx_text(path)
    return _csv_text(path)


def source_text(path: str, pdf: str):
    """(the source's text, whether it came from OCR). `pdf` is the drawn / original PDF, for the OCR reading."""
    ext = os.path.splitext(path)[1].lower()
    if ext in IMAGE_EXTS:
        return ocr_text(pdf), True
    if ext == PDF:
        text = _pdf_text(path)
        return (text, False) if text.strip() else (ocr_text(path), True)       # no text layer: a scan
    return {PPTX: _pptx_text, XLSX: _xlsx_text, CSV: _csv_text, DOCX: _docx_text}[ext](path), False


def _ocr_reading(dump: str):
    """(text, True) from the pipeline's own OCR of each page, or None when it made none (a digital source, or no
    Tesseract): the check then reads the source itself."""
    import json
    try:
        with open(dump, encoding="utf-8") as fh:
            pages = json.load(fh)
    except (OSError, ValueError):
        return None
    return ("\n".join(pages[k] for k in sorted(pages, key=int)), True) if pages else None


def check_against_source(src: str, out: str, pdf: str, read=None):
    """(coverage %, missing words in source order, whether the source was OCR-read): how much of the source file
    survived into the output. `read` is the source's (text, ocr) when it was already read while the model ran."""
    text, ocr = read if read is not None else source_text(src, pdf)
    wanted = _tokens(text)
    if ocr:                                              # OCR reads speckle and decoration as 1-2 letter "words"
        wanted = [w for w in wanted if len(w) >= 3 or w.isdigit()]
    have = set(_tokens(_out_text(out)))
    if not wanted:
        if ocr:
            print("  source check skipped: the source has no text layer and Tesseract is not installed")
        return 100.0, [], ocr
    missing = [w for w in dict.fromkeys(wanted) if w not in have]
    kept = sum(1 for w in wanted if w in have)
    return 100.0 * kept / len(wanted), missing, ocr


def convert(src: str, to: str, outdir: str, base_url: str, model: str, pages: str, keep_work: bool = False) -> bool:
    """Write <outdir>/<name>.<to> and nothing else. The drawn PDF and the model's Markdown go in a scratch folder
    that is deleted afterwards (kept under <outdir>/<name>_work with keep_work)."""
    name = os.path.splitext(os.path.basename(src))[0]
    work = tempfile.mkdtemp(prefix="office_model_")
    try:
        return _convert(src, to, outdir, work, name, base_url, model, pages)
    finally:
        if keep_work:
            shutil.copytree(work, os.path.join(outdir, name + "_work"), dirs_exist_ok=True)
        shutil.rmtree(work, ignore_errors=True)


def _drawable(src: str, work: str, name: str) -> str:
    """The file to draw: a copy made easier to read (never the original), or the source itself."""
    path = os.path.abspath(src)
    suffix = path.lower()
    if suffix.endswith(CSV):                                         # drawn as text, so 45.00 stays 45.00
        as_book = os.path.join(work, f"{name}{XLSX}")
        csv_to_text_workbook(src, as_book)
        print("  drew the CSV as text cells (numbers keep their written form), one page wide")
        return as_book
    if suffix.endswith(XLSX):
        fitted = os.path.join(work, f"{name}{XLSX}")
        if fit_sheets_to_page(src, fitted):
            print("  set every sheet to one page wide (landscape) in the copy that is drawn")
            return fitted
        print("  the workbook has charts or pictures: drawn as it is (wide sheets may spread over several pages)")
        return path
    if suffix.endswith(PPTX):
        copy = os.path.join(work, f"{name}{PPTX}")
        changed = readable_headers(src, copy)
        if changed:
            print(f"  made {changed} header cell(s) readable (white text on a light fill) in the copy that is drawn")
        return copy
    return path


def _pages_of(to_draw: str, work: str, name: str) -> str:
    """The PDF whose pages the model reads: the PDF itself, a one-page PDF made from an image, or LibreOffice's pages."""
    suffix = os.path.splitext(to_draw)[1].lower()
    if suffix == PDF:                                                # already pages: a scan or a digital PDF
        return to_draw
    if suffix in IMAGE_EXTS:                                         # an image: a PDF page the pipeline can read
        pdf = os.path.join(work, f"{name}{PDF}")
        image_to_pdf(to_draw, pdf)
        print("  made a PDF page from the image")
        return pdf
    pdf = doc_pipeline.docx_to_pdf(to_draw, work)                    # LibreOffice draws any Office file or CSV
    print(f"  LibreOffice drew {pdf}")
    return pdf


def _report_checks(src: str, out_path: str, pdf: str, read, to: str) -> str:
    """Print the source check and the table count; return OK or REVIEW."""
    coverage, missing, ocr = check_against_source(src, out_path, pdf, read)
    floor = MIN_COVERAGE_OCR if ocr else MIN_COVERAGE
    print(f"\n  source check{' (OCR reading of the source)' if ocr else ''}: {coverage:.1f}% of the source's words "
          f"are in {os.path.basename(out_path)}")
    if missing:
        print(f"  missing from the output ({len(missing)}): {', '.join(missing[:15])}{' ...' if len(missing) > 15 else ''}")
    verdict = "OK" if coverage >= floor else "REVIEW"
    if to == "docx":
        want, got = table_counts(src, out_path)
        if want is not None:
            print(f"  tables: {got} in the Word file for {want} in the source")
        if want is not None and got != want:
            print("  (a different count can mean a table was cut in two, or a sheet or slide holds several tables)")
            verdict = "REVIEW"
    return verdict


def _render_only(pdf: str, to: str, outdir: str, name: str, from_image: bool, started: float) -> bool | None:
    """The routes no model reads: page images, and an Office file to PDF. None when the model has to read the pages."""
    out_path = os.path.join(outdir, f"{name}.{to}")
    if to == "image":
        written = render_pages(pdf, outdir, name)
        print(f"\n  OK: {len(written)} page image(s) in {outdir}  ({time.time() - started:.0f}s, no model)")
        return bool(written)
    if to == "pdf" and not from_image:
        shutil.copyfile(pdf, out_path)
        print(f"\n  OK: {out_path}  ({time.time() - started:.0f}s, no model: LibreOffice's own pages)")
        return True
    return None


def _convert(src: str, to: str, outdir: str, work: str, name: str, base_url: str, model: str, pages: str) -> bool:
    started = time.time()
    to_draw = _drawable(src, work, name)
    pdf = _pages_of(to_draw, work, name)
    from_image = os.path.splitext(to_draw)[1].lower() in IMAGE_EXTS
    rendered = _render_only(pdf, to, outdir, name, from_image, started)
    if rendered is not None:
        return rendered
    out_path = os.path.join(outdir, f"{name}.{to}")
    via_word = to == "pdf"                                           # image -> PDF: read into Word, then LibreOffice
    model_to = "docx" if via_word else to
    model_out = os.path.join(work, f"{name}{DOCX}") if via_word else out_path
    drawn_s = time.time() - started
    # a scan or image is read by Tesseract once, as each page is drawn, and the final check reuses that reading
    dump = os.path.join(work, "ocr.json")
    rc, secs = doc_pipeline._run_model_pipeline(os.path.abspath(pdf), model_to, model_out,
                                                os.path.join(work, f"{name}.md"), base_url, model, pages,
                                                extra=("--ocr-dump", dump))
    read = _ocr_reading(dump)
    if rc != 0 or not os.path.exists(model_out):
        print(f"\n  FAILED: {out_path}  ({secs:.0f}s)")
        return False
    if via_word:
        made = doc_pipeline.docx_to_pdf(model_out, work + "_pdf")
        shutil.copyfile(made, out_path)
        shutil.rmtree(work + "_pdf", ignore_errors=True)
    checking = time.time()
    verdict = _report_checks(src, out_path, pdf, read, to)
    print(f"  time: drawing the pages {drawn_s:.0f}s, reading and writing {secs:.0f}s, "
          f"checking against the source {time.time() - checking:.0f}s")
    print(f"  {verdict}: {out_path}  ({time.time() - started:.0f}s)")
    return verdict == "OK"


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("files", nargs="+", help=".pptx .xlsx .csv .pdf .docx .png .jpg .tif file(s)")
    parser.add_argument("--to", required=True, choices=("docx", "xlsx", "pptx", "csv", "pdf", "image"))
    parser.add_argument("--outdir", default=paths.out("office_model_output"))
    parser.add_argument("--pages", default="all", help="drawn pages to read, e.g. 1, 1-3, 2,4 (default: all)")
    parser.add_argument("--keep-work", action="store_true",
                        help="also keep the drawn PDF and the model's Markdown, in <outdir>/<name>_work/")
    parser.add_argument("--model", default="qwen3-vl:2b-instruct", help="model name (a raw llama-server ignores it)")
    parser.add_argument("--base-url", default=MODEL_URL, help=f"the llama-server to use (default {MODEL_URL}; IDOX_BASE_URL sets it)")
    args = parser.parse_args()

    outdir = os.path.abspath(args.outdir)
    os.makedirs(outdir, exist_ok=True)
    ok = fail = 0
    for src in args.files:
        ext = os.path.splitext(src)[1].lower()
        print("=" * 72)
        print(f"{src}  ->  {args.to}")
        print("=" * 72)
        if ext not in ROUTES or args.to not in ROUTES[ext]:
            print(f"  {ext or 'this file'} -> {args.to} is not offered here (offered: {', '.join(ROUTES.get(ext, ()))})")
            fail += 1
            continue
        try:
            good = convert(src, args.to, outdir, args.base_url, args.model, args.pages, args.keep_work)
        except Exception as exc:
            print(f"  FAILED: {exc}")
            good = False
        ok += good
        fail += not good
    if len(args.files) > 1:
        print("=" * 72)
        print(f"{ok} succeeded, {fail} failed, {len(args.files)} total")
    sys.exit(1 if fail else 0)


if __name__ == "__main__":
    main()
