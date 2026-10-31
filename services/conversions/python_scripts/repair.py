"""
repair.py -- put lines the model dropped back where they belong, copied from the PDF's own text.

WHY THIS REPLACES THE OLD PLACEMENT. repair_missing_lines() in test_pdf.py and pdf_to_txt.py restored a dropped line
as a NEW BLOCK, at a position worked out from "what share of the page's characters sit above it" times the number of
blocks. Blocks are very different sizes, so the line landed somewhere near but not at its place, and a line that was
really the middle of a paragraph became a stray paragraph or, when its font was larger, a stray HEADING. On a dense
three-page text it left the Word, Excel, PowerPoint, XML and CSV outputs with the right words in the wrong order.

HOW THIS ONE DECIDES (digital PDFs only: the text layer is the author's own characters)
  1. The page's words are aligned, in order, with the PDF's words (difflib). The alignment is MONOTONIC, so a phrase
     that occurs twice in the document (Lorem ipsum does) can only match at its own place.
  2. A PDF line is MISSING only if few of its words are matched. A line the model merely misread or joined into a
     paragraph is matched and is NOT restored again (that is what doubled the text before).
  3. Each missing line is placed next to its real neighbours: the block holding the nearest matched word before it
     and the one holding the nearest matched word after it.
       - both neighbours are the same paragraph  -> the line is SPLICED INTO that paragraph at its exact place;
       - it continues the paragraph above it (same size, no paragraph gap) -> it is appended to that paragraph;
       - otherwise -> a new block right after the block above it.
     Consecutive missing lines of one paragraph are restored together as one piece of text.
  4. Lines inside a detected table are never restored as prose (they are reported), exactly as before.

restore_missing_lines() has the same contract as the function it replaces: (new_page, repairs, skipped).

KNOWN LIMIT. Which lines count as missing is decided by check_coverage() in blocks.py, which asks only whether a line's
text appears ANYWHERE in the output, so a dropped line that repeats text found elsewhere on the page is never flagged.
restore_missing_lines(scan_all=True) can find such lines by alignment, but it is OFF in the pipeline on purpose: where text
repeats, the alignment cannot tell which copy a line belongs to, and on a Lorem-ipsum test document it put restored text
in the wrong place (order score 90.6 -> 48.1). A repeated line that was dropped cannot be placed reliably.
"""

import difflib
import re

import pymupdf

from blocks import Page, TableBlock, TextBlock, normalize

SAME_PARAGRAPH_GAP = 0.6        # a line within this many line-heights of the previous one continues its paragraph
COVERED = 0.6                   # a PDF line is "covered" when at least this share of its words is matched


def _pdf_lines(pdf_page):
    """Every text line of the page in the text layer's order, and the body font size."""
    lines, sizes = [], {}
    for block in pdf_page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            text = "".join(s["text"] for s in line["spans"]).strip()
            if not text:
                continue
            size = max((s["size"] for s in line["spans"]), default=10)
            lines.append({"text": text, "bbox": line["bbox"], "size": size})
            sizes[round(size)] = sizes.get(round(size), 0) + len(text)
    return lines, (max(sizes, key=sizes.get) if sizes else 10)


def _block_tokens(block):
    """(display tokens, whether each is a word) for a block; a table is one run of its cell words."""
    if isinstance(block, TableBlock):
        text = " ".join(list(block.header) + [c for row in block.rows for c in row])
    else:
        text = block.text
    tokens = re.findall(r"\S+|\s+", text)
    return tokens


def _same_paragraph(prev, cur):
    height = max(prev["bbox"][3] - prev["bbox"][1], 1.0)
    gap = cur["bbox"][1] - prev["bbox"][3]
    return abs(cur["size"] - prev["size"]) < 0.5 and gap <= SAME_PARAGRAPH_GAP * height


MIN_SCAN = 20           # with scan_all, an unflagged line must be at least this long (normalised) to be restored;
                        # shorter ones are page numbers, bullets and the like

