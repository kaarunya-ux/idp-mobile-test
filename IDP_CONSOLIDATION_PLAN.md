# IDP Code Consolidation Plan

**Status:** Phase 1, Step 3b done · Step 4 next · **Last updated:** 2026-10-19

## Goal

Get every piece of IDP code the team has written (mobile extraction, OCR, conversions) onto **one branch** in `kaarunya-ux/idp-mobile-test`. From that branch we can standardize the code and build the three Python services described in IDP-03.

## Where the code is today

| # | Who | What | Repo | Branch |
|---|---|---|---|---|
| 1 | Karunya | Aadhaar/PAN extraction with an on-device model (Qwen3-VL-4B), plus the Android app | `kaarunya-ux/idp-mobile-test` | `qwen4bmodel` |
| 2 | Sumukh | OCR: 4 engines tested (`ocr_test_app/`) | `kaarunya-ux/idp-mobile-test` | `easyocr` |
| 3 | Sumukh | Conversions: PPT, Excel, CSV | `sumukh-naidu/idox` | `testing` |
| 4 | Karunya | 10 conversions, table and image helpers, fixes, about 95 tests | `sumukh-naidu/idox` | `testing2` |

Commits authored **"Joel"** are Sumukh's (he used Joel's Mac mini at first). That includes `afc1f9b`, the Qwen3-VL-4B Aadhaar/PAN commit at the tip of `qwen4bmodel`.

Not included as separate sources: our own experiment branches (`3B-LFM2`, `feature/visiting_card_extraction`, `minicpm_canvas`, `qwen_implementation`). Some of their commits come along anyway, because `qwen4bmodel` is built on top of them.

## Ground rules

1. **`dev` and `main` are never touched.** All work happens on the new branch.
2. **Import first, rewrite later.** While a source is being imported, its code is only moved into its folder, never edited. That way, if something breaks, we know whether the problem was in the original code or in our changes. The one exception is path lines that have to change so the two idox layouts fit together.
3. **Keep credit.** Branches from this repo come in with their full history. idox comes in as one commit that credits every author and lists the source commit IDs; its full history stays in the idox repo.
4. **One step at a time.** Each step is reviewed and approved before the next one starts.
5. **Nothing is pushed to GitHub without approval.**
6. **No large files in git.** Model weights, sample inputs and outputs, test-run results, virtual environments and build folders stay out.
7. **From Phase 2 onward, code follows the Python Engineering Standard** (`python_docs/`, kept locally and not in git).

## Step 0 findings (2026-10-19)

