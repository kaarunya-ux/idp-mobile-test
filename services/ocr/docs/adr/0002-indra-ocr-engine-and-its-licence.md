# 2. Add Indra OCR as an optional engine, and what its licence means

Status: Proposed · Date: 2026-10-21 · Deciders: Python team lead and legal (to confirm)

## Context

Open decision D-1 (IDP-00 ch. 10) picks OCR engines A, B and C by benchmark, from different model families (IDP-03
ch. 2.8). Indra OCR (`datalab-to/indra-ocr`) is a third family next to PaddleOCR. In our benchmark it had the fewest
wrong values (0 of 82), read Hindi in one pass, and ran in about 0.3 s per card on a CPU.

Two things need a decision beyond "does it work":

1. **Dependency.** `indra-ocr` is an engine library, which §22.3 allows per project by ADR. It also pulls in
   `huggingface_hub`, which is not on the approved list. The adapter never uses it: it loads a local model folder, so
   nothing is downloaded at run time (ML-02), and the folder is registered in `models.yaml` with checksums (ML-01).
2. **Licence.** The package code is Apache-2.0. The model weights are under the *AI Pubs Open RAIL-M License
   (Modified)*, Version 0.1 (the file ships with the weights and is registered next to them). The terms that matter:
   - Attachment A, section 2: no use if the user, their employer or an affiliate had **more than US$20,000,000 in gross
     revenue** in the prior year, or **raised more than US$20,000,000** in equity or debt, or provides any product or
     service that **competes with Datalab's**, except personal or research use. Commercial licences may be bought.
   - Clause 4(a): these restrictions must be written into any agreement governing use or distribution, and
     "distribution" includes hosting the model as a service.
   - Clause 7: attribution, a link and a copy of the licence. Clause 8: the same licence applies to the model, its
     derivatives "and to the Output" and derivatives of the Output. Clause 9: the licensor may restrict use remotely.

## Decision

Add the adapter (`idp_ocr_engines.engines.indra`) behind a separate optional extra, `indra`, so the default worker
image does not contain the package or the weights. Selecting the engine is an explicit setting. **It must not run in
production, or on customer data, until this ADR is Accepted with legal's answers to**: whether any clause of Attachment
A, section 2 applies to the company; whether share-alike on Output is acceptable for extraction results; or whether a
commercial licence is bought. Until then it is for benchmarks and evaluation only.

## Consequences

Better: a strong third candidate for D-1, and Hindi without a second recogniser. Worse: a licence obligation to
manage; the package runs its own ONNX sessions, so our per-run timeout (ML-06) cannot apply and the Celery task time
limit is the only bound; the benchmark in the verdict document used `indra-ocr` 0.3.2 and must be re-run on 1.1.2
(model set `v1.1.0`) before any decision rests on it. The licence compliance gate (Java standard ch. 35.6, "no
GPL/AGPL in a distributed artifact") does not catch OpenRAIL-M, so this ADR is the control.

## Alternatives

Leave Indra out and use PP-OCR for slots A and B (no licence risk, a little less accurate and about four times slower
on our set). Port Indra later, at the benchmark step, once the licence question is answered.
