# Model Handover — VisionPsy-Nano Stage 1 (PAN & Aadhaar)

For the mobile team. Drop-in replacement for the stock
`qvac/VisionPsy-Nano-460M` GGUF currently on the device.

---

## 1. What this is

The same model, fine-tuned to fix its **output format**. It is not better at
reading documents in general — it is much better at returning usable JSON, and
measurably more accurate on PAN and Aadhaar specifically.

**Scope: PAN and Aadhaar only.** See section 7 before using it on anything else.

---

## 2. Files to swap

Two files, replacing the two you already have:

```
stage1-q5_k_m.gguf                      308 MB    language model
mmproj-visionpsy-nano-460m-q8_0.gguf    104 MB    vision encoder + projector
```

Same GGUF format, same `Q5_K_M` quantization, same custom projector. **No
runtime changes** — it loads with the same patched llama.cpp build you are
already using. Stock llama.cpp still will not work (the projector is custom).

Point your existing `-m` and `--mmproj` flags at these two files. Nothing else
in your integration changes.

---

## 3. You do NOT need any adapter files

If you have seen `adapter_model.safetensors` or `adapter_config.json` mentioned,
ignore them. Those are LoRA training artifacts.

The training produced a small set of weight *deltas* (a LoRA adapter). Those
deltas have already been **merged permanently into the base weights** before
conversion. What you are getting is one ordinary model file with the training
baked in. There is nothing to load alongside it, no `--lora` flag, no extra
configuration.

---

## 4. Use this exact prompt

The model was fine-tuned against this specific prompt. Using a different one
will degrade results — the format discipline it learned is tied to seeing this
instruction block.

```
You are a strict document information extraction system.

Read the provided document image and extract information only from that image.

OUTPUT RULES:

1. Return exactly ONE JSON object.
2. Do not return an array.
3. Do not return multiple JSON objects.
4. Do not use markdown or code fences.
5. Use only the keys defined in the schema.
6. Do not add, remove, rename, or invent keys.
7. Include every schema key exactly once.
8. If a field is not present or cannot be reliably read, use null.
9. Do not guess, infer, calculate, or hallucinate.
10. For list fields, return an array. If none are present, return [].
11. Return the value exactly as it appears in the document, except for valid JSON escaping.
12. Do not include any explanation or text outside the JSON object.

The schema below is the complete and exclusive output structure.

OUTPUT SCHEMA:
<schema json here>

Return exactly one JSON object matching this schema.
```

The schema is appended as pretty-printed JSON with 2-space indentation.

### Aadhaar schema

```json
{
  "aadhaar_number": null,
  "vid": null,
  "name": null,
  "date_of_birth": null,
  "gender": null,
  "address": null
}
```

### PAN schema

```json
{
  "pan_number": null,
  "name": null,
  "father_name": null,
  "date_of_birth": null,
  "signature_present": null
}
```

### Generation settings

```
temperature  0        (greedy — required for reproducible output)
max tokens   1024
```

Temperature 0 matters. Anything higher reintroduces format variability that the
fine-tune exists to remove.

---

## 5. What changes in practice

Measured on a frozen 38-document benchmark, PAN and Aadhaar subsets:

| | Stock model | This model |
|---|---:|---:|
| **Aadhaar** | | |
| Field accuracy | 56.67% | **66.67%** |
| Fields present in response | 83.33% | **95.83%** |
| Parses as bare JSON | 33.33% | **83.33%** |
| **PAN** | | |
| Field accuracy | 42.86% | **54.17%** |
| Fields present in response | 92.86% | **100.00%** |
| Parses as bare JSON | 0.00% | **100.00%** |

Latency is unchanged (~5.4 s/document on M4 CPU).

**The biggest practical difference:** the stock model wraps its JSON in markdown
fences almost every time. On PAN it did so on **every single document** — zero
responses parsed without repair. This model returns bare JSON that starts with
`{`.

It also stops dropping keys. The stock model would silently omit fields it
couldn't read; this one includes every key in the schema and uses `null`.

---

## 6. One thing to keep in your parser

Aadhaar still comes back fenced roughly **1 time in 6**. Keep a defensive strip:

```python
text = response.strip()
if text.startswith("```"):
    text = re.sub(r"^```(?:json)?\s*|\s*```$", "", text, flags=re.S).strip()
data = json.loads(text)
```

PAN is clean 100% of the time in our testing, but keep the same guard for
safety.

---

## 7. Scope limit — read this

**Use this model for PAN and Aadhaar only.**

It was selected for those two document types specifically. On other documents it
has two known problems that do not affect PAN or Aadhaar:

- **Invoice line items are broken.** `line_items` comes back as a list of plain
  strings instead of objects. Anything invoice-related will appear to lose its
  itemisation.
- **It hallucinates on bills and driving licences.** On documents with fields
  that are genuinely absent, it invents values roughly 28% of the time. PAN and
  Aadhaar have no such fields — every field on those cards is present and
  readable — which is why the problem does not arise there.

If you need bills, invoices or driving licences, keep the stock model for those
and ask for a different build.

---

## 8. What is still not fixed

This model fixes **format**, not **reading**. Field accuracy is 54–67%, so
roughly a third of extracted values are still wrong or missed. Improving that
needs training on real Indian documents, which is a separate effort already
planned.

Do not treat it as production-accurate. It is good enough to build and test
against, not to auto-fill a customer record without review.

---

## 9. What we need back from you

This is the most valuable part of the exchange. Our benchmark is 38 scanned and
downloaded documents. You have **real phone-camera photos** — glare, skew,
shadows, thumbs in frame. That distribution is what actually matters and we have
never measured it.

Please log, with the image where possible:

1. **Responses that fail to parse** — the raw text, not just "it failed"
2. **Fields returned as `null` that are clearly visible** on the document
3. **Fields returned with the wrong value** — distinguish these from #2; wrong
   and missing are different failure modes and need different fixes
4. **Anything slower than ~8 seconds**, with the image dimensions

A few dozen examples of #1–#3 would directly shape the next training round.

---

## 10. Version

```
build:        stage1-contract
base:         qvac/VisionPsy-Nano-460M
training:     LoRA r=32 on the language model, merged
benchmark:    38 documents, evaluator v4
status:       evaluation build — will be replaced
```

Do not build anything that depends on this exact behaviour. A successor is in
progress.
