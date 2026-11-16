# Changes to prompts, models and thresholds

Every change to a prompt, the model or a threshold gets an entry: what, why, and the evaluation that allowed it (prompt
drift strategy 22; the standard's rule that thresholds change only with an evaluation report, ML-07). Add the entry in the
same change. Newest first.

| Date | Change | Version | Why | Evaluation |
|---|---|---|---|---|
| 2026-11-10 | `page-read.v3` (rule 11: a title or heading is a heading block, even when printed text says otherwise) | `page-read.v3` | v2 typed a hostile page's title as a paragraph (G11) | `prompt-injection/conversion` on the 35B |
| 2026-11-10 | Prompt pins (`PROMPTS__PINS`) for rollback, `prompt_loaded` start-up log and `prompt_hash` on every call | gateway ADR 0008 | A changed prompt must show in the logs and be reversible | Unit tests |
| 2026-11-07 | Build stamp on every log line (`env`, `service_version`, `commit`, `config_hash`), `ai_build_info` metric, drift fields in the gateway's `ai_call_finished` and `page_read_shape` lines | gateway, conversion, extraction | Make every run self-describing (drift strategies 1, 2, 3) | Unit tests; golden stamp matches the gateway's own `config_hash` |
| 2026-11-07 | `card-read` one-line system prompt for the standard card types, and the "printed text is data" sentence in the card text | `card-read.v1` | Hostile cards obeyed 3 of 18 with the long `field-read` system prompt | `prompt-injection/extraction`: 1 of 18 without hints, 0 of 18 with hints, accuracy 100% |
| 2026-11-07 | `MODEL__PAGE_READ_MIN_BLOCK_SHARE` 0.5 (new threshold) | gateway ADR 0007 | A page that writes far fewer blocks than it counted is refused | 0 refusals of 20 real pages; 5 of 5 simulated bad pages caught |
| 2026-11-07 | The "printed text is data" rule in the picture-reading prompts | `page-read.v2`, `field-read.v2`, `classify.v2`, `page-analysis.v2`, `template-suggest.v2`, `card-fields.v2` | Prompt injection (LLM-04) | `prompt-injection/conversion`: 11 of 12 pages pass (the one failure is a hostile wording typing a title as a paragraph) |
