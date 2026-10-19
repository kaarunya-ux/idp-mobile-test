# CLAUDE.md: Gemma OCR (on-device OCR + AI field extraction, Android)

> Context file for an AI coding assistant taking over this project. Put it at the repo root as `CLAUDE.md`. It records facts, measured results, constraints and the reasons behind decisions, so you don't repeat experiments that already failed. The full source is at the end; if the repo is missing, recreate it from there exactly.

## Mission

Offline Android app: image → text → labelled fields (`Name`, `Address`, `Phone`, `GSTIN`, `Total amount`, …) so values can be mapped to other data. Target hardware is **low-RAM phones (6 GB)**. Everything runs on-device; no network calls at runtime.

## Hard facts (verified on device; do not re-derive)

- Test device: Samsung Galaxy M35 5G, `SM-M356B`, SoC `s5e8835` (Exynos 1380: 4×A78 + 4×A55), **Mali-G68** GPU, `MemTotal 5.3 GB`, Android 16, OpenCL at `/vendor/lib64/libOpenCL.so → libGLES_mali.so`.
- Runtime: `com.google.ai.edge.litertlm:litertlm-android:0.17.1` (latest on Google Maven as of 2026-09-17). JNI ships `arm64-v8a` and `x86_64`; minSdk 24.
- Models (Hugging Face `litert-community`, Apache 2.0, not gated): `gemma-4-E2B-it.litertlm` 2,588,147,712 bytes; `gemma-4-E4B-it.litertlm` 3,659,530,240 bytes. Pre-quantized mixed 2/4/8-bit. `-web.litertlm` variants are text-only; never use them.
- Model graphs seen in logcat: `decode`, `prefill_1024`, `prefill_128`, `verify`; vision `vision_70`, `vision_140`, `vision_280`; `vision_adapter_*`. **Max 280 image tokens per image.**
- **E4B + GPU backend on Mali/6 GB: OOM-killed by lmkd** (~2 min into load, signal 9, foreground). LiteRT warns `Weights preparation on Gpu is disabled for PowerVR, Broadcom, Mali GPUs`. Left a 2.2 GB `mldrift_weight_cache.bin`.
- **E4B + CPU backend:** load 18.2 s first, 2.1 s cached; ~2.5 GB `MemAvailable` after load; XNNPACK cache ≈ 2.4 GB in `files/litert-cache`.
- **E4B CPU OCR on a clean synthetic invoice (large font):** exact output; image encode + prefill 34.7 s; 3.5 tok/s; total 58 s.
- **E4B on the owner's real photo:** degenerate `[unclear] [unclear]…` loop, with vision on CPU (confirmed by logcat `vision_280` via XNNPACK). Causes: the prompt offered `[unclear]` + greedy decoding, and the 280-token vision limit makes small text illegible.
- Hybrid (LLM on CPU, vision on GPU): produced `[unclear]` loops at the same time; not validated. `hybridVision` defaults to `false`.
- E4B was deleted from the phone at the owner's request (the E4B file is still in the owner's PC Downloads). **E2B is on the phone at `/data/local/tmp/gemma-ocr/`; it has not been tested yet.**
- Untested features: strip splitting, AI field detection, E2B (CPU and GPU).

## Architecture

```
MainActivity (Compose) ──► OcrViewModel (state + orchestration)
                              ├─ FAST: mlKitOcr(bitmap)              [Images.kt]
                              ├─ AI:   strips() → GemmaEngine.extract(jpeg) per strip
                              │        → isLooping guard → mergeStripText()
                              └─ then: GemmaEngine.detectFields(text) → parseFields()
GemmaEngine ── LiteRT-LM Engine (mmap model) ── fresh Conversation per request
```

| File | Owns |
|---|---|
| `app/src/main/java/com/impacto/gemmaocr/GemmaEngine.kt` | `findModels`, `OCR_PROMPT`, `FIELDS_PROMPT`, `parseFields`, `GemmaEngine.load` (backend order, EngineConfig), `extract`, `detectFields`, `run`, `stop`, `close` |
| `.../Images.kt` | `decodeScaled` (ImageDecoder, EXIF), `strips`, `mergeStripText`, `trimRepetition`, `toJpeg`, `mlKitOcr` |
| `.../OcrViewModel.kt` | UI state, backend pref + crash guard, `preload`/`ensureEngine` (Mutex), `extract`, `runGemma`, `runFieldDetection`, `isLooping`, `releaseModel` |
| `.../MainActivity.kt` | Compose UI, camera (TakePicture + FileProvider), photo picker, ACTION_SEND handling, `onTrimMemory` |

## Invariants: do not break these

1. **Never default to GPU on devices with < 7.5 GB RAM.** `defaultBackend()` enforces this. GPU → CPU fallback is allowed; CPU → GPU fallback is not.
2. **Keep the crash guard.** `KEY_LOADING` is written with `commit()` before `engine.initialize()` and removed in `finally`. A leftover marker means an uncatchable OOM kill; on the next start, switch GPU → CPU.
3. **One `Engine` at a time**, created and closed under `engineLock`. Unload before switching model or backend.
4. **A fresh `Conversation` per request**, closed in `onCompletion`. Never reuse conversations across images.
5. **Image content before text** in `Contents.of(...)`.
6. **Greedy sampling** (`topK = 1`) for OCR and fields.
7. **Never reintroduce an `[unclear]`-style placeholder** in prompts. It causes infinite loops under greedy decoding.
8. **Keep `isLooping` checks** on every streamed chunk for both OCR and field detection.
9. `maxNumTokens = 2048`, `maxNumImages = 1`, `audioBackend = null`. Raising the token limit increases KV-cache RAM; `MAX_FIELD_INPUT_CHARS = 3500` must stay consistent with it.
10. Keep both `<uses-native-library>` entries (`libOpenCL.so`, `libvndksupport.so`) in the manifest.
11. Keep `-Xskip-metadata-version-check` (LiteRT-LM is built with Kotlin 2.4 metadata; the toolchain here is 2.2.21).
12. arm64-v8a only.

## LiteRT-LM Kotlin API (0.17.1) as used here

