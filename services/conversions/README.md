# idox-conversion-service

Model-first document conversions (IDP-03 ch. 3). A vision-language model reads each page, libraries check and write the file.
Work arrives as `ConversionRequested` messages; this process only answers the platform's health probes.

**Status:** step 1 of [docs/build-log.md](docs/build-log.md). The conversions themselves still run from the prototype
scripts in `python_scripts/`, the test page in `web/` and the API in `api/`; they move into `src/idox_conversion/` step by step.

## Run

```
make sync        # uv sync --all-extras
make check       # ruff, mypy --strict, import-linter, pytest with coverage (what CI runs)
make run         # health API on 127.0.0.1:8001
```

## Environment variables (CFG-05)

See `.env.example` for the whole list. Nested groups use a double underscore, for example `MODEL__BASE_URL`.

| Variable | Default | Meaning |
|---|---|---|
| `MODEL__BASE_URL` | `http://127.0.0.1:8080` | The model server that reads pages |
| `MODEL__THINKING` | `false` | Sent on every request; a server's own default must never decide this |
| `MODEL__REQUEST_TIMEOUT` | `600` s | Time limit of one model request (ML-06) |
| `MODEL__MAX_OUTPUT_TOKENS` | `4000` | Output budget of one page |
| `MODEL__MAX_IMAGE_SIDE_PX` | `0` | Longest side of the page picture sent; `0` sends it as drawn |
| `LIMITS__DOCUMENT_TIMEOUT` / `JOB_ITEM_TIMEOUT` | `120` / `600` s | IDP-03 ch. 3.7 |
| `LIMITS__MAX_IMAGE_PIXELS` | `180000000` | Decompression-bomb cap (IDP-03 ch. 3.7) |
| `LIMITS__MAX_PAGES`, `LIMITS__MAX_SHEET_CELLS` | `200`, `5000000` | This service's own caps |
| `CHECKS__MIN_WORD_SHARE` / `MIN_WORD_SHARE_OCR` | `0.95` / `0.85` | Share of the source's words that must reach the output |
| `LAYOUT__*` | see `core/config.py` | Numbers the layout steps use |
