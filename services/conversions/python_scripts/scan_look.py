"""
scan_look.py -- what a scanned page looks like beyond its words, measured from the picture of the page.

A scan has no text layer, so the model and Tesseract give words and line positions but nothing about colour or
graphics. Two things are measured here, with PIL alone:

  find_graphic_regions  banners, charts and other large coloured areas (a shaded panel, a filled title bar) are cut out
                        of the scan and placed in the Word file as pictures. Their lettering is part of the picture,
                        so the text blocks inside them are dropped by the caller (ocr_layout._drop_picture_text).
  apply_scan_colours    the colour of each text line and the fill behind each table header cell, sampled from the
                        pixels, are written onto the finished Word file's runs and cells.

Neither changes any word. A region is turned into a picture only when it is large, filled, free of table text and mostly
not text, so a shaded paragraph or a table header stays editable text.
"""

import hashlib
import os
import re

from PIL import Image

FILL_DIFF = 14           # a pixel differs from the page background by more than this (sum of channel differences)
FILL_SHARE = 0.65        # a grid cell is "filled" when this share of its pixels differ from the background
MIN_AREA, MIN_W, MIN_H = 0.06, 0.15, 0.05      # of the page: area, width, height of a graphic region (a table header strip is smaller)
MAX_AREA = 0.45          # a region covering more than this is the page's own background, not a graphic
MAX_TEXT_COVER = 0.35    # share of the region its OCR lines may cover: more means it is a shaded paragraph
MAX_TEXT_COVER_DARK = 0.8   # ... but a dark filled bar is a title banner whatever its lettering covers
MAX_PER_PAGE = 4
COLOUR_MIN_SPREAD = 40   # max-min of the channels: below this a text colour is a grey / black and is left alone


def _norm(s: str) -> str:
    return re.sub(r"\W+", "", str(s).lower())


