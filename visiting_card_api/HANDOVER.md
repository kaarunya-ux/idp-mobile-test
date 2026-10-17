# Visiting Card Extraction API — handover

Status as of 2026-10-17, branch `feature/visiting_card_extraction`.

A photo of a visiting card goes in; the person's name, email, phone (country code separate) and
address come back as JSON. It runs on the office Mac mini, for the mobile team's demo.

| | |
|---|---|
| Test page | http://10.0.3.99:8000/ |
| Swagger | http://10.0.3.99:8000/docs |
| API contract for the mobile team | [README.md](README.md) |
| Live model | **Qwen3-VL-4B-Instruct**, Q4_K_M + F16 mmproj |
| Speed | **~4.5 s per card** (3.3–5.3 s); ~8 s when the card can't be cropped |
| Accuracy | **55/56 fields** on the 14 scored English-language test cards |

## What a request does

```
mobile app ──POST image──▶ FastAPI :8000 (app.py)
                             1. fix phone rotation (EXIF)
                             2. crop to the card, no model (card_crop.py, ~25 ms)
                             3. resize the card to 1024 px on the long edge
                             4. ask the model ──▶ llama-server :8081 (localhost only)
                                                  Qwen3-VL + fixed JSON output format
                             5. split country code from phone (code, not model)
           ◀── {"name", "email", "country_code", "phone", "address"}
```

Response on success is exactly five keys, nothing else:

```json
{"name": "Jane Doe", "email": "jane.doe@example.com", "country_code": "+44", "phone": "20 7946 0958", "address": null}
```

Errors are `{"error": {"code": "...", "message": "..."}}` with the HTTP status. Our test page and test
scripts add a hidden `?debug=true` to also get timings; Swagger does not show it.

## What we achieved

Test set: 16 phone photos of real cards on a wooden desk (`images/`, 1600×739). Reference answers in
`ground_truth.json`, read by eye. Both hold real people's contact details, so they stay on the Mac
mini and are not in git. Card 11 is not scored (its address starts with a
street named after a person — genuinely ambiguous). Card 13 is the Arabic-only back of card 14;
multilingual is out of scope for now, so the "English cards" column leaves it out.

| Step | Model | Image sent to the model | Fields, English cards | Fields, all | Avg | Median | Max |
|---|---|---|---|---|---|---|---|
| Starting point | 8B | whole photo, 1600 px | 55/56 | 58/60 | 11.5 s | 12.0 s | 13.6 s |
| Shrink photo | 8B | whole photo, 1280 px | 56/56 | 57/60 | 8.4 s | 8.4 s | 9.9 s |
| Shrink photo | 8B | whole photo, 1024 px | 54/56 | 55/60 | 6.6 s | 6.5 s | 8.4 s |
| **Crop to card** | 8B | card only, natural size | 55/56 | 57/60 | 5.9 s | 5.6 s | 13.4 s |
| Smaller model | 4B | card only, natural size | 53/56 | 54/60 | 3.5 s | 3.2 s | 7.9 s |
| **4B tuned (live)** | **4B** | **card only, enlarged to 1024 px** | **55/56** | 56/60 | **4.5 s** | **4.4 s** | 7.9 s |

What each step taught us:

- **Most of the time was spent reading desk.** The card fills only 20–35 % of these photos. Cropping to
  the card cut the image from ~1,165 to 300–540 tokens and halved the time with no loss of fields.
  Shrinking the whole photo instead was nearly as fast but blurred small print (wrong emails).
- **The crop is plain image processing, not AI** ([card_crop.py](card_crop.py)): wood is saturated
  orange, cards are not; desk glare is pale too, but it runs off the photo edge and the card does not.
  15/16 test photos crop correctly; card 10 (creased) is sent whole, which costs time, never fields.
- **Writing the answer is the floor.** The model writes ~70 tokens per card. The 8B writes ~20 tokens/s
  on this Mac (memory-bandwidth bound), the 4B ~35/s. That, not image reading, is why the 4B is faster.
- **The 4B needs more pixels per letter.** At the natural crop size (~740 px) it misspelled an email;
  enlarged to 1024 px it read it correctly. That costs ~1 s of reading and is what "4B tuned" is.
- **Both models end up at 55/56 on the English cards; the 4B is ~1.4 s faster**, so the 4B is live.
- The fixed JSON output format (llama-server grammar) means the response always parses and always has
  every key; it cannot guarantee the values are right.
- Country code is split in code after the model answers, so it is predictable and never guessed:
  `(+44) 20…`, `+91-98…`, `0044 20…`, `971 (50) 12…`, `+919876543210` all split; a number printed
  without a code returns `country_code: null`.