- `easyocr` is `qwen4bmodel` plus one commit (Sumukh's `ocr_test_app/`, 18 files). Merging it is a fast-forward with no conflicts.
- In idox, both interns reorganized the same scripts on Oct 16, into different folders: Sumukh used `scripts/`, Karunya used `python_scripts/`.
- The real overlap is small. Files both of them changed: `to_pptx.py`, `to_xlsx.py`, `idox_app.py`, `image_to_pdf.py`, `word_to_pdf.py`, `api/idox_api.py`, `tests/_load.py`, `tests/test_no_model.py`, `.gitignore`. Everything else changed on one side only, or was a path-only change.
- New files:
  - Sumukh: `csv_convert.py`, `excel_to_image.py`, `excel_to_office.py`, `ppt_convert.py`, `ppt_to_office.py`.
  - Karunya: 22 scripts, 6 test files, docs.
- idox history is 116.5 MB, of which 109 MB is sample inputs, outputs and results. That's why idox comes in as one commit rather than with its full history.

## Decisions

| Topic | Decision |
|---|---|
| How idox comes in | One credited commit into the folder `idox_conversions/` |
| Combined idox layout | Karunya's `python_scripts/` layout as the base. Sumukh's 5 new conversions added, and the overlapping files merged by hand |
| `idocx_ai/` | Included |
| Sumukh's idox `mobile/` | App code included (`idox-mobile/`, `docs/`, `memtest/`). The 374 files in `mobile/results/` are left out |
| idox sample `input/` and `output/` folders | Left out (still available in the idox repo) |
| Aadhaar/PAN images and results on `qwen4bmodel` | Kept as they are |
| PowerPoint slide titles (both interns fixed this differently) | Karunya's version kept: every title is centred and continuation slides work. Sumukh's `_centre_title` (centre only if centred in the source) is superseded |
| Excel headings | Both kept: Karunya's wrapped prose cells and Sumukh's centred headings |

## Phase 1: Consolidation

| Step | What | How | Done when | Status |
|---|---|---|---|---|
| 0 | Inspect all 4 sources | Read-only fetch and comparison | Findings and decisions above agreed | ☑ |
| 1 | Create the branch `feat/idp-consolidation` with sources 1 and 2 | Branch from `qwen4bmodel`, fast-forward to `easyocr`. Commit this plan and a `.gitignore` covering local junk and idox samples | Branch exists locally with both sources | ☑ |
| 2 | Combine idox sources 3 and 4 | On a temporary local branch: start from `testing2`, merge `testing`, resolve the overlaps, move Sumukh's new files into `python_scripts/`. Run the idox tests | Combined idox passes its tests | ☑ |
| 3 | Import the combined idox | Copy it into `idox_conversions/` as one commit credited to Sumukh, Karunya and Prakyath, leaving out the excluded folders | Folder in place, credits in the commit (`6ae45f5`) | ☑ |
| 3b | Sync idox updates pushed on 2026-10-19 | Merge the new `testing` (`6ecf83f`) and `testing2` (`7674987`) commits into the combined idox, re-test, then update `idox_conversions/` | 12 files updated in one credited commit (`e02a4d3`); 155 tests OK in a fresh checkout | ☑ |
| 4 | Verify | Run the idox tests from the new location, `ocr_test_app` tests, and the Android build if possible. Delete the temporary branch | Everything that worked before still works | ☐ |

## Phase 2: Standardization

Restructure the whole branch to the Python Engineering Standard: feature-first layout, `uv`, `ruff`, `mypy --strict`, tests. The detailed plan is written after Phase 1.

## Phase 3: First Python service

Build one of the three services from IDP-03 using the consolidated code. Which one is decided after Phase 2. conversion-service is the likely first choice, because conversion is Phase 1 of the IDP roadmap.

## Known issues to carry forward

- **Mobile extraction (source 1):** the "dot product" part isn't finished for testing on a phone.
- **XML conversions:** Image, PDF and Word → XML are included, but they aren't in the IDP conversion matrix (IDP-00 ch. 5).
- **Model calls in conversions:** the idox conversions call the 4B model directly, through `ollama` and `requests`. In IDP, every model call has to go through ai-gateway (standard rule LLM-01), and `requests` is a forbidden library. Both change in Phase 2.
- **Android app location:** the app lives under `home/aiteam/model_testing/`, and some `.gitignore` files ignore `home/`. Phase 2 moves it to a proper top-level folder.
- **Real ID data:** images and results named after people are kept for now. Revisit before any production use (DPDP Act).
- **idox tests depend on local sample folders.** In a fresh checkout, 155 tests run and pass (4 skipped). If someone has *some* sample folders in `input/` and `output/` but not all of them, `test_every_new_script_has_its_folders` fails. In Phase 2, tests should create temporary folders and carry small committed sample files.
- **`idox_app.py --host` has no login.** Sumukh's option lets colleagues on the VPN open the local idox app, and anyone who can reach that address can use it. Fine for a local tool; the IDP services need real authentication (Python standard SEC-01/02).
- **Freeze the idox branches.** The interns pushed new idox work after the first import, which Step 3b had to catch up. From now on, new IDP work should go on `feat/idp-consolidation` (or branches off it), not on idox `testing`/`testing2`.
- **Tools:** `uv` isn't installed on this laptop yet. It's needed for Phase 2.
- **VPN:** GitHub can't be reached while the company VPN is on. Turn it off before any fetch or push.
