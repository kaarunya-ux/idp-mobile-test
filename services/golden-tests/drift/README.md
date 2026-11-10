# Prompt drift tools

What the prompt-drift strategies ask for (`prompt_drift_across_environments`), as far as it can be built without a
platform decision. Quality should be the same in every environment; performance differs by hardware, so each environment
is compared only with its own baseline.

Environment names: the baselines use `dev`, `preprod` and `prod` (`GOLDEN_ENV`); the services' `ENVIRONMENT` setting has
`local`, `dev`, `test`, `staging` and `prod`, so pre-prod is `ENVIRONMENT=staging` and `GOLDEN_ENV=preprod`.

```bash
make injection-conversion injection-extraction ENV=preprod    # the suites, tagged with the environment
make drift-baseline ENV=preprod                               # record this environment's baseline
make drift ENV=preprod                                        # compare the latest run with it
make gate ENV=preprod FROM=dev                                # promotion gate, in the environment promoted to
make canary ENV=prod                                          # one small page, when you want a quick check
make full-check ENV=preprod                                   # suites + comparison in one go
```

| # | Strategy | Status |
|---|---|---|
| 1 | Stamp every log line with environment and build | **Done** in the three services: `env`, `service_version`, `commit` (`BUILD__COMMIT`), `config_hash` on every line |
| 2 | Stamp the drift inputs | **Done** in the gateway: model, temperature, max tokens, thinking, the server's build id (`system_fingerprint`) and whether the answer was repaired, on `ai_call_finished`. The golden `stamp.json` records the same |
| 3 | Hash the configuration | **Done**: `config_hash` covers the settings that change behaviour plus, in the gateway, the hash of every prompt file; URLs, credentials, concurrency and timeouts are left out so a promoted build keeps its hash |
| 4 | `models.yaml` with checksums | **Done** in the gateway (ADR 0007): pre-prod runs Qwen3.6-35B-A3B UD-IQ4_XS for every role, pinned by its published SHA-256; `MODEL__REGISTRY_CHECK=strict` stops a gateway whose server serves another model; the registry hash is in `config_hash`. Not yet pinned: the mmproj file; and the checksum has not been run on the model host |
| 5 | One promoted artifact | Process, not code. The images are built from one Dockerfile per service; promote the same image |
| 6 | Configuration as code, allowed differences | **Partly**: the allowed differences are the settings *not* listed in each service's `BEHAVIOUR_SETTINGS`; there is no start-up check against expected values |
| 7 | Send explicitly | Already true (thinking, limits and temperature are sent on every call); the server's reported build id is now logged |
| 8 | A golden set that travels | **Done**: `prompt-injection/` and `pii-compliance/`, tagged with `GOLDEN_ENV` |
| 9 | Per-class numbers | **Done**: `summary.py` and `compare.py` report digital, scan and hostile pages and cards separately |
| 10 | Tolerances | `tolerances.json` per suite (the same in every environment); change only with an entry in `CHANGES.md` |
| 11 | Baselines per environment | **Done**: `baseline.py` writes `baselines/<env>.json` |
| 12 | Ratios, not absolutes | **Done**: `compare.py` reports each metric as a ratio to the environment's own baseline |
| 13 | Normalise by work | **Done**: seconds per page, per megapixel, tokens per page or card |
| 14 | Thresholds per environment | **Done**: `thresholds.json` (dev loose, pre-prod and prod strict) |
| 15 | Alert on change | **Done from logs alone**: `logwatch.py check` alerts on a latency or token-count rise against this environment's own normal, on a new or mixed `config_hash`, on a model mismatch; the alerts are JSON lines for the log pipeline (see Monitoring from logs below) |
| 16 | Watch distributions | **Done from logs alone**: `logwatch.py` reports, per service and task, calls, latency median and p95, output tokens, the share of repaired and refused answers, and how many pages wrote under 80% of the blocks they counted; it alerts when one passes its limit in `thresholds.json` |
| 17 | Scheduled runs | **Not used, by decision**: the tests are run by hand. `make full-check` runs the suites and the comparison in one go |
| 18 | A fixed canary | **Done**: `canary.py` runs one small page against its known content; it is run by hand, not on a timer |
| 19 | A promotion gate | **Done**: `gate.py`; who runs it and blocks the deploy is the platform's decision |
| 20 | Shadow or canary release | **Not done**: needs deployment infrastructure |
| 21 | A rollback path | **Done**: `PROMPTS__PINS` rolls a prompt back to an older version with a restart; a failing gate names the environment's last good commit and hashes; `prompt_check.py` prints the pin when a new prompt is worse |
| 22 | Record the change | **Done**: `CHANGES.md` for the reason and the evaluation, and the logs for what ran when: `prompt_loaded` at every start-up, shown by `logwatch.py` as `PROMPT_CHANGED` / `PROMPT_ROLLED_BACK` with time, environment and commit |
| 23 | No personal data in drift logs | All of the above are names, versions, hashes and numbers; the golden documents are invented |
| 24 | Metrics with env and build labels | Not needed with log-only monitoring: every log line carries `service`, `env`, `service_version`, `commit` and `config_hash`. The gateway's `/metrics` endpoint and `ai_build_info` still exist and can be left unused |

