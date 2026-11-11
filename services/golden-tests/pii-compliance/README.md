# PII compliance golden tests

Five invented documents carrying personal data (a PAN card, an Aadhaar card, an Aadhaar card whose number fails the
check digit, a driving licence, a letter with a phone number, an e-mail address and a PAN) are read by the live model
through the services' real code. The suites then search what the code wrote for each document's personal data.

| Script | Environment | Checks |
|---|---|---|
| `run_gateway.py` | ai-gateway | Logs (DEBUG level), exception text and Prometheus metrics of a normal, a repaired and a failed read hold no identifier, name, date or address (OBS-04, LLM-06) |
| `run_extraction.py` | extraction-service | The model's values go through the real decision code; an Aadhaar number is masked everywhere in the decision, also when the rule rejects it (F-EX-13) |
| `run_conversion.py` | conversion-service | Logs and error text of the scanned-page converter, for a read and for a refusal |

```bash
make pii
```

`documents.py` lists the personal data on each document. `id` values are what a log mask catches; `other` values (names,
dates, addresses) no pattern catches, so finding one in a log means the code logged extracted data. `run_extraction.py`
reads the values `run_gateway.py` saved, so run them in that order (`make pii` does). Each script exits 1 on a leak.

PAN, licence number, names and addresses are shown in clear by design: the templates set no mask for them (IDP-00 strategy 7
is a pending decision), so a clear value there is not a failure.
