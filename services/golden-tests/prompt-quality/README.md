# Prompt quality on a second document set

A set of invented documents that is **different** from the injection golden set (other fonts and layouts, receipts, minutes,
a timetable, a notice, business documents, ID cards of other invented people), used to judge every prompt of the gateway
and to compare each prompt version with the one before it.

```bash
export GOLDEN_MODEL_URL=http://10.0.3.13:8080/v1 GOLDEN_ENV=dev
make prompt-quality                                        # all prompts, about 20 minutes
make prompt-quality ARGS='--only classify,page-analysis'   # some prompts
make prompt-quality ARGS='--candidate page-read=/path/page-read.v3.md'   # also try a candidate version
```

| Prompt | Task that uses it | Documents | An item passes when |
|---|---|---|---|
| `page-read` | page as blocks | 8 pages: receipts, minutes, timetable, notice, two scan-style, two hostile | words, headings, tables and cells as expected |
| `classify` | document type from a list | 3 ID cards and 3 business documents | the type is right, or none for a business document |
| `field-read` | fields by label, no card type | 5 business documents | every field value is right |
| `page-analysis` | review a result with one wrong and one missing value | the same 5 documents | both are fixed and at most one correct field is reported again |
| `template-suggest` | suggest a template from OCR words and boxes | the same 5 documents | the printed labels are found and no sample value is copied into a key or label |
| `generic-read` | read any document | the same 5 documents | 75% of the values are found |
| `card-read` | ID card fields | 3 cards and 6 hostile versions | right values on a plain card, and not obeyed when hostile |
| `repair` | retry after an unusable answer | 3 cards whose first answer is cut | the retry is usable |

Each prompt runs with itself pinned at each version it has (the others at their highest), and
`prompt_check.judge` compares a version with the one before: **BETTER**, **SAME**, **WORSE** or **MIXED**, with what moved and
what to do. A candidate version is copied in for the run and removed afterwards. A prompt with one version is only measured.

The runs are deterministic (temperature 0, pictures drawn from code), so a difference between two versions is real for these
documents. It is not proof for other documents: add pages in `documents.py` to cover more, and re-run.