def _align(page, lines):
    """Align the PDF's words with the page's words, in order.

    Returns (pdf_norm, line_words, pdf_to_out, out_norm, out_pos, block_tokens): the PDF's normalised words, the
    word indices of each PDF line, which output word each matched PDF word lines up with, the output's normalised
    words, where each sits as (block index, token index), and each block's display tokens.
    """
    pdf_norm = []
    line_words = []                                  # per line: indices into pdf_norm
    for line in lines:
        idx = []
        for tok in line["text"].split():
            n = normalize(tok)
            if n:
                idx.append(len(pdf_norm))
                pdf_norm.append(n)
        line_words.append(idx)

    out_norm, out_pos = [], []                       # out_pos[j] = (block index, token index in that block)
    block_tokens = [_block_tokens(b) for b in page.blocks]
    for bi, tokens in enumerate(block_tokens):
        for ti, tok in enumerate(tokens):
            n = normalize(tok) if tok.strip() else ""
            if n:
                out_norm.append(n)
                out_pos.append((bi, ti))

    matcher = difflib.SequenceMatcher(None, pdf_norm, out_norm, autojunk=False)
    pdf_to_out = {}
    for i, j, n in matcher.get_matching_blocks():
        for t in range(n):
            pdf_to_out[i + t] = j + t
    return pdf_norm, line_words, pdf_to_out, out_norm, out_pos, block_tokens


def restore_missing_lines(page, pdf_page, missing_lines, scan_all=False):
    """Returns (new_page, repairs, skipped); see the module docstring.

    scan_all=True does not trust the caller's list of missing lines alone: every PDF line the alignment cannot find
    in the output (and that is at least MIN_SCAN characters) is restored. That catches a dropped line whose text
    also occurs elsewhere on the page, which the caller's "is it anywhere in the output?" check cannot see.
    """
    if not missing_lines and not scan_all:
        return page, [], []

    wanted = {normalize(m) for m in (missing_lines or [])}
    lines, body = _pdf_lines(pdf_page)
    if not lines:
        return page, [], []
    try:
        table_boxes = [pymupdf.Rect(t.bbox) for t in pdf_page.find_tables().tables]
    except Exception:
        table_boxes = []

    pdf_norm, line_words, pdf_to_out, out_norm, out_pos, block_tokens = _align(page, lines)

    # --- which lines are truly missing ----------------------------------------------------------------------
    missing, skipped = [], []
    for k, line in enumerate(lines):
        words = line_words[k]
        key = normalize(line["text"])
        if not words or (key not in wanted and not (scan_all and len(key) >= MIN_SCAN)):
            continue
        if sum(1 for w in words if w in pdf_to_out) / len(words) >= COVERED:
            continue                                  # present (perhaps misread or joined): not restored again
        rect = pymupdf.Rect(line["bbox"])
        if any(abs(rect & tb) / max(abs(rect), 1) > 0.5 for tb in table_boxes):
            skipped.append(line["text"])
            continue
        missing.append(k)
    if not missing:
        return page, [], skipped

    # --- group consecutive missing lines that are one paragraph -----------------------------------------------
    runs = []
    for k in missing:
        if runs and runs[-1][-1] == k - 1 and _same_paragraph(lines[k - 1], lines[k]):
            runs[-1].append(k)
        else:
            runs.append([k])

    # --- plan: for each run, a splice into a block's text, or a new block after some block -------------------
    splices = {}                                       # block index -> [(token index to insert after, text)]
    inserts = []                                       # (after block index or -1, order key, block, note)
    repairs = []
    for run in runs:
        first, last = run[0], run[-1]
        text = " ".join(lines[k]["text"] for k in run)
        s_idx, e_idx = line_words[first][0], line_words[last][-1]
        prev_i = next((i for i in range(s_idx - 1, -1, -1) if i in pdf_to_out), None)
        next_i = next((i for i in range(e_idx + 1, len(pdf_norm)) if i in pdf_to_out), None)
        jp = pdf_to_out[prev_i] if prev_i is not None else None
        jq = pdf_to_out[next_i] if next_i is not None else None
        bp, tp = out_pos[jp] if jp is not None else (None, None)
        bq = out_pos[jq][0] if jq is not None else None

        size = lines[first]["size"]
        kind = "heading" if size >= body * 1.25 else "caption" if size <= body * 0.88 else "paragraph"
        note_head = f"{text[:50]!r} ({size:.0f}pt vs {body}pt body"

        def is_prose(bi):
            return bi is not None and isinstance(page.blocks[bi], TextBlock) and page.blocks[bi].kind == "paragraph"

        # inside one paragraph: splice at the exact place
        if bp is not None and bp == bq and is_prose(bp):
            splices.setdefault(bp, []).append((tp, text))
            repairs.append(f"{note_head} -> spliced into paragraph {bp})")
            continue
        # continues the paragraph above it
        prev_line = lines[first - 1] if first > 0 else None
        if (bp is not None and is_prose(bp) and prev_line is not None and kind == "paragraph"
                and jp == max(j for j, (b, _) in enumerate(out_pos) if b == bp)
                and _same_paragraph(prev_line, lines[first])):
            splices.setdefault(bp, []).append((tp, text))
            repairs.append(f"{note_head} -> appended to paragraph {bp})")
            continue
        # a block of its own, right after the block above it
        align = "center" if abs((lines[first]["bbox"][0] + lines[first]["bbox"][2]) / 2
                                - pdf_page.rect.width / 2) < 20 else "left"
        block = TextBlock(kind=kind, align=align,
                          size="large" if kind == "heading" else "small" if kind == "caption" else "normal",
                          bold=False, text=text)
        after = bp if bp is not None else (bq - 1 if bq is not None else len(page.blocks) - 1)
        if lines[first]["bbox"][3] > EDGE_BOTTOM * pdf_page.rect.height:       # a footer goes last, not where the
            after = len(page.blocks) - 1                                         # text layer happens to list it
        inserts.append((after, first, block))
        repairs.append(f"{note_head} -> {kind}, new block after block {after})")

    # --- apply -------------------------------------------------------------------------------------------------
    new_blocks = list(page.blocks)
    for bi, items in splices.items():
        tokens = list(block_tokens[bi])
        for tp, text in sorted(items, key=lambda it: it[0], reverse=True):
            tokens[tp + 1:tp + 1] = [" ", text]        # after the word at tp, with a space on each side
        old = new_blocks[bi]
        new_blocks[bi] = TextBlock(kind=old.kind, align=old.align, size=old.size, bold=old.bold,
                                   text="".join(tokens).replace("  ", " "))
    result = []
    for after, _, block in sorted((i for i in inserts if i[0] < 0), key=lambda i: i[1]):
        result.append(block)
    for bi, block in enumerate(new_blocks):
        result.append(block)
        for after, _, extra in sorted((i for i in inserts if i[0] == bi), key=lambda i: i[1]):
            result.append(extra)
    return Page(analysis=page.analysis, blocks=result), repairs, skipped


