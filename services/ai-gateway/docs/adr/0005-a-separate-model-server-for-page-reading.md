# 5. A separate model server for conversion's page reading

Status: Accepted · Date: 2026-11-05 · Deciders: Python team lead (Prakyath)

## Context

ai-gateway sent every task to one model server. Extraction reads a few card fields: the Qwen3.5-4B on the Mac mini at
.99 does that at 100% on the card tests, faster than the larger models. Conversion's page reading (scanned page to
Word, Excel or CSV) writes long, structured answers (paragraphs, tables, columns), and its prompt was built and tuned
by Sumukh on the Qwen3.6-35B-A3B (llama-server), where the answer's schema is enforced. On the 4B without enforcement
it left out every block's kind.

## Decision

1. **A model role for whole pages, `page`.** page-read uses it; the other tasks keep their roles.
2. **That role can have a server of its own** (`MODEL__PAGE__BASE_URL`, `MODEL__PAGE__MODEL`,
   `MODEL__PAGE__SEND_SCHEMA`, `MODEL__PAGE__API_KEY`). Set, page-read goes there; unset, it goes to the main
   server's vision model, as before. Each server has its own circuit breaker, so one failing doesn't stop the other.
3. The local stack sends pages to the 35B on the second Mac mini (`PAGE_MODEL_URL`), the rest to the 4B on .99.

## Consequences

- Page reading on the 35B: the made-up card, 9 blocks in 27 s (14 s on the 4B). Which reads real pages better is for
  the evaluation set to show, before it is the default anywhere but the local stack.
- The page role shares the semaphore's capacity setting (`MODEL__VL_MAX_CONCURRENT`, 4, which matches llama-server's
  `-np 4`); a per-server capacity is the next step if the two servers differ.
