# 8. Prompt pins and a prompt change log

Status: Proposed · Date: 2026-11-10 · Deciders: Python team lead (Prakyath)

## Context

Prompts are versioned files (`prompts/<name>.v<n>.md`, LLM-02) and the gateway used the highest version of each, so the
only way back from a bad new prompt was to delete or edit files and redeploy. Nothing in the logs said that a prompt had
changed, when, or in which environment (only a changed `config_hash`), and nothing said whether the new prompt was worse.

## Decision

1. **`PROMPTS__PINS`**, JSON of prompt name to version, fixes a prompt at an older version. It is the rollback: set it
   and restart; the new file stays in the repository. A pin to a version or a prompt that does not exist stops the start-up.
2. **A change log in the logs.** At start-up one `prompt_loaded` line per prompt (version, content hash, `pinned`) and a
   `prompts_loaded` summary; every `ai_call_finished` carries `prompt_version` and `prompt_hash`. Every line has the build
   stamp, so a change is "this prompt, from this version to that, at this time, in this environment, at this commit".
3. **Judging a change.** `golden-tests/drift/prompt_check.py` runs the golden suite that exercises the prompt with the old
   version and with the new one, compares quality per class, the hostile cards obeyed and new failures, and says WORSE,
   BETTER or SAME; on WORSE it prints the pin that rolls back. From the logs alone, `logwatch.py` compares the calls of
   two consecutive versions (repairs, refusals, time, tokens) and reports `PROMPT_WORSE`.

## Consequences

- A rollback is a configuration change and a restart, and it is visible in the logs as `PROMPT_ROLLED_BACK`.
- The log comparison sees what a bad prompt costs in repairs, refusals, time and tokens, not whether the text is right;
  the golden A/B is the quality check, and is run by hand.
- Only prompts a golden suite exercises can be A/B tested: `page-read` (conversion pages), `card-read` and `field-read`
  (extraction cards). `classify`, `page-analysis` and `template-suggest` have no golden suite yet.
- A pin applies to every replica that has the setting; replicas without it keep running the highest version, which the
  logs show as `MIXED_CONFIG`.

## Addendum: which file and which lines changed

Each `prompt_loaded` line carries the file name, full path and folder. At start-up the gateway also compares the prompt
files with the copy it kept at the previous start-up (`PROMPTS__HISTORY_FILE`, default `prompt_history.json`) and logs
`prompt_file_edited`, `prompt_file_added` or `prompt_file_removed` with the part that changed (front matter, text, both,
or the whole prompt) and up to 20 changed lines with line numbers and rule numbers. Prompts are team-written
instructions, not document content, so logging their lines is allowed (no personal data). If the copy cannot be written
the gateway logs `prompt_history_unavailable` and starts. Limits: only changes between two start-ups are seen, and
the copy is per host, so replicas on different hosts each log their own first start-up.
