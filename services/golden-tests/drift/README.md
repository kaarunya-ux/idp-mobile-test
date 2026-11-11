# Prompt drift

Quality should be the same in every environment; speed differs by hardware, so each environment is compared only with
its own baseline. Environment names: `dev`, `preprod`, `prod` (`GOLDEN_ENV`); a service started with
`ENVIRONMENT=staging` is pre-prod.

```bash
make injection pii ENV=preprod      # run the suites, tagged with the environment
make drift-baseline ENV=preprod     # record this environment's baseline (once, from a good run)
make drift ENV=preprod              # compare the latest run with it; exit 1 past the limits in thresholds.json
make full-check ENV=preprod         # the suites and the comparison in one go
```

| Piece | Where |
|---|---|
| Build stamp on every log line: `service`, `env`, `service_version`, `commit` (`BUILD__COMMIT`), `config_hash` | the three services' `core/build_info.py` |
| What made each AI answer: model, `prompt_version`, `prompt_hash`, temperature, max tokens, thinking, the server's `system_fingerprint`, `repaired` | gateway, `ai_call_finished` |
| Which prompts are running, and whether a pin chose them | gateway, `prompt_loaded` and `prompts_loaded` at start-up |
| Rollback of a worse prompt: `PROMPTS__PINS='{"page-read": 2}'` and a restart | gateway ADR 0008 |
| The golden suites and their stamp (`stamp.json`: commit, prompt hash, config hash, server build) | `prompt-injection/`, `pii-compliance/`, `stamp.py` |
| Numbers per class (digital, scan, hostile pages; plain and hostile cards), normalised per page or card | `summary.py` |
| Baselines per environment, and the comparison as ratios to them | `baseline.py`, `compare.py`, `thresholds.json` |
| Why each prompt, model or threshold changed, with its evaluation | `CHANGES.md` |

Not here: alerting from production logs and a promotion gate. Those wait for the platform's monitoring decision.
