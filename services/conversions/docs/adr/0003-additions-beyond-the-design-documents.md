# 0003. Additions beyond the design documents

Status: Proposed (needs the owner's sign-off). From now on nothing is added that IDP-03 or the Python Engineering Standard
does not describe; where they are silent the question goes to the owner first.

Steps 1 to 3 contain the following, which neither document describes. Each is here so it can be kept, changed or removed.

| # | Addition | Where | Why it exists |
|---|---|---|---|
| 1 | Service name `idox-conversion-service`, package `idox_conversion` | `pyproject.toml`, `src/` | IDP-03 names the service `conversion-service` (package `conversion`); that name is taken by the existing service |
| 2 | The 27 conversions of the model-first scripts (IDP-03 lists a 30-pair matrix) | `application/plan_route.py` | The scripts' own list; the difference is ADR 0001 |
| 3 | Six stages: prepare, read, correct, write, layout, check | `domain/route.py` | IDP-03 describes routes as chains of converters, not stages |
| 4 | Settings groups `CHECKS__*` and `LAYOUT__*`, and `MODEL__THINKING`, `MODEL__RETRIES`, `MODEL__MAX_*`, `MODEL__NAME`, `MODEL__VERSION`, `MODEL__PROMPTS_DIR` | `core/config.py` | The thresholds the scripts used as literals (ML-07 wants them in settings); IDP-03 names no such settings |
| 5 | `LIMITS__MAX_PAGES` = 200 and `LIMITS__MAX_SHEET_CELLS` = 5,000,000 | `core/config.py` | IDP-03 says "page and sheet-size caps" without numbers |
| 6 | Error codes `CNV-503-MODEL-001`, `CNV-502-MODEL-002`, `CNV-500-INTERNAL-001` | `core/error_codes.py` | IDP-03 ch. 3.8 lists seven codes; none covers the model server failing or an unexpected error (they are marked "new" in the code) |
| 7 | `idox_conversion.conversion` as the feature name | `src/idox_conversion/conversion/` | The standard says feature first; IDP-03 shows `conversion/` inside the package |
| 8 | The model reached over HTTP by a client in this service | `infrastructure/model/` | IDP-03 ch. 3.4 reaches the model only for table repair, through the AI gateway (ch. 4); ADR 0001, "Open" |

Fixed in this step: `PageReader` and its data now live in `domain/ports.py`, where the standard's tree puts ports (`domain/ports.py`);
they had been in `application/`.
