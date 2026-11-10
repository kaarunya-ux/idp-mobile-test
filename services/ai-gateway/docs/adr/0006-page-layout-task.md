# 6. page-layout: a page's structure by OCR line numbers

Status: Accepted · Date: 2026-11-05 · Deciders: Python team lead (Prakyath)

## Context

page-read has the page model write every word of a scanned page: about 15 s a page on the Qwen3.6-35B, almost all of
it output. conversion-service can read the words itself with PP-OCR in 1 to 2 s (conversion ADR 0001); what it can't
work out alone is the structure: which lines are a heading, a paragraph, a list, or the cells of a table.

## Decision

1. **A new task, `page-layout`** (`POST /internal/v1/ai/page-layout`, prompt `prompts/page-layout.v1.md`), on the
   `page` model role (ADR 0005). Its request is the caller, the page picture (`bucket`/`key` or `data`, ADR 0004) and
   the OCR lines: number (unique), text, box. Its answer is the blocks in reading order, each naming line numbers only:
   text blocks (`kind`, `lines`, `align`, `size`, `bold`) and tables (`hasHeader`, `rows` of cells of line numbers).
2. **The lines go to the model as data** (`<<<DATA>>>` markers), as every other task's page text does.
3. **A number the caller never sent is refused** and repaired once, like any answer that doesn't fit; a block with no
   `kind` is a table when it has rows, else a paragraph (the 4B leaves kinds out, entry 7).
4. Its own timeout, `MODEL__TIMEOUTS__PAGE_LAYOUT` (120 s). Caps, lanes and breakers as every task.

## Consequences

- An internal API addition only: no existing route, field or error code changes.
- The model writes a few line numbers instead of the page, so a page's answer is short; the measured time is in
  conversion's build log.
- The answer carries no text, so it can't add words to a document; the caller keeps any line it left out.