```kotlin
val engine = Engine(EngineConfig(
  modelPath = path, backend = Backend.CPU(threadCount = 4), // or Backend.GPU()
  visionBackend = Backend.CPU(), audioBackend = null,
  maxNumTokens = 2048, maxNumImages = 1, cacheDir = filesDir/"litert-cache"))
engine.initialize()                       // blocking; background thread only
val conv = engine.createConversation(ConversationConfig(
  samplerConfig = SamplerConfig(topK = 1, topP = 1.0, temperature = 1.0, seed = 0),
  maxOutputToken = 1536))
conv.sendMessageAsync(Contents.of(Content.ImageBytes(jpeg), Content.Text(prompt)))  // Flow<Message>
  // each Message = delta; text = message.contents.contents.filterIsInstance<Content.Text>().joinToString("") { it.text }
conv.cancelProcess(); conv.close(); engine.close()
```

Other API surface available but unused: `ThinkingConfig`, `ExperimentalFlags.enableSpeculativeDecoding` (multi-token prediction), `Capabilities(modelPath).hasSpeculativeDecodingSupport()`, `ResponseFormat` / constrained decoding, `ToolSet`, `Backend.NPU(nativeLibraryDir)` (NPU fails to register on Exynos).

## Build / deploy / debug (Windows)

```bash
export JAVA_HOME="/c/Program Files/Android/Android Studio/jbr"
export MSYS_NO_PATHCONV=1                                   # Git Bash: stop /data/... path mangling
gradle wrapper --gradle-version 9.2.1   # once; the prototype used a standalone gradle-9.2.1
./gradlew assembleDebug
ADB="$LOCALAPPDATA/Android/Sdk/platform-tools/adb.exe"
"$ADB" install -r app/build/outputs/apk/debug/app-debug.apk
"$ADB" shell mkdir -p /data/local/tmp/gemma-ocr
"$ADB" push gemma-4-E2B-it.litertlm /data/local/tmp/gemma-ocr/ && "$ADB" shell chmod 644 /data/local/tmp/gemma-ocr/gemma-4-E2B-it.litertlm
# headless test: open with an image via share intent
"$ADB" shell am start -a android.intent.action.SEND -t image/jpeg --eu android.intent.extra.STREAM file:///data/local/tmp/gemma-ocr/test.jpg -n com.impacto.gemmaocr/.MainActivity
"$ADB" logcat -d | grep -iE "litert|tflite|GemmaEngine|lowmemorykiller|signal 9"
"$ADB" shell uiautomator dump /sdcard/ui.xml && "$ADB" shell cat /sdcard/ui.xml   # read on-screen status text
"$ADB" shell run-as com.impacto.gemmaocr ls -la files/litert-cache                # cache files: delete one by one (run-as sh -c globbing fails)
```

SDK: platform `android-37.0`, build-tools `36.0.0`. AGP 9.0.1, Gradle 9.2.1, Kotlin Compose plugin 2.2.21, JDK 25 (Android Studio JBR).

## Working agreements with the owner

- The owner tests on the physical phone and has **interrupted automated adb tap-driven tests before**. Ask before driving the UI with `input tap`; installing builds and reading logs is fine.
- Explain results plainly with measured numbers; say explicitly when something is untested.
- Do not claim a root cause without log evidence (a wrong GPU diagnosis was made once).

## Next tasks (priority order)

1. Add the Gradle wrapper, `.gitignore` (`build/`, `.gradle/`, `local.properties`, `*.litertlm`), `git init`.
2. Test on device and record: E2B CPU load/cached load; E2B GPU single attempt (confirm crash guard); **Fast OCR + field detection** timing and quality (expected production path); AI whole vs split on a dense receipt; loop guard on a blurry photo.
3. JVM unit tests: `parseFields`, `mergeStripText`, `trimRepetition`, `isLooping` (move it to a top-level function to test), `strips`.
4. In-app model download (WorkManager, resumable, checksum) into `getExternalFilesDir(null)`; remove the dependency on `/data/local/tmp`.
5. JSON output via constrained decoding / `ResponseFormat` with a fixed field schema.
6. Keep ML Kit line bounding boxes and link fields to boxes (for mapping to UI elements).
7. Add ML Kit Devanagari recognizer + script selector.
8. Cache management UI (size + clear), release signing config.

## Source code (authoritative snapshot, 2026-09-17)

### A.1 `settings.gradle.kts`

```kotlin
pluginManagement {
  repositories { google(); mavenCentral(); gradlePluginPortal() }
}
dependencyResolutionManagement {
  repositoriesMode.set(RepositoriesMode.FAIL_ON_PROJECT_REPOS)
  repositories { google(); mavenCentral() }
}
rootProject.name = "GemmaOCR"
include(":app")
```

### A.2 `build.gradle.kts`

```kotlin
plugins {
  id("com.android.application") version "9.0.1" apply false
  id("org.jetbrains.kotlin.plugin.compose") version "2.2.21" apply false
}
```

### A.3 `gradle.properties`

```properties
org.gradle.jvmargs=-Xmx4g -Dfile.encoding=UTF-8
android.useAndroidX=true
kotlin.code.style=official
```

### A.4 `app/build.gradle.kts`

```kotlin
import org.jetbrains.kotlin.gradle.dsl.JvmTarget

plugins {
  id("com.android.application")
  id("org.jetbrains.kotlin.plugin.compose")
}

android {
  namespace = "com.impacto.gemmaocr"
  compileSdk { this.version = release(37) { minorApiLevel = 0 } }

  defaultConfig {
    applicationId = "com.impacto.gemmaocr"
    minSdk = 28
    targetSdk = 36
    versionCode = 1
    versionName = "1.0"
    // Phones only: drops the x86_64 native libs and halves the APK size.
    ndk { abiFilters += "arm64-v8a" }
  }

  buildTypes {
    release {
      isMinifyEnabled = false
      signingConfig = signingConfigs.getByName("debug")
    }
  }
  compileOptions {
    sourceCompatibility = JavaVersion.VERSION_11
    targetCompatibility = JavaVersion.VERSION_11
  }
  buildFeatures { compose = true }
}

kotlin {
  compilerOptions {
    jvmTarget.set(JvmTarget.JVM_11)
    // litertlm is compiled with a newer Kotlin than this toolchain.
    freeCompilerArgs.addAll("-Xskip-metadata-version-check")
  }
}

dependencies {
  implementation("com.google.ai.edge.litertlm:litertlm-android:0.17.1")
  implementation("com.google.mlkit:text-recognition:16.0.1")

  implementation(platform("androidx.compose:compose-bom:2026.02.00"))
  implementation("androidx.compose.ui:ui")
  implementation("androidx.compose.material3:material3")
  implementation("androidx.activity:activity-compose:1.10.1")
  implementation("androidx.lifecycle:lifecycle-viewmodel-compose:2.8.7")
  implementation("androidx.core:core-ktx:1.15.0")
}
```

