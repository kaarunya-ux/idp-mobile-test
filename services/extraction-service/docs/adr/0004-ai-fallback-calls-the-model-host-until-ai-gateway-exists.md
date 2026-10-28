# 4. The AI fallback calls the model host directly until ai-gateway exists

Status: Accepted (temporary) · Date: 2026-10-28 · Deciders: Python team lead

## Context

IDP-03 ch. 2.6 stage 7 sends fields the OCR engines can't settle to AI, and IDP-03 ch. 4 says only ai-gateway may
talk to the model server. ai-gateway isn't built yet. The web extraction app (`idp_web_app`, feature/visiting_card_extraction)
already reads PAN, Aadhaar and driving-licence fields with a vision model on the model host, with schema-constrained
answers, and was benchmarked on real cards.

## Decision

OCR is the first layer and AI the fallback, as IDP-03 says. The pipeline calls AI through a port
(`AiFieldReaderPort`); today its adapter (`infrastructure/ai/vision_reader.py`) is the web app's model client and
prompts, ported to the standard, calling the model host directly. An AI value is accepted only when the field's rule
passes; otherwise the field stays NEEDS_REVIEW. When the model host can't answer, fields stay NEEDS_REVIEW and the job
still succeeds. The fallback is off unless `AI_FALLBACK__URL` is set.

## Consequences

The deviation from IDP-03 ch. 4 (one service talking to the model server directly) lasts until ai-gateway exists; then
a gateway client replaces the adapter and `AI_FALLBACK__URL` points at the gateway. Nothing else changes. Until then
the gateway's fair queuing and caps don't apply: the fallback runs at most once per job item, only for unsettled fields.