def _bg_colour(img: Image.Image, box=None):
    """The commonest colour of the page (or of a box), ignoring small noise."""
    region = img.crop(box) if box else img
    small = region.resize((max(1, min(48, region.width)), max(1, min(48, region.height))), Image.BOX).convert("RGB")
    counts = {}
    for px in small.getdata():
        key = tuple(v // 12 * 12 for v in px)
        counts[key] = counts.get(key, 0) + 1
    key = max(counts, key=counts.get)
    pts = [px for px in small.getdata() if tuple(v // 12 * 12 for v in px) == key]
    return tuple(sum(c[i] for c in pts) // len(pts) for i in range(3))


def _distance(a, b) -> int:
    return sum(abs(x - y) for x, y in zip(a, b))


def _ink_colour(img: Image.Image, box, bg):
    """The colour of the lettering in a box: the average of the pixels farthest from the background."""
    px = list(img.crop(box).convert("RGB").getdata())
    if not px:
        return None
    far = sorted(px, key=lambda p: -_distance(p, bg))[:max(3, len(px) // 8)]
    return tuple(sum(c[i] for c in far) // len(far) for i in range(3))


def _hex(rgb) -> str:
    return "%02X%02X%02X" % tuple(rgb)


def _is_table_line(line, cells) -> bool:
    n = _norm(line["text"])
    return sum(1 for c in cells if c and c in n) >= 2


def _cell_word_share(words_text, cells) -> float:
    """The share of a region's OCR words that are words of the model's table cells: a shaded table header is all of
    them, a chart's labels or a banner's title almost none."""
    if not words_text:
        return 0.0
    hits = sum(1 for t in words_text if len(t) >= 3 and any(t in c for c in cells))
    return hits / len(words_text)


def find_graphic_regions(png_path, lines, cells, out_dir, pno, sc, taken=()):
    """Large filled graphics of a scanned page, cut out as pictures. `lines` are the OCR lines in pixels, `cells` the
    normalised table cell texts, `sc` points per pixel, `taken` boxes (in pixels) already holding a picture."""
    img = Image.open(png_path).convert("RGB")
    W, H = img.size
    cell = max(6, W // 100)
    gw, gh = W // cell, H // cell
    if gw < 10 or gh < 10:
        return []
    bg = _bg_colour(img)
    small = img.resize((gw * 4, gh * 4), Image.BOX)
    sx = small.load()
    filled = [[False] * gw for _ in range(gh)]
    for y in range(gh):
        for x in range(gw):
            n = sum(1 for yy in range(4) for xx in range(4) if _distance(sx[x * 4 + xx, y * 4 + yy], bg) > FILL_DIFF)
            filled[y][x] = n / 16.0 >= FILL_SHARE
    seen = [[False] * gw for _ in range(gh)]
    regions = []
    for y in range(gh):
        for x in range(gw):
            if not filled[y][x] or seen[y][x]:
                continue
            stack, xs, ys = [(x, y)], [], []
            seen[y][x] = True
            while stack:
                cx, cy = stack.pop()
                xs.append(cx), ys.append(cy)
                for nx in (cx - 1, cx, cx + 1):
                    for ny in (cy - 1, cy, cy + 1):
                        if 0 <= nx < gw and 0 <= ny < gh and filled[ny][nx] and not seen[ny][nx]:
                            seen[ny][nx] = True
                            stack.append((nx, ny))
            x0, y0, x1, y1 = min(xs) * cell, min(ys) * cell, (max(xs) + 1) * cell, (max(ys) + 1) * cell
            fw, fh = (x1 - x0) / W, (y1 - y0) / H
            if not (MIN_AREA <= fw * fh <= MAX_AREA and fw >= MIN_W and fh >= MIN_H):
                continue
            words = [w for l in lines for w in l.get("words", ())
                     if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]
            texts = [_norm(t) for l in lines for w, t in zip(l.get("words", ()), l["text"].split())
                     if x0 <= (w[0] + w[2]) / 2 <= x1 and y0 <= (w[1] + w[3]) / 2 <= y1]
            if _cell_word_share(texts, cells) >= 0.5:
                continue                              # a shaded table header: its words are the table's cells
            cover = sum((w[2] - w[0]) * (w[3] - w[1]) for w in words) / max(1, (x1 - x0) * (y1 - y0))
            fill = _bg_colour(img, (x0, y0, x1, y1))
            dark = sum(fill) / 3 < 140            # a dark title bar is mostly lettering by OCR's wide line boxes
            if cover > (MAX_TEXT_COVER_DARK if dark else MAX_TEXT_COVER):
                continue
            if any(not (x1 <= t[0] or t[2] <= x0 or y1 <= t[1] or t[3] <= y0) for t in taken):
                continue
            regions.append((y0, x0, x1, y1))
    pics = []
    for k, (y0, x0, x1, y1) in enumerate(sorted(regions)[:MAX_PER_PAGE]):
        crop = img.crop((x0, y0, x1, y1))
        os.makedirs(out_dir, exist_ok=True)
        path = os.path.join(out_dir, f"p{pno + 1:03d}_scanregion{k:02d}.png")
        crop.save(path)
        data = open(path, "rb").read()
        pics.append({"path": path, "ext": "png", "bytes": len(data), "sha1": hashlib.sha1(data).hexdigest(),
                     "px": crop.size, "bbox_pt": (x0 * sc, y0 * sc, x1 * sc, y1 * sc)})
    return pics


def apply_scan_colours(docx_path, pages, plan) -> list:
    """Write the colour of each text line and each table header cell's fill, measured from the scan, onto the .docx.
    Returns notes. Paragraphs and tables are matched to the blocks in reading order by their text."""
    import docx
    from docx.oxml import OxmlElement
    from docx.oxml.ns import qn
    from docx.shared import RGBColor

    d = docx.Document(docx_path)
    paras = [p for p in d.paragraphs if p.text.strip()]
    tables = list(d.tables)
    pi_cursor, ti_cursor, coloured, shaded = 0, 0, 0, 0
    from blocks import TableBlock
    for page, info in zip(pages, plan["pages"]):
        img = Image.open(info["png"]).convert("RGB")
        bg_page = _bg_colour(img)
        for bi, block in enumerate(page.blocks):
            li = info["idx"].get(bi)
            if isinstance(block, TableBlock):
                if ti_cursor >= len(tables):
                    continue
                table = tables[ti_cursor]
                ti_cursor += 1
                for ci, label in enumerate(block.header[:len(table.rows[0].cells)]):
                    box = next(((l["x0"], l["top"], l["x1"], l["bottom"]) for l in info["lines"]
                                if _norm(label) and _norm(label) == _norm(l["text"])), None)
                    if not box:
                        continue
                    pad = 4
                    around = (max(0, box[0] - pad), max(0, box[1] - pad), box[2] + pad, box[3] + pad)
                    bg = _bg_colour(img, around)
                    cell = table.rows[0].cells[ci]
                    if _distance(bg, bg_page) > 2 * FILL_DIFF:
                        tc_pr = cell._tc.get_or_add_tcPr()
                        shade = OxmlElement("w:shd")
                        shade.set(qn("w:val"), "clear"), shade.set(qn("w:color"), "auto"), shade.set(qn("w:fill"), _hex(bg))
                        tc_pr.append(shade)
                        ink = _ink_colour(img, box, bg)
                        for par in cell.paragraphs:
                            for run in par.runs:
                                if ink:
                                    run.font.color.rgb = RGBColor(*ink)
                        shaded += 1
                continue
            if li is None or not block.text.strip():
                continue
            key = _norm(block.text.split("\n")[0])[:24]
            while pi_cursor < len(paras) and _norm(paras[pi_cursor].text)[:24] != key:
                pi_cursor += 1
            if pi_cursor >= len(paras):
                pi_cursor = 0
                continue
            ln = info["lines"][li]
            box = (ln["x0"], ln["top"], ln["x1"], ln["bottom"])
            bg = _bg_colour(img, box)
            ink = _ink_colour(img, box, bg)
            if ink and max(ink) - min(ink) >= COLOUR_MIN_SPREAD:
                for run in paras[pi_cursor].runs:
                    run.font.color.rgb = RGBColor(*ink)
                coloured += 1
            pi_cursor += 1
    d.save(docx_path)
    notes = []
    if coloured:
        notes.append(f"{coloured} text line(s) coloured as in the scan")
    if shaded:
        notes.append(f"{shaded} table header cell(s) filled as in the scan")
    return notes