### A.5 `app/src/main/AndroidManifest.xml`

```xml
<?xml version="1.0" encoding="utf-8"?>
<manifest xmlns:android="http://schemas.android.com/apk/res/android">

  <application
    android:label="Gemma OCR"
    android:theme="@android:style/Theme.Material.Light.NoActionBar">

    <!-- Lets LiteRT-LM reach the phone's GPU (OpenCL). -->
    <uses-native-library android:name="libvndksupport.so" android:required="false" />
    <uses-native-library android:name="libOpenCL.so" android:required="false" />

    <activity
      android:name=".MainActivity"
      android:exported="true"
      android:configChanges="orientation|screenSize|screenLayout|keyboardHidden">
      <intent-filter>
        <action android:name="android.intent.action.MAIN" />
        <category android:name="android.intent.category.LAUNCHER" />
      </intent-filter>
      <!-- Accept images shared from other apps. -->
      <intent-filter>
        <action android:name="android.intent.action.SEND" />
        <category android:name="android.intent.category.DEFAULT" />
        <data android:mimeType="image/*" />
      </intent-filter>
    </activity>

    <provider
      android:name="androidx.core.content.FileProvider"
      android:authorities="${applicationId}.files"
      android:exported="false"
      android:grantUriPermissions="true">
      <meta-data
        android:name="android.support.FILE_PROVIDER_PATHS"
        android:resource="@xml/file_paths" />
    </provider>
  </application>
</manifest>
```

### A.6 `app/src/main/res/xml/file_paths.xml`

```xml
<?xml version="1.0" encoding="utf-8"?>
<paths>
  <cache-path name="photos" path="photos/" />
</paths>
```

### A.7 `app/src/main/java/com/impacto/gemmaocr/Images.kt`

```kotlin
package com.impacto.gemmaocr

import android.content.Context
import android.graphics.Bitmap
import android.graphics.ImageDecoder
import android.net.Uri
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import java.io.ByteArrayOutputStream
import kotlin.coroutines.resume
import kotlin.coroutines.resumeWithException
import kotlin.math.max
import kotlinx.coroutines.suspendCancellableCoroutine

/**
 * Decodes straight to the target size (never holds the full 12–50 MP photo in memory) and
 * applies EXIF rotation, so camera photos arrive upright.
 */
fun decodeScaled(context: Context, uri: Uri, maxSide: Int): Bitmap {
  val source = ImageDecoder.createSource(context.contentResolver, uri)
  return ImageDecoder.decodeBitmap(source) { decoder, info, _ ->
    val longSide = max(info.size.width, info.size.height)
    if (longSide > maxSide) {
      val scale = maxSide.toFloat() / longSide
      decoder.setTargetSize(
        (info.size.width * scale).toInt().coerceAtLeast(1),
        (info.size.height * scale).toInt().coerceAtLeast(1),
      )
    }
    decoder.allocator = ImageDecoder.ALLOCATOR_SOFTWARE
    decoder.isMutableRequired = false
  }
}

/**
 * Splits the image into full-width horizontal strips with overlap.
 *
 * The on-device Gemma vision encoder sees at most 280 image tokens per image, so a whole page
 * becomes too blurry to read. Each strip gets its own 280-token budget, making text ~2–4× sharper.
 */
fun Bitmap.strips(maxStrips: Int = 6): List<Bitmap> {
  val stripHeight = minOf(height, (width * 0.6f).toInt())
  if (stripHeight >= height) return listOf(this)
  val overlap = (stripHeight * 0.15f).toInt()
  val step = stripHeight - overlap
  val count = ((height - overlap + step - 1) / step).coerceIn(1, maxStrips)
  // Re-spread evenly so the last strip ends exactly at the bottom.
  val realStep = if (count == 1) 0 else (height - stripHeight) / (count - 1)
  val realHeight = if (count == 1) height else maxOf(stripHeight, height - realStep * (count - 1))
  return List(count) { i ->
    val top = (i * realStep).coerceAtMost(height - realHeight)
    Bitmap.createBitmap(this, 0, top, width, realHeight)
  }
}

/** Joins strip outputs, dropping lines repeated because the strips overlap. */
fun mergeStripText(previous: String, next: String): String {
  if (previous.isBlank()) return next.trim()
  val prevLines = previous.trimEnd().lines()
  val nextLines = next.trim().lines()
  val norm = { s: String -> s.trim().lowercase().replace(Regex("\\s+"), " ") }
  var skip = 0
  for (n in minOf(6, prevLines.size, nextLines.size) downTo 1) {
    if (prevLines.takeLast(n).map(norm) == nextLines.take(n).map(norm)) {
      skip = n
      break
    }
  }
  // A line cut in half at the strip edge: drop the partial copy.
  if (skip == 0 && nextLines.isNotEmpty() && prevLines.isNotEmpty()) {
    val first = norm(nextLines.first())
    val last = norm(prevLines.last())
    if (first.length >= 8 && (last.endsWith(first) || first.startsWith(last) || last.contains(first))) skip = 1
  }
  return (prevLines + nextLines.drop(skip)).joinToString("\n")
}

/** Removes a runaway repetition at the end, e.g. "abc abc abc abc" -> "abc". */
fun trimRepetition(text: String): String {
  var t = text.trimEnd()
  Regex("(.{2,60}?)(?:\\s*\\1){3,}\\s*$", RegexOption.DOT_MATCHES_ALL).find(t)?.let {
    t = t.substring(0, it.range.first) + it.groupValues[1]
  }
  return t
}

fun Bitmap.toJpeg(quality: Int = 92): ByteArray =
  ByteArrayOutputStream().also { compress(Bitmap.CompressFormat.JPEG, quality, it) }.toByteArray()

private val latinRecognizer by lazy { TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS) }

/** Google ML Kit on-device OCR: tiny, sub-second, no model download. */
suspend fun mlKitOcr(bitmap: Bitmap): String = suspendCancellableCoroutine { cont ->
  latinRecognizer
    .process(InputImage.fromBitmap(bitmap, 0))
    .addOnSuccessListener { cont.resume(it.text) }
    .addOnFailureListener { cont.resumeWithException(it) }
}
```

