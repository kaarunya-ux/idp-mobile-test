# Visiting Card Extraction API

Send a photo of a visiting card, get back the person's name, email, phone and address as JSON.
Runs on the office Mac mini; reachable inside the office network only.

- **Base URL:** `http://10.0.3.99:8000`
- **Test page:** `http://10.0.3.99:8000/` (upload a card, see the result)
- **Interactive docs:** `http://10.0.3.99:8000/docs`

## Extract a card

`POST /api/v1/visiting-card/extract`

Body: `multipart/form-data` with the image in a field named **`file`** (JPEG, PNG or WebP, up to 15 MB).
The card can be photographed on a desk; no cropping needed — the API finds the card and crops to it
before reading (`meta.image.cropped_to_card`). One card per image.

```bash
curl -F "file=@card.jpg" http://10.0.3.99:8000/api/v1/visiting-card/extract
```

```js
const form = new FormData();
form.append("file", imageFile);            // File/Blob from an <input> or camera
const res = await fetch("http://10.0.3.99:8000/api/v1/visiting-card/extract", { method: "POST", body: form });
const body = await res.json();
```

### Success — HTTP 200

```json
{
  "name": "Jane Doe",
  "email": "jane.doe@example.com",
  "country_code": "+44",
  "phone": "20 7946 0958",
  "address": null
}
```

| Field | Type | Notes |
|---|---|---|
| `name` | string \| null | Person's full name as printed (not company or title). |
| `email` | string \| null | Person's email; the personal one if several are printed. |
| `country_code` | string \| null | Calling code of `phone`, always `+` and digits (`"+244"`, `"+91"`). `null` when the card prints no code. |
| `phone` | string \| null | One number as printed, without its country code. Mobile preferred, never fax. |
| `address` | string \| null | Full postal address on one line, parts separated by `", "`. |

These five keys are always present and nothing else is sent. `country_code` is only filled when the
code is printed on the card (`+244 …`, `0044 …`, `971 (50) …`); it is never guessed from the address. A field that is not on the card is
`null` — for example the back of a card with only a logo returns all four as `null`. Values are
copied as printed, not reformatted.

### Errors — any non-200 status

```json
{ "error": { "code": "INVALID_IMAGE", "message": "Could not read the image. Send a JPEG, PNG or WebP file." } }
```

| HTTP | `error.code` | Meaning |
|---|---|---|
| 400 | `INVALID_IMAGE` | File is empty or not a readable image (e.g. iPhone HEIC). |
| 413 | `IMAGE_TOO_LARGE` | Image over 15 MB. |
| 422 | `INVALID_REQUEST` | No `file` field in the multipart body. |
| 502 | `MODEL_ERROR` / `MODEL_OUTPUT_INVALID` | Model failed on this image; retry once. |
| 503 | `MODEL_UNAVAILABLE` | Model server is down. |
| 504 | `MODEL_TIMEOUT` | Model took over 2 minutes. |

## Notes for the mobile app

- **Format:** send JPEG, PNG or WebP. **HEIC (the iPhone camera default) is rejected** with
  `INVALID_IMAGE` — convert to JPEG in the app before uploading. Full camera resolution is fine;
  the API resizes.
- **Timing:** about 4-5 s per card. Requests are handled **one at a time**: if three people tap
  Extract together, the third waits ~12-15 s. Set the client timeout to **30 s** and show a spinner.
- **Photo:** the whole card, flat on a desk, reasonably sharp. The API finds the card by its contrast
  with a wooden desk and reads only the card. On other backgrounds (white table, card held in a hand)
  it reads the whole photo instead: same fields, but ~8 s.
- **Phone:** one number is returned, the mobile one when the card prints several (never fax).
- **Language:** English cards. Cards printed only in another script (e.g. Arabic) are not supported yet.
- **Retries:** a 502/503/504 can be retried once; 400/413/422 will fail again with the same file.

## Health

`GET /health` → `{"status": "ok", "model": "Qwen3VL-4B-Instruct-Q4_K_M"}` (`status` is `model_unavailable` if the model is down).

---

## Running it (Mac mini)

Start/stop commands, results, switching between the 4B and 8B models and testing are in
[HANDOVER.md](HANDOVER.md).
