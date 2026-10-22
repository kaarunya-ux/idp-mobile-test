# 5. Prompt versioning and the model registry, in the form the Python Standard prescribes

Status: Proposed · Date: 2026-10-22 · Deciders: mobile extraction owner (to approve)

## Context

The Python Engineering Standard asks for two things the app did not have: prompts as versioned files with the version recorded on every output (LLM-02; IDP-03 Ch. 4.4), and a registry of every model with name, version, source, SHA-256, runtime and device, with the model name and version recorded on every output (ML-01, ML-04). The standard writes these rules for the Python services. IDP-04 says nothing about how the Android app should do it, so this ADR records how the same rules were applied here and where the form had to be adapted.

## Decision

**Prompts.** Each prompt asset is renamed to `<name>.v<n>.md` (for example `user_pan_full.v1.md`, `system.v1.md`) and starts with front matter giving `model`, `temperature` and `schema`, as IDP-03 Ch. 4.4 describes. `PromptFile` removes the front matter before the text is used, so the model receives exactly the text that was validated on a phone; a unit test pins the SHA-256 of every body to prove it. A prompt change is a new `v2` file, never an edit of `v1` (LLM-02). The run-log header records `promptVersion=<base name>` (and `systemPromptVersion` when a system prompt is sent), in the form of the standard's `field-read.v2`.

**Registry.** `assets/models.yaml` lists the models with the fields ML-01 names, plus two more, `file` and `mmproj`, because the app finds a model by the file name in its models folder and needs that link to reach the entry. Facts that the project's docs do not state are `null`, never guessed: no checksum is registered yet and the Qwen3-VL-4B has no file name. The run-log header records `registryModel=<name>@<version>` and the runtime (and the start of the checksum once there is one), or `registryModel=unregistered`.

**Reader.** `ModelRegistry` reads only the fixed shape of that file and rejects anything else with the line number. A YAML library would be a new dependency (it needs an ADR of its own and adds size to the APK); the file is small and flat, so a strict reader with tests was chosen.

## Consequences

- Every result can be traced to a prompt version and, where the registry has it, to a registered model.
- Files that other code or notes called `user_*.txt` and `system.txt` no longer exist under those names. The findings docs still mention the old names; they describe history.
- Not done, and still gaps against the standard: checking a model file against its SHA-256 before use (ML-02), choosing the model by registry name instead of by file name (ML-01), and the golden-set evaluation that must accompany a prompt change (ML-12).
- Whoever has the model files can complete the registry with one command per file (`shasum -a 256 <file>`), which turns the `unversioned` and missing-checksum cases into full records.

## Alternatives

- Keep `.txt` prompts and record a hash instead of a version: rejected, the standard names the `<name>.v<n>.md` form.
- Put the registry in JSON: rejected, the standard names `models.yaml`.
- Use a YAML library: deferred, see above.