### A.8 `app/src/main/java/com/impacto/gemmaocr/GemmaEngine.kt`

```kotlin
package com.impacto.gemmaocr

import android.content.Context
import android.util.Log
import com.google.ai.edge.litertlm.Backend
import com.google.ai.edge.litertlm.Content
import com.google.ai.edge.litertlm.Contents
import com.google.ai.edge.litertlm.Conversation
import com.google.ai.edge.litertlm.ConversationConfig
import com.google.ai.edge.litertlm.Engine
import com.google.ai.edge.litertlm.EngineConfig
import com.google.ai.edge.litertlm.SamplerConfig
import java.io.File
import kotlinx.coroutines.flow.Flow
import kotlinx.coroutines.flow.map
import kotlinx.coroutines.flow.onCompletion

private const val TAG = "GemmaEngine"

/** Folders searched for .litertlm files. The first one is where adb pushes the model. */
fun modelDirs(context: Context): List<File> =
  listOfNotNull(File("/data/local/tmp/gemma-ocr"), context.getExternalFilesDir(null))

fun findModels(context: Context): List<File> =
  modelDirs(context)
    .flatMap { dir -> dir.listFiles { f -> f.name.endsWith(".litertlm") && f.canRead() }?.toList().orEmpty() }
    .filterNot { it.name.contains("-web") } // browser builds are text-only
    .distinctBy { it.name }
    // Prefer E4B (more accurate) over E2B.
    .sortedByDescending { it.name.contains("E4B", ignoreCase = true) }

// No "[unclear]" escape hatch: with greedy decoding the model latches onto it and repeats it forever.
const val OCR_PROMPT =
  "Extract all the text from this image exactly as written, top to bottom, keeping the line " +
    "breaks. Do not summarise, translate or explain. Output only the text."

const val FIELDS_PROMPT =
  "The text below was read from an image (a document, card, label, form or screenshot). " +
    "Work out what each important piece of information is and output one field per line in the " +
    "form `Label: value`.\n" +
    "Use these labels when they apply: Name, Company, Designation, Address, Phone, Email, Website, " +
    "Date, Invoice number, Order number, Total amount, Tax, GSTIN, PAN, ID number, Account number, " +
    "IFSC, Pincode. For anything else important, make up a short clear label.\n" +
    "Rules: join an address that spans several lines into one line; list each phone number or " +
    "email on its own line; copy values exactly as they appear; never invent values that are not " +
    "in the text; skip boilerplate. Output only the `Label: value` lines.\n\nTEXT:\n"

/** Parses `Label: value` lines, tolerating the markdown bullets/bold the model sometimes adds. */
fun parseFields(raw: String): List<Pair<String, String>> =
  raw
    .lines()
    .mapNotNull { line ->
      val m = Regex("""^\s*(?:[-*•]\s*)?\**\s*([^:*]{1,40}?)\s*\**\s*:\s*\**\s*(.+?)\s*\**\s*$""").find(line)
        ?: return@mapNotNull null
      val label = m.groupValues[1].trim()
      val value = m.groupValues[2].trim()
      if (label.isEmpty() || value.isEmpty() || value.equals("n/a", true) || value == "-") null
      else label to value
    }
    .distinct()

/**
 * Holds one loaded Gemma model.
 *
 * Memory notes: the .litertlm file is memory-mapped, so weights are paged in from flash only
 * when used, and Gemma's per-layer embeddings stay on storage instead of in RAM. The GPU
 * backend keeps the main weights in GPU memory, which leaves far more free RAM than CPU mode.
 */
class GemmaEngine private constructor(
  private val engine: Engine,
  val modelName: String,
  val backendName: String,
) : AutoCloseable {

  @Volatile private var active: Conversation? = null

  /** Streams the extracted text. A fresh conversation per image keeps the KV cache small. */
  fun extract(jpegBytes: ByteArray): Flow<String> =
    // Image first, then the instruction, matching the order the model was trained on.
    run(Contents.of(Content.ImageBytes(jpegBytes), Content.Text(OCR_PROMPT)))

  /**
   * Labels already-extracted text (Name, Address, Phone…). Text-only, so it skips the vision
   * encoder entirely and takes seconds instead of the ~35 s an image costs on CPU.
   */
  fun detectFields(text: String): Flow<String> =
    run(Contents.of(Content.Text(FIELDS_PROMPT + text.take(MAX_FIELD_INPUT_CHARS))))

  private fun run(input: Contents): Flow<String> {
    val conversation =
      engine.createConversation(
        ConversationConfig(
          // Greedy decoding: we want the single most likely reading, not creativity.
          samplerConfig = SamplerConfig(topK = 1, topP = 1.0, temperature = 1.0, seed = 0),
          maxOutputToken = MAX_OUTPUT_TOKENS,
        )
      )
    active = conversation
    return conversation
      .sendMessageAsync(input)
      .map { message -> message.contents.contents.filterIsInstance<Content.Text>().joinToString("") { it.text } }
      .onCompletion {
        active = null
        conversation.close()
      }
  }

  fun stop() {
    runCatching { active?.cancelProcess() }
  }

  override fun close() {
    stop()
    runCatching { engine.close() }
  }

  companion object {
    // Image tokens + text output. The KV cache grows with this, so keep it no larger than OCR needs.
    private const val MAX_TOTAL_TOKENS = 2048
    private const val MAX_OUTPUT_TOKENS = 1536
    // ~1,000 tokens of input, leaving room in the 2048-token context for the answer.
    private const val MAX_FIELD_INPUT_CHARS = 3500

    private fun backendOf(name: String): Backend =
      if (name == "GPU") Backend.GPU() else Backend.CPU(threadCount = cpuThreads())

    /** Big cores only: small cores slow the whole matrix multiply down. */
    private fun cpuThreads() = Runtime.getRuntime().availableProcessors().coerceAtMost(8).let { if (it >= 8) 4 else it }

    /**
     * Loads on the calling thread (call from a background dispatcher). Tries [preferred] first; GPU
     * falls back to CPU if it throws, but CPU never falls back to GPU (GPU needs more RAM).
     *
     * CPU mode reads weights straight from the memory-mapped file, so the OS can page them in and
     * out on demand. That is what makes a 4B-effective model survive on a 6 GB phone. On Mali GPUs
     * LiteRT has to build extra CPU-side copies of the weights, which can run the phone out of RAM.
     */
    fun load(
      context: Context,
      model: File,
      preferred: String,
      // GPU vision is faster but returns garbage on some GPUs (e.g. Mali on Exynos), so it is opt-in.
      hybridVision: Boolean = false,
      onStatus: (String) -> Unit,
    ): GemmaEngine {
      // Compiled GPU kernels / CPU weight caches live here, so later loads are much faster.
      val cacheDir = File(context.filesDir, "litert-cache").apply { mkdirs() }.absolutePath
      val attempts = if (preferred == "GPU") listOf("GPU", "CPU") else listOf("CPU")
      var lastError: Throwable? = null
      for (name in attempts) {
        onStatus("Loading ${model.name} on $name… (first load takes a few minutes)")
        val engine =
          Engine(
            EngineConfig(
              modelPath = model.absolutePath,
              backend = backendOf(name),
              // Hybrid: the small vision encoder fits on the GPU even when the LLM does not.
              visionBackend = if (name == "CPU" && hybridVision) Backend.GPU() else backendOf(name),
              audioBackend = null, // audio encoder is never loaded
              maxNumTokens = MAX_TOTAL_TOKENS,
              maxNumImages = 1,
              cacheDir = cacheDir,
            )
          )
        try {
          engine.initialize()
          return GemmaEngine(engine, model.name.removeSuffix(".litertlm"), name)
        } catch (t: Throwable) {
          Log.w(TAG, "$name backend failed", t)
          runCatching { engine.close() }
          lastError = t
        }
      }
      throw IllegalStateException("Could not load model: ${lastError?.message}", lastError)
    }
  }
}
```

