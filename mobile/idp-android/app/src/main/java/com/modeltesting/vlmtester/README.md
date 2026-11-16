# com.modeltesting.vlmtester: the code, area by area

One package, so one README. For the whole picture and the flow of a scan see `docs/mobile-extraction/ARCHITECTURE.md`; for rule-by-rule status see `docs/mobile-extraction/STANDARDS_COMPLIANCE.md` (Java Standard Ch. 34.2: use cases, configuration, errors, caches, tests).

Everything here except `MainActivity.kt` is plain Kotlin with unit tests in `app/src/test`. None of it keeps a cache or any state between calls.

| Area | Files | What it does | Reads (configuration) | Fails with | Tests |
|---|---|---|---|---|---|
| Output checks | `ExtractionRules`, `PanRules`, `AadhaarRules`, `DlRules`, `PassportRules`, `Stage1Rules`, `TtRules`, `ValidationReports` | Turn the model's JSON into a graded text report (HIGH, MEDIUM, LOW) | The calendar year, passed in | Nothing: a bad value becomes a line in the report | `ExtractionRulesTest`, `PanRulesTest`, `AadhaarRulesTest`, `DlRulesTest`, `PassportRulesTest`, `Stage1RulesTest`, `TtRulesTest`, `ValidationReportsTest` |
| Model output | `OutputParser` | Reads the model's text as JSON, rejects text copied from our own prompts, strips llama.cpp's start-up log, summarises its timing lines | The lists of known prompt text inside the file | Returns an error report; never throws on bad model output | `OutputParserTest` |
| Model and document settings | `ModelProfiles`, `ExtractionPlan`, `ImageSizing` | Which model family is loaded, what each document type gets (prompt, grammar, token limits, image preparation), and the resize arithmetic | The measured constants in `ModelProfiles` | Nothing | `ModelProfilesTest`, `ExtractionPlanTest`, `ImageSizingTest` |
| Calling the model | `LlamaLaunch`, `LlamaRequest` | The llama.cpp command lines and environment; the request JSON and the reading of the reply | Nothing; everything is passed in | `JSONException` from `LlamaRequest.contentOf` when the reply has no answer | `LlamaLaunchTest`, `LlamaRequestTest` |
| Prompts and models | `PromptFile`, `ModelRegistry` | Versioned prompt files (`assets/prompts/<name>.v1.md`) and the model registry (`assets/models.yaml`) | Those two asset folders | `IllegalArgumentException` naming the missing field or the line, for a malformed prompt or registry file | `PromptFileTest`, `ModelRegistryTest` |
| Files and logs | `RunLog` (and `RunCounter`), `ModelFiles`, `ByteFormat`, `ProcessStats`, `ProcessMonitor` (and `ProcessIds`) | The run log (`files/run_log.txt`, private storage), which pushed `.gguf` files are models, size text, memory and CPU figures from `/proc` | Nothing | The file-writing calls throw `IOException` if the phone's storage fails | `RunLogTest`, `ModelFilesTest`, `ByteFormatTest`, `ProcessStatsTest` |
| GPU switch | `GpuSupport` | Whether the shipped libraries contain a GPU backend | The native library folder's file names | Nothing | `GpuSupportTest` |
| Screen and runtime | `MainActivity.kt` | The screen, picking and converting files, bitmap work, starting the llama.cpp processes, the HTTP calls, the warm-up, `runExtraction`, the Trinidad & Tobago validators | The switches and spinners on the screen | Shows `Error: …` on the screen | No unit tests; needs a phone (`docs/mobile-extraction/PHONE_CHECKLIST.md`) |

Run `mobile/idp-android/scripts/check.sh` before calling a change done.