# ---------------------------------------------------------------------------------------------------------------
# Putting blocks the model wrote in the wrong ORDER back in the PDF's order
# ---------------------------------------------------------------------------------------------------------------
EDGE_BOTTOM = 0.90               # a whole-line block whose text sits below this share of the page height is a footer
EDGE_TOP = 0.05                  # ... above this share, a running header
SHINGLE = 6                     # a block is located by its first words; this many must match to count


def _block_text(block):
    if isinstance(block, TableBlock):
        return " ".join(list(block.header) + [c for row in block.rows for c in row])
    return block.text


def reorder_blocks(page, pdf_page):
    """Return (new_page, moves). Blocks go back into the order of the PDF's own text.

    The model sometimes reads a page's blocks in the wrong order (a table before the caption and heading that sit
    above it). Each block is located in the PDF's word stream by its first words. Only an UNAMBIGUOUS location is
    trusted: if the same opening words occur more than once in the document (repeated text), the block is left
    where it is. A block that cannot be located (text read out of a picture, say) stays right behind the block
    before it. The sort is stable, so blocks that are already in order never move.
    """
    lines, _ = _pdf_lines(pdf_page)
    pdf_norm = [normalize(t) for line in lines for t in line["text"].split()]
    pdf_norm = [w for w in pdf_norm if w]
    if len(pdf_norm) < SHINGLE or len(page.blocks) < 2:
        return page, []

    # where each whole PDF line starts in the word stream, and which lines sit in the page's top / bottom margin
    whole_lines, pos, edge_of = {}, 0, {}
    height = pdf_page.rect.height
    for line in lines:
        ws = [w for w in (normalize(t) for t in line["text"].split()) if w]
        if ws:
            whole_lines.setdefault(tuple(ws), []).append(pos)
            pos += len(ws)
            if line["bbox"][3] > EDGE_BOTTOM * height:
                edge_of[tuple(ws)] = 1                  # a footer: stays last, whatever the text layer's order
            elif line["bbox"][1] < EDGE_TOP * height:
                edge_of[tuple(ws)] = -1                 # a running header: stays first

    # where each run of SHINGLE consecutive words starts in the PDF
    where = {}
    for i in range(len(pdf_norm) - SHINGLE + 1):
        where.setdefault(tuple(pdf_norm[i:i + SHINGLE]), []).append(i)

    anchors, edges = [], []
    for block in page.blocks:
        words = [w for w in (normalize(t) for t in _block_text(block).split()) if w]
        edges.append(edge_of.get(tuple(words), 0) if not isinstance(block, TableBlock) else 0)
        anchor = None
        if len(words) >= SHINGLE:
            hits = where.get(tuple(words[:SHINGLE]), [])
            if len(hits) == 1:
                anchor = hits[0]
        elif len(words) >= 2:
            # a short block (a heading): located only when its text is an ENTIRE line of the PDF that occurs once.
            # A fragment of a longer line does not count: text read out of a picture often repeats part of a caption.
            hits = whole_lines.get(tuple(words), [])
            if len(hits) == 1:
                anchor = hits[0]
        anchors.append(anchor)

    # a block with no anchor sorts right behind the block before it
    keys, last = [], -1.0
    for n, anchor in enumerate(anchors):
        if edges[n]:                                     # header / footer: pinned to the page edge
            keys.append((edges[n] * 1e9, n))
            continue
        last = float(anchor) if anchor is not None else last + 1e-6
        keys.append((last, n))
    order = [n for _, n in sorted(keys)]
    if order == list(range(len(order))):
        return page, []

    moves = []
    for new_pos, n in enumerate(order):
        if n != new_pos:
            moves.append(f"{_block_text(page.blocks[n])[:40]!r} moved from position {n} to {new_pos}")
    return Page(analysis=page.analysis, blocks=[page.blocks[n] for n in order]), moves