### A.9 `app/src/main/java/com/impacto/gemmaocr/OcrViewModel.kt`

```kotlin
package com.impacto.gemmaocr

import android.app.ActivityManager
import android.app.Application
import android.content.Context
import android.graphics.Bitmap
import android.net.Uri
import android.os.SystemClock
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.lifecycle.AndroidViewModel
import androidx.lifecycle.viewModelScope
import java.io.File
import java.util.Locale
import kotlinx.coroutines.CancellationException
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.Job
import kotlinx.coroutines.flow.flowOn
import kotlinx.coroutines.launch
import kotlinx.coroutines.sync.Mutex
import kotlinx.coroutines.sync.withLock
import kotlinx.coroutines.withContext

enum class Mode { AI, FAST }

enum class Detail(val label: String, val maxSide: Int, val split: Boolean) {
  WHOLE("Whole image (fast)", 1024, false),
  STRIPS("Split (small text)", 2048, true),
}

class OcrViewModel(app: Application) : AndroidViewModel(app) {
  var models by mutableStateOf(findModels(app))
    private set
  var selectedModel by mutableStateOf(models.firstOrNull())
    private set
  var mode by mutableStateOf(if (models.isEmpty()) Mode.FAST else Mode.AI)
  var detail by mutableStateOf(Detail.STRIPS)
    private set

  var modelStatus by mutableStateOf(if (models.isEmpty()) "No model found" else "Model not loaded")
    private set
  var modelReady by mutableStateOf(false)
    private set

  var imageUri by mutableStateOf<Uri?>(null)
    private set
  var preview by mutableStateOf<Bitmap?>(null)
    private set

  var output by mutableStateOf("")
    private set

  /** AI-labelled fields, e.g. ("Name", "Ravi Kumar"). */
  var fields by mutableStateOf<List<Pair<String, String>>>(emptyList())
    private set
  var fieldsStatus by mutableStateOf("")
    private set
  var stats by mutableStateOf("")
    private set
  var busy by mutableStateOf(false)
    private set
  var error by mutableStateOf<String?>(null)
    private set

  private val prefs = app.getSharedPreferences("settings", Context.MODE_PRIVATE)

  /** Run the text-only "Name / Address / Phone…" labelling step after reading the text. */
  var detectFieldsEnabled by mutableStateOf(prefs.getBoolean(KEY_FIELDS, true))
    private set

  fun toggleDetectFields(on: Boolean) {
    detectFieldsEnabled = on
    prefs.edit().putBoolean(KEY_FIELDS, on).apply()
  }

  /** "CPU" or "GPU". Phones under ~8 GB start on CPU, which uses far less RAM. */
  var backend by mutableStateOf(prefs.getString(KEY_BACKEND, null) ?: defaultBackend(app))
    private set

  private var engine: GemmaEngine? = null
  private val engineLock = Mutex()
  private var job: Job? = null

  init {
    // A process kill can't be caught, so a leftover marker means the last load ran out of memory.
    val crashedBackend = prefs.getString(KEY_LOADING, null)
    if (crashedBackend != null) {
      prefs.edit().remove(KEY_LOADING).apply()
      if (crashedBackend == "GPU") {
        backend = "CPU"
        prefs.edit().putString(KEY_BACKEND, "CPU").apply()
        error = "Last GPU load ran out of memory, so the app switched to CPU."
      } else {
        error = "Last load ran out of memory. Close other apps, or use the E2B model."
      }
    }
    preload()
  }

  fun selectBackend(name: String) {
    if (name == backend || busy) return
    backend = name
    prefs.edit().putString(KEY_BACKEND, name).apply()
    error = null
    viewModelScope.launch(Dispatchers.IO) {
      engineLock.withLock { unloadLocked() }
      preload()
    }
  }

  fun rescanModels() {
    models = findModels(getApplication())
    if (selectedModel == null || selectedModel !in models) selectedModel = models.firstOrNull()
    if (models.isEmpty()) modelStatus = "No model found" else preload()
  }

  fun selectModel(file: File) {
    if (file == selectedModel || busy) return
    selectedModel = file
    viewModelScope.launch(Dispatchers.IO) {
      engineLock.withLock { unloadLocked() }
      preload()
    }
  }

  fun changeDetail(newDetail: Detail) {
    detail = newDetail
    imageUri?.let { setImage(it) }
  }

  /** Loads the model in the background so the first extraction doesn't pay the load time. */
  fun preload() {
    if (selectedModel == null) return
    viewModelScope.launch {
      try {
        ensureEngine()
      } catch (e: Exception) {
        error = e.message
      }
    }
  }

  private suspend fun ensureEngine(): GemmaEngine =
    withContext(Dispatchers.IO) {
      engineLock.withLock {
        engine?.let { return@withLock it }
        val model = selectedModel ?: throw IllegalStateException("No model file found")
        modelReady = false
        val start = SystemClock.elapsedRealtime()
        prefs.edit().putString(KEY_LOADING, backend).commit()
        try {
          GemmaEngine.load(getApplication(), model, backend) { modelStatus = it }.also {
            engine = it
            modelReady = true
            modelStatus = "${it.modelName} • ${it.backendName} • loaded in ${secs(start)}"
          }
        } catch (e: Throwable) {
          modelStatus = "Load failed"
          throw e
        } finally {
          prefs.edit().remove(KEY_LOADING).apply()
        }
      }
    }

  fun setImage(uri: Uri) {
    imageUri = uri
    output = ""
    fields = emptyList()
    fieldsStatus = ""
    stats = ""
    error = null
    viewModelScope.launch {
      try {
        preview = withContext(Dispatchers.IO) { decodeScaled(getApplication(), uri, detail.maxSide) }
      } catch (e: Exception) {
        error = "Could not open image: ${e.message}"
      }
    }
  }

  fun extract() {
    val bitmap = preview ?: return
    if (busy) return
    error = null
    output = ""
    fields = emptyList()
    fieldsStatus = ""
    stats = ""
    busy = true
    job =
      viewModelScope.launch {
        try {
          when (mode) {
            Mode.FAST -> {
              val start = SystemClock.elapsedRealtime()
              output = mlKitOcr(bitmap)
              stats = "ML Kit • ${secs(start)}"
            }
            Mode.AI -> runGemma(bitmap)
          }
          if (output.isBlank()) error = "No text found"
          else if (detectFieldsEnabled && selectedModel != null) runFieldDetection(output)
        } catch (e: CancellationException) {
          stats = "Stopped. $stats"
        } catch (e: Throwable) {
          error = e.message ?: e.toString()
        } finally {
          busy = false
        }
      }
  }

  private class RepetitionDetected : Exception()

  private suspend fun runGemma(bitmap: Bitmap) {
    val loadedEngine =
      if (modelReady) engine!!
      else {
        stats = "Waiting for model to load…"
        ensureEngine()
      }
    val parts = withContext(Dispatchers.Default) { if (detail.split) bitmap.strips() else listOf(bitmap) }
    val start = SystemClock.elapsedRealtime()
    var tokens = 0
    var genMillis = 0L
    var merged = ""
    for ((index, part) in parts.withIndex()) {
      val label = if (parts.size > 1) "Part ${index + 1}/${parts.size}: " else ""
      stats = "${label}reading image…"
      val jpeg = withContext(Dispatchers.Default) { part.toJpeg() }
      val partText = StringBuilder()
      var firstTextAt = 0L
      try {
        loadedEngine.extract(jpeg).flowOn(Dispatchers.IO).collect { piece ->
          if (piece.isEmpty()) return@collect
          if (firstTextAt == 0L) firstTextAt = SystemClock.elapsedRealtime()
          tokens++
          partText.append(piece)
          output = mergeStripText(merged, partText.toString())
          stats = "${label}writing… $tokens tokens"
          if (isLooping(partText)) throw RepetitionDetected()
        }
      } catch (e: RepetitionDetected) {
        // Keep what was read before the loop and move on to the next part.
        loadedEngine.stop()
      }
      if (firstTextAt != 0L) genMillis += SystemClock.elapsedRealtime() - firstTextAt
      merged = mergeStripText(merged, trimRepetition(partText.toString()))
      output = merged
    }
    val tokPerSec = tokens / (genMillis.coerceAtLeast(1) / 1000.0)
    stats =
      "${loadedEngine.modelName} • ${loadedEngine.backendName} • ${parts.size} part(s) • " +
        "total ${secs(start)} • ${fmt(tokPerSec)} tok/s"
  }

  private suspend fun runFieldDetection(text: String) {
    val loadedEngine =
      if (modelReady) engine!!
      else {
        fieldsStatus = "Waiting for model to load…"
        ensureEngine()
      }
    fieldsStatus = "AI is labelling the fields…"
    val start = SystemClock.elapsedRealtime()
    val raw = StringBuilder()
    try {
      loadedEngine.detectFields(text).flowOn(Dispatchers.IO).collect { piece ->
        raw.append(piece)
        fields = parseFields(raw.toString())
        if (isLooping(raw)) throw RepetitionDetected()
      }
    } catch (e: RepetitionDetected) {
      loadedEngine.stop()
    }
    fields = parseFields(trimRepetition(raw.toString()))
    fieldsStatus =
      if (fields.isEmpty()) "No fields recognised" else "${fields.size} fields • ${secs(start)}"
  }

  fun stop() {
    engine?.stop()
    job?.cancel()
  }

  /** Called when Android asks for memory back while the app is in the background. */
  fun releaseModel() {
    if (busy || engine == null) return
    viewModelScope.launch(Dispatchers.IO) {
      engineLock.withLock {
        if (!busy) {
          unloadLocked()
          modelStatus = "Unloaded to free memory • reloads when you return"
        }
      }
    }
  }

  fun onReturnToForeground() {
    if (engine == null && selectedModel != null) preload()
  }

  private fun unloadLocked() {
    engine?.close()
    engine = null
    modelReady = false
    modelStatus = "Model not loaded"
  }

  override fun onCleared() {
    engine?.close()
    engine = null
  }

  /** True when the tail of the output is the same short chunk repeated, e.g. "[unclear] [unclear] …". */
  private fun isLooping(text: CharSequence): Boolean {
    if (text.length < 80) return false
    val tail = text.takeLast(240).toString()
    for (period in 2..40) {
      val unit = tail.takeLast(period)
      if (unit.isBlank()) continue
      val repeats = tail.length / period
      if (repeats >= 6 && tail.takeLast(period * 6) == unit.repeat(6)) return true
    }
    // Same non-empty line six times in a row.
    val lines = tail.lines().map { it.trim() }.filter { it.isNotEmpty() }.takeLast(6)
    return lines.size == 6 && lines.distinct().size == 1
  }

  private companion object {
    const val KEY_BACKEND = "backend"
    const val KEY_LOADING = "loading_backend"
    const val KEY_FIELDS = "detect_fields"

    fun defaultBackend(context: Context): String {
      val mem = ActivityManager.MemoryInfo()
      context.getSystemService(ActivityManager::class.java).getMemoryInfo(mem)
      return if (mem.totalMem >= 7_500_000_000L) "GPU" else "CPU"
    }
  }

  private fun secs(start: Long) = fmt((SystemClock.elapsedRealtime() - start) / 1000.0) + "s"

  private fun fmt(v: Double) = String.format(Locale.US, "%.1f", v)
}
```