`stamp.py` records the run's environment, commit, prompt hash, configuration hash, model and the model server's build id
next to every suite's results. A difference between two runs' stamps explains a difference between their results.

## Monitoring from logs only

No Prometheus, Grafana or alert manager: the services' JSON logs are the monitoring. Every line carries the build stamp
(`service`, `env`, `service_version`, `commit`, `config_hash`), and the events below carry the numbers.

| Event | Service | Fields used |
|---|---|---|
| `ai_call_finished` | gateway | `task`, `outcome`, `latency_ms`, `tokens_in`, `tokens_out`, `repaired`, `model`, `server_fingerprint` |
| `page_read_shape` | gateway | `blocks`, `counted` |
| `page_read` | conversion | `latency_ms`, `tokens_out`, `blocks`, `blocks_counted` |
| `page_reading_rejected` | conversion | (the OCR check refused a page) |
| `model_registry_mismatch` | gateway | (a server served another model than `models.yaml`) |

Everything is run by hand; nothing is scheduled.

```bash
# once per environment, from the logs of a good period
make logs-record ENV=preprod LOGS='/var/log/idp/*.jsonl'
# whenever you want to know (after a deploy, before promoting, when something looks wrong)
make logs-check  ENV=preprod LOGS='/var/log/idp/*.jsonl' SINCE=1h
# the same without make, from a container's output
docker logs --since 1h idp-gateway 2>&1 | python drift/logwatch.py check --env preprod -
# keep the alerts: redirect the output to a file
python drift/logwatch.py check --env preprod --since 1h /var/log/idp/*.jsonl >> drift.jsonl
```

Alerts are JSON lines (`{"event": "drift_alert", "alert": "LATENCY", "severity": "warn", ...}`) followed by a
`drift_check` line, so they can be kept in a file or read by whatever reads your logs. Exit code 1 means an
alert. The alerts: `LATENCY` and `TOKENS` (against this environment's own baseline), `REPAIRED` and `REFUSED` (the share of
answers that needed a repair or were refused as unusable), `SHAPE` (pages that wrote under 80% of the blocks they
counted), `REJECTED` (scanned pages the OCR check refused), `MODEL_MISMATCH` (critical), `MIXED_CONFIG` (replicas run
different configurations) and `CONFIG_CHANGED` (information: a new hash since the baseline, a deploy marker). A window
with fewer than `min_calls` calls is not judged. The limits per environment are the `logs` block of `thresholds.json`.
Lines that are not JSON are ignored, and `--since` counts back from the newest event, so an archived log gives the same
answer as a live one. Logs contain no document text (OBS-04, LLM-06), so the alerts do not either.

## When you change a prompt

1. Add the new version as a new file (`page-read.v3.md`); never edit a version in place (the logs flag that as `PROMPT_EDITED`).
2. **Compare it with the old one** on the golden suite, by hand, against the model server:
   ```bash
   make prompt-ab PROMPT=page-read OLD=2 NEW=3        # about 12 minutes: runs the conversion pages with each version
   ```
   `prompt_check.py` runs the suite that exercises the prompt (`page-read`: conversion pages; `card-read`, `field-read`:
   extraction cards) once pinned to the old version and once to the new, then prints a table and a verdict:
   **WORSE** (quality fell in a class, more hostile cards obeyed, or a new test failure, and nothing improved), **MIXED**
   (something broke and something was fixed: both lists are shown), **BETTER** or **SAME**, plus whether it is slower or
   writes more. On WORSE and MIXED it prints the rollback and exits 1.
3. **Deploy it.** The start-up log now has `prompt_loaded page-read.v3 ...` with the environment, commit and time.
4. **Watch the logs** with `make logs-check`: `PROMPT_CHANGED` marks the change, and `PROMPT_WORSE` says the new version
   needs more repairs, refusals, time or tokens than the one before, with the rollback.
5. **Roll back** if it is worse: set `PROMPTS__PINS='{"page-read": 2}'` in the gateway's environment and restart. The log
   shows `prompt_loaded page-read.v2` with `pinned: true` and `logwatch.py` reports `PROMPT_ROLLED_BACK`.
6. Add the entry to `CHANGES.md` (the verdict output prints a line to complete).