RESEGMENT_MIN_LINES = 3          # a block must hold at least this many PDF lines in order to be split up


def _styled_lines(pdf_page):
    """Every text line of the page in reading order: text, bbox, size, bold."""
    out = []
    for block in pdf_page.get_text("dict")["blocks"]:
        if block["type"] != 0:
            continue
        for line in block["lines"]:
            text = "".join(s["text"] for s in line["spans"]).strip()
            if text:
                first = next((s for s in line["spans"] if s["text"].strip()), line["spans"][0])
                out.append({"text": text, "bbox": line["bbox"], "font": first["font"], "color": first["color"],
                            "size": max(s["size"] for s in line["spans"]),
                            "bold": all(s["flags"] & 16 or "bold" in s["font"].lower() for s in line["spans"] if s["text"].strip())})
    out.sort(key=lambda l: (round(l["bbox"][1]), l["bbox"][0]))
    return out


def resegment_blocks(page, pdf_page):
    """Return (new_page, notes). A text block that holds several of the page's paragraphs (a heading, the paragraph under
    it, a caption, even a whole table flattened to words) is split back into the PDF's own headings, paragraphs and
    tables, in the PDF's order. The model sometimes writes a page as one block; the repair steps that follow only splice
    lines into it, so a table was lost to prose and a figure caption landed far from its figure.

    The PDF's lines must run one after another through the block's text (a chain); a block they do not cover is left
    alone. Table cells come from the detected table, never from the flattened words."""
    from blocks import TableBlock, TextBlock
    lines = _styled_lines(pdf_page)
    if len(lines) < RESEGMENT_MIN_LINES:
        return page, []
    try:
        detected = [(t.bbox, [[" ".join(w[4] for w in sorted(
                        (w for w in pdf_page.get_text("words")
                         if pymupdf.Rect(c).contains(pymupdf.Point((w[0] + w[2]) / 2, (w[1] + w[3]) / 2))),
                        key=lambda w: (round(w[1] / 3), w[0]))) if c else "" for c in row.cells] for row in t.rows])
                    for t in pdf_page.find_tables().tables]
    except Exception:
        detected = []
    detected = [(bb, [r for r in g if any(r)]) for bb, g in detected]
    detected = [(bb, g) for bb, g in detected if len(g) >= 2 and len(g[0]) >= 2]
    model_tables = [b for b in page.blocks if isinstance(b, TableBlock)]
    body = _pdf_lines(pdf_page)[1]

    def in_table(line):
        x0, y0, x1, y1 = line["bbox"]
        for k, (bb, _g) in enumerate(detected):
            if bb[0] - 2 <= x0 and x1 <= bb[2] + 2 and bb[1] - 2 <= y0 and y1 <= bb[3] + 2:
                return k
        return None

    def sq(t):
        return re.sub(r"\W+", "", normalize(t))

    new_blocks, notes, used, emitted = [], [], set(), set()
    for block in page.blocks:
        if isinstance(block, TableBlock) or not isinstance(block, TextBlock) or block.kind == "image":
            new_blocks.append(block)
            continue
        if not block.text.strip():
            continue                                       # an empty block says nothing
        covered, best_len = _lines_in_block(block.text, lines, used)
        k = len(covered)
        text = sq(block.text)
        if k < RESEGMENT_MIN_LINES or best_len < 0.6 * len(text):
            new_blocks.append(block)
            continue
        # group the covered lines: a heading is its own block, a paragraph is a run of same-size lines close together
        groups, tables_done = [], set(emitted)
        for i in covered:
            ln = lines[i]
            t = in_table(ln)
            if t is not None:
                if t not in tables_done and not any(_cells_overlap(mt, detected[t][1]) for mt in model_tables):
                    groups.append(("table", t))
                tables_done.add(t)
                emitted.add(t)
                continue
            heading = ln["size"] >= body * 1.15 or (ln["bold"] and len(ln["text"]) < 90)
            prev = groups[-1] if groups else None
            if (prev and prev[0] == "text" and not heading and not prev[3] and
                    _same_paragraph(lines[prev[2][-1]], ln)):
                prev[2].append(i)
            else:
                groups.append(("text", None, [i], heading))
        for g in groups:
            if g[0] == "table":
                grid = detected[g[1]][1]
                new_blocks.append(TableBlock(kind="table", has_header=True, n_data_rows=len(grid) - 1,
                                             n_cols=len(grid[0]), header=grid[0], rows=grid[1:]))
            else:
                idx, heading = g[2], g[3]
                first = lines[idx[0]]
                kind = "heading" if heading else "paragraph"
                size = "large" if first["size"] >= body * 1.25 else "normal"
                new_blocks.append(TextBlock(kind=kind, align="left", size=size, bold=bool(first["bold"] and heading),
                                            text=" ".join(lines[i]["text"] for i in idx)))
        used.update(covered)
        notes.append(f"split one block of {k} PDF lines back into {len(groups)} block(s) "
                     f"({sum(1 for g in groups if g[0] == 'table')} table(s))")
    if not notes:
        return page, []
    return Page(analysis=page.analysis, blocks=new_blocks), notes


