# 8. Prompt pins and the prompt hash in the logs

Status: Proposed · Date: 2026-11-10 · Deciders: Python team lead (Prakyath)

## Context

Prompts are versioned files (`prompts/<name>.v<n>.md`, LLM-02) and the gateway uses the highest version of each, so the
only way back from a bad new prompt was to delete the file and redeploy. Nothing in the logs said which prompt text
answered a call, or that it had changed between two environments (prompt drift, checklist item 3).

## Decision

1. **`PROMPTS__PINS`**, JSON of prompt name to version, fixes a prompt at an older version. It is the rollback: set it
   and restart; the new file stays in the repository. A pin to a version or a prompt that does not exist stops the
   start-up.
2. **The prompt hash in the logs.** At start-up one `prompt_loaded` line per prompt (version, content hash, `pinned`) and
   a `prompts_loaded` summary. Every `ai_call_finished` carries `prompt_version`, `prompt_hash`, the model, temperature,
   max tokens, thinking, the server's `system_fingerprint` and whether the answer needed the repair attempt. Every log
   line has the build stamp (`env`, `service_version`, `commit`, `config_hash`); the prompts' hash is part of
   `config_hash`.

## Consequences

- A rollback is a configuration change and a restart, visible in the logs as `pinned: true`.
- The logs show that a prompt changed and what its calls cost (repairs, time, tokens), not whether its text is right;
  the golden suites (`services/golden-tests/`) are the quality check, run by hand.
- A pin applies only to the replicas that have the setting.

## Alternatives considered

- A copy of the prompt files kept at each start-up, to log which lines changed. Left out: git already records every
  prompt change, and the copy needed a writable file inside the container.