## Known limitations

- **One phone number.** Mobile is preferred over Telephone/Office, never fax. The 4B tuned run picked
  the mobile on every card; the 8B sometimes returned the office number.
- **Card 02-type slip:** "Island Computing Services Pvt. Ltd." returned as the address (the card has no
  address). Seen once; left as is.
- **Background.** The crop finds the card against a wooden desk. On a white table or a card held in a
  hand it sends the whole photo: same fields, ~8 s (4B) / ~13 s (8B) instead of ~4.5 s.
- **Languages.** English cards only. Arabic-only cards misread names and addresses.
- **HEIC** (iPhone camera default) is rejected with `INVALID_IMAGE`; the app must send JPEG/PNG/WebP.
- **One request at a time.** Concurrent requests queue (~4.5 s each). Clients should allow 30 s.
- **No authentication.** Reachable from the office network only.

## Running it

All commands from the repo root, `/Users/impactoinfra/idp-mobile-test`.

| Task | Command |
|---|---|
| Start (background) | `nohup caffeinate -is ./visiting_card_api/start.sh > visiting_card_api/logs/api.log 2>&1 &` |
| Stop | `pkill -f "uvicorn app:app"` (also stops the model server) |
| Is it up, which model? | `curl http://10.0.3.99:8000/health` |
| Try one card | `curl -F "file=@images/card_08.jpg" http://10.0.3.99:8000/api/v1/visiting-card/extract` |
| API log | `visiting_card_api/logs/api.log` (one line per request with timings) |
| Model server log | `visiting_card_api/logs/llama-server.log` |

Startup loads the model, locks it in RAM (`--load-mode mmap+mlock`, so an idle Mac does not page it
out) and sends one warm-up request; the API accepts requests only after that (under a minute).
`caffeinate -is` keeps the Mac mini from sleeping.

## Switching back to the 8B

The model is chosen in **one line of [model.py](model.py)**:

```python
ACTIVE_MODEL = os.getenv("ACTIVE_MODEL") or "4b"
```

**To make the 8B the model (permanent):**

1. In `visiting_card_api/model.py`, change `"4b"` to `"8b"` on that line. Nothing else changes: the
   `MODELS` table above it holds each model's files and its card size (8B: natural crop size, 4B: 1024 px).
2. Restart the backend:
   ```bash
   cd /Users/impactoinfra/idp-mobile-test
   pkill -f "uvicorn app:app"; sleep 3
   nohup caffeinate -is ./visiting_card_api/start.sh > visiting_card_api/logs/api.log 2>&1 &
   ```
3. Check: `curl http://10.0.3.99:8000/health` should show `"model":"Qwen3VL-8B-Instruct-Q4_K_M"`.

**To switch for a while without editing code** (lasts until the next plain restart, which goes back
to whatever `ACTIVE_MODEL` says in model.py):

```bash
./visiting_card_api/switch_model.sh 8b     # or 4b
```

It stops the API, starts it on that model and waits until `/health` answers. Either way requests fail
for under a minute, so warn whoever is using it. The response format is identical for both models.

## Testing a change

```bash
.venv/bin/python visiting_card_api/test_cards.py images                  # all 16 photos via the live API
mv visiting_card_api/logs/test_raw.json visiting_card_api/logs/test_<name>.json
.venv/bin/python visiting_card_api/score.py <name> 4b_v2                 # compare with the live baseline
```

`score.py` prints fields right, average/median/max time, and every miss. Saved runs are in
`visiting_card_api/logs/` (not in git).

## Files

| File | What it is |
|---|---|
| [app.py](app.py) | FastAPI app: endpoints, response/error format, Swagger text, starts/stops llama-server |
| [model.py](model.py) | Model choice (`ACTIVE_MODEL`), prompt, JSON format, image prep, country-code split, llama-server flags |
| [card_crop.py](card_crop.py) | Finds the card on the desk and crops to it (Pillow only) |
| [static/index.html](static/index.html) | Test page |
| [start.sh](start.sh) / [switch_model.sh](switch_model.sh) | Start the API / restart it on another model |
| [test_cards.py](test_cards.py) / [score.py](score.py) / [ground_truth.json](ground_truth.json) | 16-card test, scoring, reference answers |
| [README.md](README.md) | API contract for the mobile team |
| `models/` (repo root, not in git) | Qwen3-VL 4B and 8B GGUF files from the official `Qwen/Qwen3-VL-*-Instruct-GGUF` repos |
| `requirements.txt`, `.venv/` (repo root) | fastapi, uvicorn[standard], python-multipart, httpx, pillow |