def _word_list(text: str) -> list:
    return re.findall(r"\w+", text.lower())


def _find_run(words: list, run: list) -> list:
    """Every index where `run` occurs inside `words` as consecutive words."""
    n = len(run)
    return [i for i in range(len(words) - n + 1) if words[i:i + n] == run] if n else []


def _lines_in_block(block_text: str, lines: list, used: set):
    """(indexes of the PDF lines the block was written from, in PDF order; their total length).

    A line counts when its words occur in the block as one consecutive run. A line of one or two words is too easy to
    find by accident ("work." inside some other sentence), so it counts only when it sits right beside a line that
    already counts, in the block as well as on the page. The block's own order does not matter: the model sometimes
    writes a page's paragraphs in another order, and the lines are written out in the PDF's order."""
    words = _word_list(block_text)
    found = {}                                        # line index -> (start in the block, length in words)
    for i, ln in enumerate(lines):
        w = _word_list(ln["text"])
        if i in used or len(w) < 3:
            continue
        at = _find_run(words, w)
        if len(at) == 1:
            found[i] = (at[0], len(w))
    for _ in range(2):                                # short lines: beside a counted line, in both orders
        for i, ln in enumerate(lines):
            w = _word_list(ln["text"])
            if i in used or i in found or not w or len(w) >= 3:
                continue
            at = _find_run(words, w)
            for j in (i - 1, i + 1):
                if j in found and any((found[j][0] + found[j][1] == p) if j < i else (p + len(w) == found[j][0]) for p in at):
                    found[i] = (next(p for p in at if ((found[j][0] + found[j][1] == p) if j < i else (p + len(w) == found[j][0]))), len(w))
                    break
    covered = sorted(found)
    return covered, sum(len("".join(_word_list(lines[i]["text"]))) for i in covered)


