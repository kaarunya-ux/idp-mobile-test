# OCR card tester (PAN / Aadhaar)

Backend for the `ocr-template-lab` React frontend plus a small built-in test UI. It runs the Qualcomm EasyOCR models
(`qualcomm/EasyOCR`, ONNX float, CPU: CRAFT detector + CRNN recognizer, English alphabet) and maps the recognised text to
PAN / Aadhaar fields with per-card templates. It is **not** the `easyocr` pip package: the same networks are run through
`onnxruntime` with our own pre/post-processing (`ocr_engine.py`). Further engines (PP-OCRv5 / v6 from PaddlePaddle on Hugging Face, optional Indra OCR) are listed under `POST /extract`; the PP-OCR ones run the official ONNX models directly.

## Run

```bash
python -m venv .venv && .venv/bin/pip install -r ocr_test_app/requirements.txt
OCR_MODEL_DIR=/path/to/easyocr-onnx-float .venv/bin/python ocr_test_app/app.py
```

The server prints its LAN address on start, e.g. `http://10.0.3.91:5055/extract`; that is the endpoint to enter in the frontend
(the frontend's own default `127.0.0.1:8000` points at the frontend laptop, not here). `GET /api/config` returns it too and the
built-in UI at `/` shows it with a copy button. Allow the port in the firewall if other devices cannot connect (`sudo ufw allow 5055/tcp`).

| Env var | Default | Meaning |
|---|---|---|
| `HOST` / `PORT` | `0.0.0.0` / `5055` | listen address |
| `PUBLIC_URL` | caller's address | override the URL shown by `/api/config` (proxies) |
| `CORS_ORIGINS` | `*` | `*` or comma list, e.g. `http://10.0.3.40:5173,http://localhost:5173` |
| `MAX_UPLOAD_MB` | `25` | upload size limit |
| `OCR_MAX_PAGES` / `OCR_PDF_DPI` | `4` / `200` | pages read from a PDF / TIFF, PDF render resolution |
| `OCR_TIME_BUDGET_S` | `60` | per page: no further retry passes after this |
| `OCR_MODEL_DIR` | `../../models/qualcomm-EasyOCR/onnx-float/easyocr-onnx-float` | folder with `detector.onnx`, `recognizer.onnx` |
| `PPOCRV6_MODEL_DIR` | `../../models/PaddlePaddle-PP-OCRv6-small` | folder with `det/inference.onnx`, `rec/inference.onnx`, `rec/inference.yml` (from `PaddlePaddle/PP-OCRv6_small_{det,rec}_onnx`) |
| `PPOCRV5_MODEL_DIR` | `../../models/PaddlePaddle-PP-OCRv5-mobile` | same layout, from `PaddlePaddle/PP-OCRv5_mobile_{det,rec}_onnx` |
| `DEFAULT_ENGINE` | `ppocrv5` | engine used when the request has none |
| `OCR_DEBUG_DIR` | unset | if set, every `/extract` saves the page images + response there. **Contains personal data**, dev only |

## `POST /extract`

multipart/form-data: `file` (PNG/JPG/WEBP/TIFF/BMP or PDF; content is sniffed, the extension is ignored), `template_type`
(`pan` | `aadhaar`, required), `engine` (optional, default `ppocrv5`; see below). Unknown engines (`doctr`, ...) return `ENGINE_UNAVAILABLE`.

| `engine` | What it is | Hindi (`value_hi`) |
|---|---|---|
| `ppocrv5` (default) | PP-OCRv5 mobile + Devanagari recogniser, HF ONNX, Apache-2.0; reads English and Hindi | yes |
| `ppocrv6` | PP-OCRv6 small, HF ONNX, Apache-2.0 | no |
| `easyocr` | Qualcomm EasyOCR ONNX (CRAFT + CRNN), English only | no |
| `indra` | `datalab-to/indra-ocr` (optional: `pip install indra-ocr`; weights under a modified OpenRAIL-M licence - check it before production use) | yes |
| `compare` | runs every available engine; the first is returned at the top level, all under `engines` | - |

```json
{"success": true, "template_type": "aadhaar", "engine": "ppocrv5", "languages": ["en", "hi"], "document_side_detected": "both",
 "fields": {"name": {"value_en": "RAHUL KUMAR", "value_hi": "राहुल कुमार", "status": "extracted", "confidence": 0.992, "reason": ""},
            "enrolment_number": {"value_en": "", "value_hi": "", "status": "not_available", "reason": "Not printed on this card"},
            "qr_code": {"value_en": "Detected", "value_hi": "", "status": "extracted", "reason": ""}},
 "processing_time_ms": 1620}
```

- PAN fields: `pan_number name father_name dob photo signature qr_code`. Aadhaar: `name dob_or_yob gender aadhaar_number enrolment_number
  photo qr_code care_of address pincode` (+ `document_side_detected`: `front` | `back` | `both` | `unknown`). Every key is always present.
- All values are strings, never `null`; `confidence` (0-1) is left out when unknown (photo / signature / QR and failures).
- `value_en` is Latin-script text only, `value_hi` Devanagari only, NFC-normalised, labels removed, exactly as read. Visual fields use `"Detected"`.
  `gender` is `Male|Female|Transgender` (+ `पुरुष|महिला|ट्रांसजेंडर`), `aadhaar_number` is `1234 5678 9012` (or `XXXX XXXX 1234`),
  `care_of` keeps its relation (`S/O Name`, Hindi `आत्मज: नाम`), `address` is one string without the care-of lead-in and without the PIN.
- `status`: `extracted` (at least one language read and validated), `failed` (expected but not read; `reason` is user-readable),
  `not_available` (not on this card/side: `Back side not provided`, `Not printed on this card`, `Older PAN without QR`).
- `value_hi` is only filled when the engine can read Devanagari **and** the card prints it (the masked sample prints only the Hindi name).
- JSON is UTF-8 (`application/json; charset=utf-8`, no `\u` escapes). CORS headers are on every response, errors included.
- Errors: `{"success": false, "error": {"code", "message"}, "detail", "message"}` (`detail`/`message` mirror `error.message`). HTTP 400/413/415/422/500.
  Codes: `INVALID_FILE FILE_TOO_LARGE UNSUPPORTED_TEMPLATE WRONG_DOCUMENT_TYPE UNREADABLE_DOCUMENT ENGINE_UNAVAILABLE INTERNAL_ERROR`.
  A template mismatch (e.g. an Aadhaar uploaded as `pan`) is `WRONG_DOCUMENT_TYPE`; almost no readable text is `UNREADABLE_DOCUMENT`.
- Server log: one summary line per request (client IP, page count, template, engine, how many fields, which failed, ms). No filenames or values.

Other endpoints (used by the built-in UI): `GET /api/images`, `/api/engines`, `/api/ocr/<group>/<file>`, `/api/lines/<group>/<file>`,
`/api/benchmark` (all engines x all test images vs `ground_truth.json`), `/api/config`, `/img/<group>/<file>`. The UI's engine dropdown has
"compare all".

## Pipeline

1. `documents.py`: validate size, sniff content, decode (EXIF orientation applied, multi-page TIFF / PDF via `pypdfium2`).
2. `preprocess.py`: find the card (largest card-shaped quad), perspective-warp it, estimate and remove skew. Geometry only - no
   thresholding, denoising or sharpening of the pixels OCR sees.
3. `ocr_engine.py`: zoom + tile the image for the 608x800 detector, CRAFT boxes, split/merge into text lines, CTC-decode each line.
4. `pipeline.py`: if the first attempt is not confident (template found and >= 3 text fields accepted) retry: original image, then 90/270/180
   degree turns; keep the best. Pages are merged field by field.
5. `extractor.py` + `card_templates/*.json`: locate each field from printed labels and position (below/above a label, regex over all
   lines, a block under `Address:`), repair look-alike characters by position (PAN `O`->`0`), then validate (PAN structure, Verhoeff
   checksum / masked `XXXX XXXX 1234` for Aadhaar, real dates, pincode inside the address block, minimum confidence).
   A value that does not validate is reported as `failed` instead of being passed through.
6. `contract.py`: map to the JSON above.

## Tests

```bash
python -m unittest discover -s tests -v                      # all, ~3.5 min (real inference)
OCR_SKIP_INFERENCE=1 python -m unittest discover -s tests    # request/contract tests only, <1 s
```

`tests/test_api.py`: `Unit*` classes use no model; `Inference*` classes run the real models on `pan_test_images/` and
`aadhaar_test_images/` (clear, blurred, dark, rotated, perspective, 90/180/270 turned, EXIF-rotated, card small in a 4000x3000 photo, PDF,
front-only, front+back, non-card noise). `tools/make_synthetic_pans.py` regenerates the fictional English-only PAN specimens.

## Measured limits (be aware before relying on it)

- Last full run on the 17 images with known values (10 real cards from the repo + 7 synthetic): 66 correct, 1 wrong, 5 missing of 72 text fields.
  The wrong one is a misread letter ("Rathod" read as "Ralhod"); the recogniser does not know the surname, so validators cannot catch it.
- **PP-OCRv5 mobile** (same 17 images): 70 correct, 1 wrong, 1 missing, ~0.6 s per card.
- **PP-OCRv6 small** (same 17 images): 71 correct, 1 wrong, 0 missing, ~0.5 s per card vs ~10 s for EasyOCR. Its alphabet covers Chinese, Japanese and Latin scripts - no Devanagari either.
- **English only (EasyOCR).** The Qualcomm export has a 97-class Latin alphabet. Hindi / Marathi lines are detected but decode as noise and are ignored
  (address lines with symbol noise or low confidence are dropped). Reading Devanagari needs another recogniser.
- Small text fails: a 860x312 Aadhaar scan lost name/gender; a 480x300 PAN lost the PAN number. Those fields come back `failed`.
- Photo / signature / QR checks are heuristics (anchor-relative texture check, ink density above the "Signature" label, OpenCV QR decoders
  plus a bi-level-block fallback). The QR check misses codes on blurry or tilted photos and then reports `failed` (Aadhaar) / `not_available` (PAN).
- Speed on CPU: roughly 5-15 s per card, up to ~20 s for tall scans, more when retry passes are needed (worst case bounded by `OCR_TIME_BUDGET_S`).
- PDFs: first `OCR_MAX_PAGES` pages, rendered at `OCR_PDF_DPI`. Password-protected PDFs are rejected.
- CORS defaults to `*` and there is no authentication: for a trusted development network only.
