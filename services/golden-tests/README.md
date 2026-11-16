# Golden tests

Regression tests that run the services' real code against a **live model server**, on invented documents with known
expected results (Python standard ML-12). They are not unit tests: they need a model with vision, take minutes, and
are run by hand, not on every commit.

| Folder | What it checks |
|---|---|
| [`prompt-injection/`](prompt-injection/README.md) | Printed instructions on a page or card do not change what the model reads (conversion pages, extraction cards) |
| [`pii-compliance/`](pii-compliance/README.md) | No personal data reaches logs, error text or metrics; extraction masks an Aadhaar number everywhere |

| [`drift/`](drift/README.md) | Baselines per environment and the comparison with them: prompt drift across dev, pre-prod and prod |

Shared by the suites: `drawing.py` (draws the invented pages and cards), `model.py` (the model server), `textmatch.py`.

## Run

```bash
cd services/golden-tests
make help
make injection-conversion       # or injection-extraction, pii, all
GOLDEN_MODEL_URL=http://host:8080/v1 make all
```

Each suite needs the environment of the service it exercises (the Makefile uses `uv run --project`), so
`uv sync --all-extras` in `ai-gateway`, `extraction-service` and `conversion-service` first. The model server must have
vision on. Results go to ignored `results/` folders; the numbers measured when these suites were added are in
[`BASELINE.md`](BASELINE.md).

## Rules for these tests

- **Invented data only.** Every name, number and address is made up (CLAUDE.md: no personal data in git). The Aadhaar
  number `2345 6789 1008` passes the check digit by construction and belongs to no one.
- **No pictures in git.** The drawing code and the expected values are committed; the pictures are drawn on each run
  into ignored `pages/` and `cards/` folders.
- **A pass mark changes only with an evaluation report** (ML-07, ML-12). The limits live in `tolerances.json` files.

## Running them on a server

They are meant to run where the services run, against that environment's model server, not only on a laptop.

- **Set `GOLDEN_MODEL_URL`** (the model server's base URL, for example `http://model-host:8080/v1`) and `GOLDEN_ENV`
  (`dev`, `preprod` or `prod`: the name baselines are kept under; a service started with `ENVIRONMENT=staging` is
  pre-prod, so run the suites with `GOLDEN_ENV=preprod`). There is no default address: a model host's address changes.
- **Python 3.12 and the three services' environments.** The Makefile uses `uv`; on a machine without it, point the
  commands at the services' virtual environments: `make pii GATEWAY=/opt/idp/ai-gateway/.venv/bin/python
  EXTRACTION=/opt/idp/extraction-service/.venv/bin/python CONVERSION=/opt/idp/conversion-service/.venv/bin/python`.
- **Nothing else is needed from the host**: the fonts ship with the tests (`fonts/`), the pictures are drawn on each run,
  git is only used to stamp the commit (the stamp says `unknown` without it), and the suites write only under their own
  `results/` folders.
- **The model server needs vision on** (`--mmproj`). A text-only server answers every picture with HTTP 500.
