# idp-android: how the code is organized

For anyone reading or building on this code. It describes the app as it is after the standardization work, including what is still in `MainActivity.kt` and why. The target structure is IDP-04 Ch. 3.2; the gaps are in `STANDARDS_COMPLIANCE.md` and ADR 0002.

## What the app does

A single-screen Android app that reads Aadhaar, PAN and a few other ID documents on the phone. The user picks an image (or PDF), a document type and a model; the app runs the model with llama.cpp, checks the answer with deterministic rules, shows the result and appends it to a run log. It calls no server.

## The code, in two groups

**Pure code** (plain Kotlin, no Android classes except the platform `org.json`; every file here has unit tests under `app/src/test`). This is the part intended to become the `:engine:*` modules.

| File | What it owns |
|---|---|
| `ExtractionRules` | Shared building blocks: date normalising, the Aadhaar (Verhoeff) checksum, the coverage grade, `withDocumentType` |
| `PanRules`, `AadhaarRules`, `DlRules`, `PassportRules`, `Stage1Rules` | The checks on a PAN, an Aadhaar, an Indian driving licence, an Indian passport, and the stage1 fine-tune evaluation. Each returns a text report graded HIGH, MEDIUM or LOW. `PanRules` also holds the single-field crop check |
| `OutputParser` | Reads the model's raw text into JSON, rejects output copied from our own prompts, strips llama.cpp's startup log, summarises its timing lines |
| `ExtractionPlan` | For a single-image scan, everything decided from the document type and the model: image preparation, prompt, grammar and schema, token cap and output budget, and how the answer is checked. Two-sided documents (the Trinidad & Tobago ID) do not use it |
| `PromptFile`, `ModelRegistry` | `PromptFile` reads the versioned prompt files (`assets/prompts/<name>.v1.md`, front matter removed) and writes the prompt-version fields of the run log. `ModelRegistry` reads `assets/models.yaml`, the list of models with name, version, source, checksum, runtime and device |
| `ModelProfiles` | Which model is loaded (Qwen, LFM2, MiniCPM, recognised by file name), which per-document profile applies, the measured constants and the image-token cap rule. The measurement notes live in its comments |
| `ImageSizing` | The arithmetic for when and how far to shrink an image |
| `LlamaLaunch` | The `llama-mtmd-cli` and `llama-server` command lines and the process environment |
| `LlamaRequest` | The JSON sent to the server, the warm-up request, and the reading of the reply |
| `ModelFiles` | Which pushed `.gguf` files are models and which are `mmproj-` projectors, and whether a projector belongs to a model |
| `RunLog`, `ByteFormat`, `ProcessStats` | The run-log writer, size formatting, memory and CPU figures from `/proc` |

**Android code** (`MainActivity.kt`, about 1,800 lines). It still holds what needs Android or a phone to prove:
- the screen and its event handling (view binding, spinners, switches, the Stop button);
- picking and converting files (an image or the first page of a PDF becomes a JPEG);
- the image resizing itself (decoding and saving bitmaps; the sizes come from `ImageSizing`);
- starting and stopping the llama.cpp processes, the HTTP calls to the local server, the server warm-up and the process monitor;
- `runExtraction`, which ties it together (the decisions in it now come from `ExtractionPlan`; what is left is the image work, the model run and the screen updates);
- the validators for the Trinidad & Tobago documents, which are out of scope for now.

## A scan, step by step

1. The user taps Run; `runExtraction` reads the selected document type, model, thread count and switches.
2. `ExtractionPlan` (using `ModelProfiles`) decides the profile (for example Qwen with `pan_full`), the image preparation, the prompt, grammar, token cap and output budget; the image is shrunk to the size `ImageSizing` gives.
3. Either `runOnePass` (one `llama-mtmd-cli` process per scan) or `runOnePassServer` (a persistent `llama-server`, reused over loopback HTTP) runs the model. The arguments, environment and request JSON come from `LlamaLaunch` and `LlamaRequest`.
4. `OutputParser` cleans the output; `OutputParser.parseSide` turns it into JSON and rejects copied prompt text; the matching `*Rules` file grades it.
5. The header of each run records the model file, its registry entry and the prompt version(s) used (`registryModel=…`, `promptVersion=…`).
6. The result, the validation report and a diagnostics line (image size, cold or warm server, token counts, timings) are shown and appended to `run_log.txt` in the app's private storage through `RunLog`.

## Rules for new code

- Put pure logic in its own small file with unit tests; keep files under 400 lines and functions under 50 lines (ADR 0003).
- Nothing in the pure files may read the clock or use randomness; pass the year or date in.
- Do not log document content. The run log is the one exception and stays in private storage (ADR 0002 row 4).
- Run `mobile/idp-android/scripts/check.sh` before calling a change done.

## What is not here on purpose

No account, sign-in, sync or database; no Compose, Hilt or Room (ADR 0002 row 3); no server calls. The server-side re-check and the template bundle described in IDP-03 do not exist in this repo.
