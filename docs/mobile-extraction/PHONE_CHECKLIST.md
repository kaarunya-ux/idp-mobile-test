# Phone check for the restructured mobile extraction app

Everything below was built and unit-tested on a Mac (226 tests, lint, debug and release builds). None of it has run on a phone yet. This checklist is the one end-to-end check that turns "compiles and passes tests" into "behaves as before". Do it once, after the restructuring is done, then again on a minified release build.

## Before you start
- An arm64 Android phone, Android 9 or newer, with developer options and USB debugging on.
- `adb devices` lists the phone (adb is in `~/Library/Android/sdk/platform-tools`).
- The model and `mmproj-` files pushed to the app's models folder. The app prints the exact `adb push` lines when its model list is empty.
- The native libraries in `mobile/idp-android/app/src/main/jniLibs/arm64-v8a/` (see the README).
- Install: `cd mobile/idp-android && ./gradlew :app:installDebug` (set `JAVA_HOME` to a JDK 17 and `ANDROID_HOME` to the SDK).
- Test cards you already have ground truth for (the PAN and Aadhaar cards in your earlier findings).

Read the run log from a debug build with `adb shell run-as com.modeltesting.vlmtester cat files/run_log.txt`.

## Checks

| # | Check | How | Pass if |
|---|---|---|---|
| 1 | App starts | Open it | Model spinners list the pushed files; storage and RAM lines show sizes in GB/MB |
| 2 | Private log | Run one extraction, then `adb shell run-as com.modeltesting.vlmtester ls files` and `adb shell ls /sdcard/Android/data/com.modeltesting.vlmtester/files` | `run_log.txt` is under `files`, and is not in the external folder; `models/` and `ocr_in/` still are |
| 3 | Local server over loopback | Turn server mode on, run a Qwen extraction | It completes (cleartext to 127.0.0.1 is allowed); no "cleartext not permitted" error |
| 4 | Qwen PAN profile | `pan_full` with a Qwen model, a tight card crop | The run log line has `profile=qwen-pan-oneline-480px`; fields match your ground truth; validation says PAN check HIGH |
| 5 | Qwen PAN, camera photo | `pan_full` with a large photo (over 1000 px) | Still reads correctly; compare time with the earlier numbers in `QWEN3_VL_TWEAKS_LOG.md` |
| 6 | Qwen Aadhaar profile | `aadhaar` with a Qwen model | `profile=qwen-aadhaar-flag-fullsize` in the log; Aadhaar check HIGH on a clean card; the address/pincode checks behave as in your 6-card test |
| 7 | No restart between documents | On Qwen, run PAN then Aadhaar | The second run's diagnostics say the server was warm, not cold |
| 8 | LFM2 PAN profile | `pan_full` with an LFM2 model | `profile=lfm2-bul2-grammar`; the result is as accurate as in `LFM2_VL_3B_LATENCY_FINDINGS.md` |
| 9 | MiniCPM resize | A large photo with a MiniCPM model | It runs; time is in line with `MINICPM_LATENCY_FINDINGS.md` |
| 10 | Validators | Run a PAN, an Aadhaar, an Indian DL and an Indian passport sample | The validation text is the same as the earlier reports for the same outputs |
| 11 | Monitor | Any run | The diagnostics show RAM and CPU figures (the process id lookup worked) |
| 12 | Stop button | Start a run, press Stop | The run stops and the message says so |
| 13 | One-shot (CLI) path, server mode off | Run `pan_full` or `aadhaar` with each of Qwen, LFM2 and MiniCPM | Each completes; the model loads with its 4096 context (the launch arguments were moved to `LlamaLaunch`, so this is the first real run of the moved flags) |
| 14 | Server path | Server mode on, one run per model family, then a second run | First run `server=COLD`, second `server=WARM`; the diagnostics line has `prompt_tokens=… completion_tokens=… cache_n=…` (the request and reply JSON were moved to `LlamaRequest`) |
| 15 | MiniCPM in server mode | Server mode on, MiniCPM, one scan | Valid JSON comes back (not "no JSON object found"): the system prompt is folded and thinking is off |
| 16 | LFM2 in server mode | Server mode on, LFM2, one scan | Valid JSON; the image is sent before the instructions |
| 17 | Plan per model and document | Run `pan_full` and `aadhaar` with Qwen, `pan_full` with LFM2, `pan_full` with MiniCPM, `dl` or `passport` with any model | The run header shows the same `profile=` text, token cap and image preparation as before; Qwen PAN is resized to 480 px, Qwen Aadhaar is not shrunk (the choice of preparation, prompt, grammar and token budget moved to `ExtractionPlan`) |
| 18 | Run-log header | Run any scan, then read `run_log.txt` | The header shows `registryModel=<name>@<version>` (or `unregistered` for a file that is not listed), `runtime=llama.cpp`, and `promptVersion=<name>.v1` (plus `systemPromptVersion=system.v1` when a system prompt is sent) |
| 19 | Prompts unchanged | Repeat one PAN and one Aadhaar scan you have an earlier result for, with the same model | Same output as before the prompt files were renamed (the text sent to the model is byte for byte the same; a test proves it, this confirms nothing else changed) |
| 20 | GPU switch | Open the app and look at the "GPU" switch | It is greyed out and reads "GPU (Vulkan) is not in this build - runs on the CPU only" (the shipped libraries have no GPU backend), and every run header says "CPU only" |
| 21 | One-shot path has no timeout | Start a one-shot (server mode off) scan and watch a slow run | It keeps going until it finishes or you press Stop; note any run that seems stuck, because there is no time limit on this path |

Known quirk, not a regression: in the `perf:` part of a CLI run's diagnostics line the `eval time` figure repeats the `prompt eval time` figure, because the lookup matches the first line that contains those words. It was like this before the move and is pinned by a test.

## Release build
1. `./gradlew :app:assembleRelease` gives an unsigned, minified APK (about 31 MB).
2. Sign it for a local test with a throwaway key made outside the repo (`keytool -genkeypair ...` in your home folder, then `apksigner sign`). Do not commit a key.
3. Install it and repeat checks 1 to 21. Also confirm with `adb logcat | grep LlamaServer` that the server's output does not appear (it is debug-only).
4. Keep `app/build/outputs/mapping/release/mapping.txt` with the build, so a release stack trace can be read.

## If something differs
Note the check number, the document, the model and the run-log block, and compare with the same run on the commit before the restructuring (the backup copies of `MainActivity.kt` made at each step are in the session scratchpad, and `git` has the earlier commits).