### A.10 `app/src/main/java/com/impacto/gemmaocr/MainActivity.kt`

```kotlin
package com.impacto.gemmaocr

import android.content.ClipData
import android.content.ClipboardManager
import android.content.ComponentCallbacks2
import android.content.Intent
import android.net.Uri
import android.os.Bundle
import android.widget.Toast
import androidx.activity.ComponentActivity
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.compose.setContent
import androidx.activity.enableEdgeToEdge
import androidx.activity.result.PickVisualMediaRequest
import androidx.activity.result.contract.ActivityResultContracts
import androidx.activity.viewModels
import androidx.compose.foundation.Image
import androidx.compose.foundation.clickable
import androidx.compose.foundation.isSystemInDarkTheme
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.fillMaxSize
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.safeDrawingPadding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.selection.SelectionContainer
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Button
import androidx.compose.material3.Card
import androidx.compose.material3.CardDefaults
import androidx.compose.material3.CircularProgressIndicator
import androidx.compose.material3.FilterChip
import androidx.compose.material3.HorizontalDivider
import androidx.compose.material3.Switch
import androidx.compose.material3.MaterialTheme
import androidx.compose.material3.OutlinedButton
import androidx.compose.material3.SegmentedButton
import androidx.compose.material3.SegmentedButtonDefaults
import androidx.compose.material3.SingleChoiceSegmentedButtonRow
import androidx.compose.material3.Surface
import androidx.compose.material3.Text
import androidx.compose.material3.darkColorScheme
import androidx.compose.material3.lightColorScheme
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.asImageBitmap
import androidx.compose.ui.layout.ContentScale
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.core.content.FileProvider
import androidx.core.content.IntentCompat
import java.io.File

class MainActivity : ComponentActivity() {
  private val vm: OcrViewModel by viewModels()

  override fun onCreate(savedInstanceState: Bundle?) {
    super.onCreate(savedInstanceState)
    enableEdgeToEdge()
    if (savedInstanceState == null) handleShare(intent)
    setContent {
      val colors = if (isSystemInDarkTheme()) darkColorScheme() else lightColorScheme()
      MaterialTheme(colorScheme = colors) {
        Surface(Modifier.fillMaxSize()) { OcrScreen(vm) }
      }
    }
  }

  override fun onNewIntent(intent: Intent) {
    super.onNewIntent(intent)
    handleShare(intent)
  }

  override fun onStart() {
    super.onStart()
    vm.onReturnToForeground()
  }

  @Suppress("DEPRECATION")
  override fun onTrimMemory(level: Int) {
    super.onTrimMemory(level)
    // Free the model's GPU/RAM when we're in the background and the system is short on memory,
    // so Android doesn't kill the whole app.
    if (level >= ComponentCallbacks2.TRIM_MEMORY_BACKGROUND) vm.releaseModel()
  }

  private fun handleShare(intent: Intent?) {
    if (intent?.action != Intent.ACTION_SEND) return
    IntentCompat.getParcelableExtra(intent, Intent.EXTRA_STREAM, Uri::class.java)?.let(vm::setImage)
  }
}

@Composable
private fun OcrScreen(vm: OcrViewModel) {
  val context = LocalContext.current
  val photoFile = remember { File(context.cacheDir, "photos/capture.jpg").apply { parentFile?.mkdirs() } }
  val photoUri = remember {
    FileProvider.getUriForFile(context, "${context.packageName}.files", photoFile)
  }
  val takePhoto =
    rememberLauncherForActivityResult(ActivityResultContracts.TakePicture()) { ok ->
      if (ok) vm.setImage(photoUri)
    }
  val pickPhoto =
    rememberLauncherForActivityResult(ActivityResultContracts.PickVisualMedia()) { uri ->
      uri?.let(vm::setImage)
    }

  Column(
    Modifier.fillMaxSize().safeDrawingPadding().verticalScroll(rememberScrollState()).padding(16.dp),
    verticalArrangement = Arrangement.spacedBy(12.dp),
  ) {
    Text("Gemma OCR", style = MaterialTheme.typography.headlineSmall, fontWeight = FontWeight.SemiBold)

    ModelCard(vm)

    SingleChoiceSegmentedButtonRow(Modifier.fillMaxWidth()) {
      SegmentedButton(
        selected = vm.mode == Mode.AI,
        onClick = { vm.mode = Mode.AI },
        shape = SegmentedButtonDefaults.itemShape(0, 2),
        enabled = vm.models.isNotEmpty() && !vm.busy,
      ) { Text("AI (Gemma)") }
      SegmentedButton(
        selected = vm.mode == Mode.FAST,
        onClick = { vm.mode = Mode.FAST },
        shape = SegmentedButtonDefaults.itemShape(1, 2),
        enabled = !vm.busy,
      ) { Text("Fast OCR") }
    }

    if (vm.models.isNotEmpty()) {
      Row(verticalAlignment = Alignment.CenterVertically, modifier = Modifier.fillMaxWidth()) {
        Column(Modifier.weight(1f)) {
          Text("Detect fields with AI", style = MaterialTheme.typography.bodyMedium)
          Text(
            "Name, Address, Phone… (text only, a few seconds)",
            style = MaterialTheme.typography.bodySmall,
            color = MaterialTheme.colorScheme.onSurfaceVariant,
          )
        }
        Switch(checked = vm.detectFieldsEnabled, onCheckedChange = vm::toggleDetectFields, enabled = !vm.busy)
      }
    }

    Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
      Text("Detail:", style = MaterialTheme.typography.bodyMedium)
      Detail.entries.forEach { d ->
        FilterChip(
          selected = vm.detail == d,
          onClick = { vm.changeDetail(d) },
          label = { Text(d.label) },
          enabled = !vm.busy,
        )
      }
    }

    Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
      OutlinedButton(
        onClick = { takePhoto.launch(photoUri) },
        enabled = !vm.busy,
        modifier = Modifier.weight(1f),
      ) { Text("Camera") }
      OutlinedButton(
        onClick = { pickPhoto.launch(PickVisualMediaRequest(ActivityResultContracts.PickVisualMedia.ImageOnly)) },
        enabled = !vm.busy,
        modifier = Modifier.weight(1f),
      ) { Text("Gallery") }
    }

    vm.preview?.let { bmp ->
      Image(
        bitmap = remember(bmp) { bmp.asImageBitmap() },
        contentDescription = "Selected image",
        contentScale = ContentScale.Fit,
        modifier = Modifier.fillMaxWidth().heightIn(max = 260.dp),
      )
    }

    if (vm.busy) {
      Button(onClick = vm::stop, modifier = Modifier.fillMaxWidth()) {
        CircularProgressIndicator(Modifier.size(18.dp), strokeWidth = 2.dp, color = MaterialTheme.colorScheme.onPrimary)
        Spacer(Modifier.size(8.dp))
        Text("Stop")
      }
    } else {
      Button(
        onClick = vm::extract,
        enabled = vm.preview != null,
        modifier = Modifier.fillMaxWidth(),
      ) { Text("Extract text") }
    }

    if (vm.stats.isNotEmpty()) {
      Text(vm.stats, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
    }
    vm.error?.let { Text(it, color = MaterialTheme.colorScheme.error, style = MaterialTheme.typography.bodyMedium) }

    if (vm.fields.isNotEmpty() || vm.fieldsStatus.isNotEmpty()) {
      Text("Detected fields", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
      if (vm.fieldsStatus.isNotEmpty()) {
        Text(vm.fieldsStatus, style = MaterialTheme.typography.bodySmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
      }
      Card(Modifier.fillMaxWidth()) {
        Column(Modifier.padding(vertical = 4.dp)) {
          vm.fields.forEachIndexed { i, (label, value) ->
            if (i > 0) HorizontalDivider()
            Row(
              Modifier.fillMaxWidth().clickable { copy(context, value) }.padding(horizontal = 12.dp, vertical = 8.dp),
              verticalAlignment = Alignment.CenterVertically,
            ) {
              Column(Modifier.weight(1f)) {
                Text(label, style = MaterialTheme.typography.labelMedium, color = MaterialTheme.colorScheme.primary)
                SelectionContainer { Text(value, style = MaterialTheme.typography.bodyLarge) }
              }
              Text("Copy", style = MaterialTheme.typography.labelSmall, color = MaterialTheme.colorScheme.onSurfaceVariant)
            }
          }
        }
      }
      if (vm.fields.isNotEmpty()) {
        OutlinedButton(
          onClick = { share(context, vm.fields.joinToString("\n") {"${it.first}: ${it.second}" }) },
          modifier = Modifier.fillMaxWidth(),
        ) { Text("Share fields") }
      }
    }

    if (vm.output.isNotEmpty()) {
      Text("Full text", style = MaterialTheme.typography.titleMedium, fontWeight = FontWeight.SemiBold)
      Card(colors = CardDefaults.cardColors(containerColor = MaterialTheme.colorScheme.surfaceVariant)) {
        SelectionContainer {
          Text(
            vm.output,
            fontFamily = FontFamily.Monospace,
            style = MaterialTheme.typography.bodyMedium,
            modifier = Modifier.fillMaxWidth().padding(12.dp),
          )
        }
      }
      Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        OutlinedButton(onClick = { copy(context, vm.output) }, modifier = Modifier.weight(1f)) { Text("Copy") }
        OutlinedButton(onClick = { share(context, vm.output) }, modifier = Modifier.weight(1f)) { Text("Share") }
      }
    }
  }
}

@Composable
private fun ModelCard(vm: OcrViewModel) {
  Card(Modifier.fillMaxWidth()) {
    Column(Modifier.padding(12.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
      if (vm.models.isEmpty()) {
        Text("No Gemma model found", fontWeight = FontWeight.SemiBold)
        Text(
          "Copy a .litertlm file to /data/local/tmp/gemma-ocr/ or to Android/data/com.impacto.gemmaocr/files/. " +
            "Fast OCR works without a model.",
          style = MaterialTheme.typography.bodySmall,
        )
        OutlinedButton(onClick = vm::rescanModels) { Text("Scan again") }
      } else {
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp)) {
          vm.models.forEach { f ->
            FilterChip(
              selected = f == vm.selectedModel,
              onClick = { vm.selectModel(f) },
              label = { Text(shortName(f.name)) },
              enabled = !vm.busy,
            )
          }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.CenterVertically) {
          Text("Run on:", style = MaterialTheme.typography.bodyMedium)
          listOf("CPU", "GPU").forEach { b ->
            FilterChip(
              selected = vm.backend == b,
              onClick = { vm.selectBackend(b) },
              label = { Text(if (b == "CPU") "CPU (low RAM)" else "GPU (8 GB+)") },
              enabled = !vm.busy,
            )
          }
        }
        Row(verticalAlignment = Alignment.CenterVertically) {
          if (!vm.modelReady && vm.modelStatus.startsWith("Loading")) {
            CircularProgressIndicator(Modifier.size(14.dp), strokeWidth = 2.dp)
            Spacer(Modifier.size(8.dp))
          }
          Text(vm.modelStatus, style = MaterialTheme.typography.bodySmall)
        }
      }
    }
  }
}

private fun shortName(file: String) =
  Regex("gemma-(\\w+)-(E\\dB)", RegexOption.IGNORE_CASE).find(file)?.let { "Gemma ${it.groupValues[1]} ${it.groupValues[2]}" }
    ?: file.removeSuffix(".litertlm")

private fun copy(context: android.content.Context, text: String) {
  context.getSystemService(ClipboardManager::class.java).setPrimaryClip(ClipData.newPlainText("OCR text", text))
  Toast.makeText(context, "Copied", Toast.LENGTH_SHORT).show()
}

private fun share(context: android.content.Context, text: String) {
  val send = Intent(Intent.ACTION_SEND).setType("text/plain").putExtra(Intent.EXTRA_TEXT, text)
  context.startActivity(Intent.createChooser(send, "Share text"))
}
```