RUNNING_FOOTER_FROM = 0.92       # a line whose bottom is below this share of the page height is in the footer margin
RUNNING_HEADER_TO = 0.06         # ... one whose top is above this share is in the header margin
RUNNING_SHARE = 0.6              # a line is "running" when it repeats on this share of the pages (and on at least two)


def _running_key(text: str) -> str:
    return re.sub(r"\d+", "#", " ".join(text.lower().split()))


def find_running_lines(pdf_pages):
    """Per page, the lines of the footer / header margin that repeat across pages, as rows: lines at one height, left to
    right, each (text, x0, x1, font, size, bold, rgb). Repetition is judged with the numbers blanked, so "Page 2" and
    "Page 3" are one running line. Returns (footer_rows_per_page, header_rows_per_page)."""
    if len(pdf_pages) < 2:
        return [[] for _ in pdf_pages], [[] for _ in pdf_pages]
    margin = []
    for pg in pdf_pages:
        H = pg.rect.height
        rows = {"footer": [], "header": []}
        for ln in _styled_lines(pg):
            where = "footer" if ln["bbox"][3] > RUNNING_FOOTER_FROM * H else "header" if ln["bbox"][1] < RUNNING_HEADER_TO * H else None
            if where:
                rows[where].append(ln)
        margin.append(rows)
    seen = {}
    for rows in margin:
        for k in {_running_key(l["text"]) for side in rows.values() for l in side}:
            seen[k] = seen.get(k, 0) + 1
    need = max(2, -(-len(pdf_pages) * 6 // 10))
    out = {"footer": [], "header": []}
    for rows in margin:
        for side in ("footer", "header"):
            lines = [l for l in rows[side] if seen.get(_running_key(l["text"]), 0) >= need]
            lines.sort(key=lambda l: (round(l["bbox"][1] / 3), l["bbox"][0]))
            grouped = []
            for l in lines:
                if grouped and abs(grouped[-1][0]["bbox"][1] - l["bbox"][1]) <= 3:
                    grouped[-1].append(l)
                else:
                    grouped.append([l])
            out[side].append([[(l["text"], l["bbox"][0], l["bbox"][2], l["font"], l["size"], l["bold"], l["color"]) for l in g]
                              for g in grouped])
    return out["footer"], out["header"]


def strip_running_lines(pages, footer_rows, header_rows):
    """Return (new_pages, count). The running lines are cut out of each page's text blocks (from the end for a footer,
    the start for a header); a block left empty is dropped. They go into the file's real footer / header instead."""
    from blocks import TableBlock, TextBlock
    out, count = [], 0
    for page, frows, hrows in zip(pages, footer_rows, header_rows):
        blocks = list(page.blocks)
        for rows, at_end in ((frows, True), (hrows, False)):
            for row in (reversed(rows) if at_end else rows):
                for part in (reversed(row) if at_end else row):
                    tokens = part[0].split()
                    if not tokens:
                        continue
                    pattern = r"\s*".join(re.escape(t) for t in tokens)
                    pattern = (pattern + r"\s*$") if at_end else (r"^\s*" + pattern)
                    for i in (range(len(blocks) - 1, -1, -1) if at_end else range(len(blocks))):
                        b = blocks[i]
                        if isinstance(b, TableBlock) or not isinstance(b, TextBlock):
                            continue
                        new = re.sub(pattern, "", b.text, count=1, flags=re.I)
                        if new != b.text:
                            count += 1
                            blocks[i] = b.model_copy(update={"text": new.strip()}) if new.strip() else None
                            break
                    blocks = [b for b in blocks if b is not None]
        out.append(Page(analysis=page.analysis, blocks=blocks))
    return out, count


def merge_continued_tables(pages, pdf_pages):
    """Return (new_pages, notes). A table that runs from the bottom of one page onto the top of the next is one table:
    the model read the two halves as two tables, and took the first data row of the second half for its header.
    Joined when both pages' tables have the same number of columns and the PDF's own tables sit at the page bottom and
    the next page's top. A second half that repeats the first half's header loses the repeat."""
    from blocks import TableBlock
    pages, notes = list(pages), []
    for i in range(len(pages) - 1):
        a, b = pages[i].blocks, pages[i + 1].blocks
        if not a or not b or not isinstance(a[-1], TableBlock) or not isinstance(b[0], TableBlock):
            continue
        ta, tb = a[-1], b[0]
        if ta.n_cols != tb.n_cols:
            continue
        try:
            ends = max(t.bbox[3] for t in pdf_pages[i].find_tables().tables) / pdf_pages[i].rect.height
            starts = min(t.bbox[1] for t in pdf_pages[i + 1].find_tables().tables) / pdf_pages[i + 1].rect.height
        except Exception:
            continue
        if ends < 0.8 or starts > 0.2:
            continue
        second = list(tb.rows)
        if [normalize(c) for c in tb.header] != [normalize(c) for c in ta.header]:
            second = [list(tb.header)] + second          # its "header" was a data row
        rows = [row for row in list(ta.rows) + second if any(str(c).strip() for c in row)]   # no blank rows
        merged = TableBlock(kind="table", has_header=ta.has_header, n_data_rows=len(rows), n_cols=ta.n_cols,
                            header=list(ta.header), rows=rows)
        pages[i] = Page(analysis=pages[i].analysis, blocks=list(a[:-1]) + [merged])
        pages[i + 1] = Page(analysis=pages[i + 1].analysis, blocks=list(b[1:]))
        notes.append(f"the table at the end of page {i + 1} continues on page {i + 2}: joined into one ({len(rows)} rows)")
    return pages, notes


def _cells_overlap(model_table, grid) -> bool:
    """True when the model's table block shares at least 30% of its cells with the detected grid."""
    cells = {normalize(c) for r in grid for c in r if c}
    mine = [normalize(c) for c in list(model_table.header) + [c for r in model_table.rows for c in r] if c]
    return bool(mine) and sum(1 for c in mine if c in cells) / len(mine) >= 0.3


# ---------------------------------------------------------------------------------------------------------------
# Removing text the model wrote a second time
# ---------------------------------------------------------------------------------------------------------------
SPURIOUS_UNMATCHED = 0.8        # a block is a candidate when at least this share of its words lines up with nothing
SPURIOUS_SHINGLE = 4            # ... and it is a copy of real PDF text when this share of its 4-word runs occur in the PDF
SPURIOUS_REAL = 0.7
SPURIOUS_MIN_WORDS = 12         # shorter blocks are left alone: too little text to be sure


def drop_spurious_blocks(page, pdf_page):
    """Return (new_page, dropped). Remove blocks that repeat PDF text the model had already written elsewhere.

    On dense pages the model sometimes writes a paragraph a second time, or at the wrong place. The word alignment
    keeps the best-fitting copy, so the extra one lines up with nothing; yet its words are real PDF words, which tells
    it apart from text the model read out of a picture (that is not in the PDF at all and is kept). Tables and short
    blocks are never touched. If a dropped block was the only copy of some text, the restore step that follows puts
    the text back in its proper place, so this cannot lose content.
    """
    lines, _ = _pdf_lines(pdf_page)
    if not lines or len(page.blocks) < 2:
        return page, []
    pdf_norm, _lw, pdf_to_out, out_norm, out_pos, _bt = _align(page, lines)
    if len(pdf_norm) < SPURIOUS_SHINGLE:
        return page, []
    matched_out = set(pdf_to_out.values())
    pdf_shingles = {tuple(pdf_norm[i:i + SPURIOUS_SHINGLE]) for i in range(len(pdf_norm) - SPURIOUS_SHINGLE + 1)}

    by_block = {}
    for j, (bi, _ti) in enumerate(out_pos):
        by_block.setdefault(bi, []).append(j)

    drop, notes = set(), []
    for bi, block in enumerate(page.blocks):
        if isinstance(block, TableBlock) or block.kind == "image":
            continue
        js = by_block.get(bi, [])
        if len(js) < SPURIOUS_MIN_WORDS:
            continue
        if sum(1 for j in js if j not in matched_out) / len(js) < SPURIOUS_UNMATCHED:
            continue
        words = [out_norm[j] for j in js]
        runs = [tuple(words[i:i + SPURIOUS_SHINGLE]) for i in range(len(words) - SPURIOUS_SHINGLE + 1)]
        if not runs or sum(1 for r in runs if r in pdf_shingles) / len(runs) < SPURIOUS_REAL:
            continue                                   # not PDF text (e.g. read from a picture): keep
        drop.add(bi)
        notes.append(f"{block.text[:40]!r} ({len(js)} words) repeats PDF text written elsewhere: dropped")
    if not drop:
        return page, []
    return Page(analysis=page.analysis, blocks=[b for i, b in enumerate(page.blocks) if i not in drop]), notes
