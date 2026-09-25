package com.modeltesting.vlmtester

import android.app.ActivityManager
import android.content.Context
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.pdf.PdfRenderer
import android.net.Uri
import android.os.Bundle
import android.os.Handler
import android.os.Looper
import android.os.ParcelFileDescriptor
import android.os.StatFs
import android.system.Os
import android.system.OsConstants
import android.view.View
import android.widget.ArrayAdapter
import android.widget.Toast
import androidx.appcompat.app.AppCompatActivity
import androidx.activity.result.contract.ActivityResultContracts
import com.modeltesting.vlmtester.databinding.ActivityMainBinding
import java.io.File
import java.io.FileOutputStream
import java.io.RandomAccessFile

class MainActivity : AppCompatActivity() {

    private lateinit var binding: ActivityMainBinding
    private val ui = Handler(Looper.getMainLooper())

    private var pickedImageFile: File? = null
    private var pickedBackImageFile: File? = null
    private var clkTck: Long = 100L

    // Kill switch state. currentProcess is set right after the child is spawned and
    // cleared once the background thread finishes, so the Stop button (main thread)
    // and the run thread never race on who owns cleanup.
    @Volatile private var currentProcess: Process? = null
    @Volatile private var currentConnection: java.net.HttpURLConnection? = null
    @Volatile private var userStopped = false

    // Prompts and grammars live in assets/{prompts,schemas}/, one pair per document type,
    // so each type gets its own field set instead of one schema trying to cover all four.
    // system.txt is shared; user_<type>.txt and schemas/<type>.json are selected by the
    // Document type spinner. See loadAsset() / promptFor() / schemaFor() below.
    // 2026-09-16: added Trinidad & Tobago passport, driving permit, and national ID as
    // new types, alongside the original four rather than replacing them, so all seven
    // stay available long-term.
    // "pan_crop" is a second prompt for the SAME document (PAN), not a new doc type:
    // "pan" itself is the full-card, multi-field prompt; "pan_crop" is a single-field,
    // no-grammar prompt for a tight single-field crop (validated: 21.2s cold / 0.87s
    // warm server-mode, exact match, vs the full multi-field prompt's heavier grammar-
    // constrained path). See singleFieldCropDocTypes below for what it skips.
    private val docTypes =
        listOf("pan", "aadhaar", "passport", "dl", "tt_passport", "tt_dl", "tt_id",
            "pan_stage1", "aadhaar_stage1", "pan_crop", "pan_full")

    // tt_id is the ONLY type where the data is split across two separate photos (front
    // has just the name; everything else is on the back). This set is what gates the
    // whole two-image flow - every other type stays on the single-image path below,
    // completely unaffected.
    private val twoSidedDocTypes = setOf("tt_id")

    // 2026-09-17: the "stage1-contract" fine-tune from MODEL_HANDOVER_stage1.md - a LoRA
    // merged into the stock VisionPsy-Nano-460M weights, scoped to PAN and Aadhaar only.
    // These two entries are the ONLY thing that activates it: pick "pan_stage1" or
    // "aadhaar_stage1" from the same doc-type spinner (plus the stage1 .gguf/mmproj in
    // the model spinners) to test it; pick "pan"/"aadhaar" again to go straight back to
    // the stock model - nothing else in the app changes either way. See promptFor() for
    // why these two skip the shared system prompt, and runExtraction() for why they skip
    // grammar-constrained decoding (-jf): the handover's whole point is measuring the
    // model's OWN parse-failure/dropped-key rate, which -jf would force to zero.
    private val stage1EvalDocTypes = setOf("pan_stage1", "aadhaar_stage1")

    // Single-field crop prompts: plain-text answer only (no JSON, no schema), so these
    // skip both grammar-constrained decoding and the shared system prompt (which is
    // written for the multi-field JSON contract) exactly like stage1 does, for the same
    // mechanical reason - different underlying purpose (stage1 measures parse-failure
    // rate; this is a genuinely different, lighter-weight extraction contract) but the
    // same skip-list shape, so it reuses runExtraction()'s existing branches rather than
    // adding a third parallel path.
    private val singleFieldCropDocTypes = setOf("pan_crop")

    // pan_full keeps grammar (its schema already structurally forces JSON-only output,
    // blocks extra fields via additionalProperties:false, and blocks non-Latin script via
    // the name/parent_name regex patterns) but drops the shared system.txt, whose other
    // rules duplicate what the schema already guarantees for this doc type specifically.
    // The one rule grammar can't enforce - don't guess, use null when unreadable - is
    // folded directly into user_pan_full.txt instead. Verified on-device 2026-09-24: no
    // system prompt, lean user prompt, still 5/5 fields exact match on pan_sample.png.
    // Doesn't touch system.txt itself, so every other doc type keeps its current,
    // separately-hardened behavior unchanged.
    private val skipSystemPromptOnlyDocTypes = setOf("pan_full")

    private val systemPrompt: String by lazy { loadAsset("prompts/system.txt") }

    private fun loadAsset(path: String): String =
        assets.open(path).bufferedReader().use { it.readText() }

    private fun promptFor(docType: String): String = loadAsset("prompts/user_$docType.txt")
    private fun schemaFor(docType: String): String = loadAsset("schemas/$docType.json")

    // Values that appeared in earlier prompts and were echoed back as if read from the
    // card. Any of these in an output means the model copied the prompt, not the image.
    // (None of the current prompt files contain literal ID/name examples any more -
    // this list is kept as a permanent guard against the failure mode returning.)
    private val knownPromptLeaks = listOf(
        "RAJESH KUMAR SHARMA", "MOHAN LAL SHARMA", "AFZPK7190K", "12/03/1978",
        "ABCPK1234F", "ABCDE1234F", "482910374652", "M1234567", "DL0201234567",
        "full name in English", "FULL NAME IN ENGLISH", "English or null",
        "document number", "FATHER OR MOTHER NAME",
        // The model copied this literal anchor text as a value instead of reading the
        // parent's name printed beneath it (found 2026-09-16 on a live card). Kept here
        // as a permanent guard even though the prompt no longer quotes these labels.
        "Father's Name", "Fathers Name", "Mother's Name", "Mothers Name",
        "Permanent Account Number", "INCOME TAX DEPARTMENT",
        // Example Aadhaar number that was in an earlier draft of user_aadhaar.txt.
        "8416 1590 3267", "841615903267",
        // Quoted field-locating labels and example values from an earlier draft of
        // user_passport.txt (found by audit workflow, 2026-09-16, before any live
        // passport test - passport remains otherwise unverified against a real sample).
        "Name of Father", "Legal Guardian", "Surname", "Given Name(s)", "Nationality",
        "Date of Birth", "Date of Issue", "Date of Expiry", "Place of Issue",
        "Place of Birth", "Sex", "15-JUN-1990", "New Delhi", "Mumbai", "INDIAN",
        // Example vehicle-class codes from an earlier draft of user_dl.txt.
        "LMV, MCWG", "LMV, MCWG, HMV",
        // Confirmed live 2026-09-16: a real DL's own vehicle-class TABLE HEADER text,
        // copied into vehicle_classes instead of the data rows beneath it (MCWG/LMV).
        // Unlike other leaks this one came from the document image itself, not the
        // prompt - the prompt never mentions this phrase.
        "Class of Vehicle"
    )

    // Phrase FRAGMENTS: flagged if they appear anywhere INSIDE a value, even glued to
    // other text - unlike knownPromptLeaks above, which only fires on an exact whole-
    // value match. See findLeaks() below for why both kinds of check are needed.
    private val knownPromptLeakFragments = listOf(
        // Confirmed live 2026-09-16 on a tt_id back-side extraction: the prompt's own
        // description of skin_colour ("colour of skin") leaked into a DIFFERENT
        // field, social_assistance_number, glued to the real value ("COLOUR OF SKIN
        // BROWN"). The prompt was reworded to remove this phrase entirely; kept here
        // as a permanent guard in case it resurfaces in some other combination.
        "colour of skin", "colour of eyes"
    )

    private val pickFileLauncher =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
            if (uri != null) handlePickedFile(uri, isBack = false)
        }

    // Only used for twoSidedDocTypes (currently just tt_id) - see docTypes above.
    private val pickFileLauncherBack =
        registerForActivityResult(ActivityResultContracts.OpenDocument()) { uri: Uri? ->
            if (uri != null) handlePickedFile(uri, isBack = true)
        }

    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        binding = ActivityMainBinding.inflate(layoutInflater)
        setContentView(binding.root)

        clkTck = try {
            Os.sysconf(OsConstants._SC_CLK_TCK)
        } catch (e: Exception) {
            100L
        }

        populateModelSpinners()
        binding.spinnerDocType.adapter =
            ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, docTypes)
        binding.spinnerDocType.onItemSelectedListener =
            object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(
                    parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long
                ) = updateForDocType()
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
            }
        refreshDeviceInfo()
        setupThreadSlider()

        binding.btnPick.setOnClickListener {
            pickFileLauncher.launch(arrayOf("image/*", "application/pdf"))
        }
        binding.btnPickBack.setOnClickListener {
            pickFileLauncherBack.launch(arrayOf("image/*", "application/pdf"))
        }

        binding.btnRun.setOnClickListener { runExtraction() }
        binding.btnStop.setOnClickListener { stopExtraction() }
    }

    // Shows/hides the back-side picker depending on whether the selected type needs
    // two images, and relabels the front picker so it's clear which side it's for.
    // Only tt_id is in twoSidedDocTypes right now - every other type leaves this
    // whole block a no-op and behaves exactly as before.
    private fun updateForDocType() {
        val docType = binding.spinnerDocType.selectedItem as? String ?: return
        val twoSided = twoSidedDocTypes.contains(docType)
        binding.btnPick.text = if (twoSided) "Pick front-side image" else "Pick image or PDF"
        binding.btnPickBack.visibility = if (twoSided) View.VISIBLE else View.GONE
        if (!twoSided) {
            binding.imgPreviewBack.visibility = View.GONE
            binding.tvFileNameBack.text = ""
        }
        updateRunButtonEnabled()
    }

    private fun updateRunButtonEnabled() {
        val docType = binding.spinnerDocType.selectedItem as? String
        val needsBack = docType != null && twoSidedDocTypes.contains(docType)
        binding.btnRun.isEnabled = pickedImageFile != null && (!needsBack || pickedBackImageFile != null)
    }

    // --- Kill switch ------------------------------------------------------
    // Sends SIGKILL to the child process directly (it was exec'd, not run through a
    // shell, so this is the actual llama-mtmd-cli process - no orphaned grandchild to
    // chase). llama.cpp cannot trap SIGKILL, so this stops CPU/RAM usage immediately.
    // In server mode there's no per-run process to kill (the server stays resident on
    // purpose) - Stop instead aborts the in-flight HTTP connection, same user-facing
    // effect (the read unblocks with an exception instead of a truncated stream).

    private fun stopExtraction() {
        if (currentProcess == null && currentConnection == null) return
        userStopped = true
        currentProcess?.destroyForcibly()
        currentConnection?.disconnect()
        binding.tvOutput.text = "Stopping..."
        binding.btnStop.isEnabled = false
    }

    // --- CPU thread count control ------------------------------------

    private fun setupThreadSlider() {
        val maxCores = Runtime.getRuntime().availableProcessors()
        val defaultThreads = maxOf(2, maxCores / 2)

        binding.seekThreads.max = maxCores - 1
        binding.seekThreads.progress = defaultThreads - 1
        binding.tvThreads.text = "CPU threads: $defaultThreads / $maxCores"

        binding.seekThreads.setOnSeekBarChangeListener(object : android.widget.SeekBar.OnSeekBarChangeListener {
            override fun onProgressChanged(seekBar: android.widget.SeekBar?, progress: Int, fromUser: Boolean) {
                binding.tvThreads.text = "CPU threads: ${progress + 1} / $maxCores"
            }
            override fun onStartTrackingTouch(seekBar: android.widget.SeekBar?) {}
            override fun onStopTrackingTouch(seekBar: android.widget.SeekBar?) {}
        })
    }

    override fun onResume() {
        super.onResume()
        refreshDeviceInfo()
    }

    // Server mode's whole point is staying resident ACROSS runs, so it must not be torn
    // down on every onPause/onStop (e.g. a brief backgrounding or screen rotation) -
    // only when the activity is actually going away for good.
    override fun onDestroy() {
        super.onDestroy()
        stopServer()
    }

    // --- Device / storage overview (shown from launch, not just during a run) ---

    private fun refreshDeviceInfo() {
        val statFs = StatFs(getExternalFilesDir(null)?.path ?: filesDir.path)
        val freeBytes = statFs.availableBytes
        val totalBytes = statFs.totalBytes
        binding.tvStorage.text =
            "Storage: %s free / %s total".format(formatBytes(freeBytes), formatBytes(totalBytes))

        val modelsBytes = modelsDir().listFiles()?.sumOf { it.length() } ?: 0L
        binding.tvModelsSize.text = "Models folder: %s".format(formatBytes(modelsBytes))

        val am = getSystemService(Context.ACTIVITY_SERVICE) as ActivityManager
        val memInfo = ActivityManager.MemoryInfo()
        am.getMemoryInfo(memInfo)
        binding.tvRamIdle.text =
            "RAM (device): %s available / %s total".format(formatBytes(memInfo.availMem), formatBytes(memInfo.totalMem))
    }

    private fun formatBytes(bytes: Long): String {
        val gb = bytes / 1e9
        return if (gb >= 1.0) "%.2f GB".format(gb) else "%.0f MB".format(bytes / 1e6)
    }

    // --- Model discovery -----------------------------------------------

    private fun modelsDir(): File {
        val dir = File(getExternalFilesDir(null), "models")
        if (!dir.exists()) dir.mkdirs()
        return dir
    }

    // --- Run log -----------------------------------------------------
    // Every completed run (not stopped/errored - there's no response to save in that
    // case) gets appended here as a plain text block: run number, doc type,
    // timestamp, then the exact JSON response(s) and validation report shown on
    // screen. The run counter persists across app restarts via SharedPreferences, so
    // "Run 1, Run 2, ..." stays meaningful across a whole day's testing, not just one
    // app session.

    private fun nextRunNumber(): Int {
        val prefs = getSharedPreferences("vlm_tester", Context.MODE_PRIVATE)
        val next = prefs.getInt("run_counter", 0) + 1
        prefs.edit().putInt("run_counter", next).apply()
        return next
    }

    private fun runLogFile(): File = File(getExternalFilesDir(null), "run_log.txt")

    private fun saveRun(runNumber: Int, docType: String, body: String) {
        val timestamp = java.text.SimpleDateFormat("yyyy-MM-dd HH:mm:ss", java.util.Locale.US)
            .format(java.util.Date())
        runLogFile().appendText("=== Run $runNumber | $docType | $timestamp ===\n$body\n\n")
    }

    private fun populateModelSpinners() {
        val ggufFiles = modelsDir().listFiles { f -> f.name.endsWith(".gguf") }?.toList().orEmpty()
        val lmModels = ggufFiles.filter { !it.name.startsWith("mmproj-") }.map { it.name }.sorted()
        val mmprojModels = ggufFiles.filter { it.name.startsWith("mmproj-") }.map { it.name }.sorted()

        if (lmModels.isEmpty() || mmprojModels.isEmpty()) {
            binding.tvOutput.text =
                "No model files found in:\n${modelsDir().absolutePath}\n\n" +
                "Push a language-model .gguf and a mmproj-*.gguf there first, e.g.:\n" +
                "adb push visionpsy-nano-460m-flash-q5_k_m.gguf ${modelsDir().absolutePath}/\n" +
                "adb push mmproj-visionpsy-nano-460m-flash-q8.gguf ${modelsDir().absolutePath}/"
        }

        binding.spinnerModel.adapter =
            ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, lmModels)
        binding.spinnerMmproj.adapter =
            ArrayAdapter(this, android.R.layout.simple_spinner_dropdown_item, mmprojModels)
    }

    // --- File picking (image or PDF -> a JPEG the model can read) ------

    private fun handlePickedFile(uri: Uri, isBack: Boolean) {
        val mime = contentResolver.getType(uri) ?: ""
        val outFile = File(cacheDir, if (isBack) "vlm_input_back.jpg" else "vlm_input.jpg")

        try {
            val bitmap: Bitmap = if (mime == "application/pdf") {
                renderFirstPdfPage(uri)
            } else {
                contentResolver.openInputStream(uri).use { input ->
                    BitmapFactory.decodeStream(input)
                } ?: throw IllegalStateException("Could not decode image")
            }

            FileOutputStream(outFile).use { out ->
                bitmap.compress(Bitmap.CompressFormat.JPEG, 92, out)
            }

            val name = "Selected: ${queryDisplayName(uri) ?: uri.lastPathSegment}"
            if (isBack) {
                pickedBackImageFile = outFile
                binding.imgPreviewBack.setImageBitmap(bitmap)
                binding.imgPreviewBack.visibility = View.VISIBLE
                binding.tvFileNameBack.text = name
            } else {
                pickedImageFile = outFile
                binding.imgPreview.setImageBitmap(bitmap)
                binding.imgPreview.visibility = View.VISIBLE
                binding.tvFileName.text = name
            }
            updateRunButtonEnabled()
        } catch (e: Exception) {
            Toast.makeText(this, "Could not load file: ${e.message}", Toast.LENGTH_LONG).show()
        }
    }

    private fun renderFirstPdfPage(uri: Uri): Bitmap {
        val pfd: ParcelFileDescriptor =
            contentResolver.openFileDescriptor(uri, "r") ?: throw IllegalStateException("Cannot open PDF")
        PdfRenderer(pfd).use { renderer ->
            renderer.openPage(0).use { page ->
                // Render at 2x page size for sharper text before the vision encoder downsamples it.
                val bitmap = Bitmap.createBitmap(page.width * 2, page.height * 2, Bitmap.Config.ARGB_8888)
                bitmap.eraseColor(android.graphics.Color.WHITE)
                page.render(bitmap, null, null, PdfRenderer.Page.RENDER_MODE_FOR_DISPLAY)
                return bitmap
            }
        }
    }

    private fun queryDisplayName(uri: Uri): String? {
        return contentResolver.query(uri, null, null, null, null)?.use { cursor ->
            val idx = cursor.getColumnIndex(android.provider.OpenableColumns.DISPLAY_NAME)
            if (idx >= 0 && cursor.moveToFirst()) cursor.getString(idx) else null
        }
    }

    // --- Running the model as a subprocess ------------------------------

    // PaddleOCR-VL's chat template is a custom, non-ChatML format baked into the LM
    // GGUF's own tokenizer.chat_template - it only gets applied with --jinja passed at
    // startup (confirmed via the server's own logged example_format). VisionPsy/stage1
    // never needed this (they work through the CLI's built-in default formatting), so
    // this stays keyed off the model filename rather than turned on globally, to avoid
    // any risk of changing already-validated VisionPsy behaviour.
    private fun isPaddleOcrModel(modelPath: String): Boolean =
        File(modelPath).name.contains("paddleocr", ignoreCase = true)

    // PaddleOCR-VL ONLY: its hparams are read straight from GGUF metadata
    // (get_u32(KEY_IMAGE_MIN/MAX_PIXELS)) rather than through set_limit_image_tokens(),
    // so it never sees --image-min-tokens/--image-max-tokens (see applyImageTokenCapEnv
    // below) - this custom env var pair is the only lever that reaches it. Confirmed
    // no-op for VisionPsy (different preprocessor, idefics3, never reads these).
    //
    // Full-image cap chosen at ~1024 image tokens (1,048,576 px) - PaddleOCR-VL's own
    // stock ceiling, so this only trims the resolution headroom above what it already
    // defaults to for a full photo, not anything it needs.
    //
    // Crop threshold set well above real single-field crop sizes (~20K px measured) and
    // well below full document photos (~10M+ px measured).
    private fun applyDynSizeEnv(pb: ProcessBuilder) {
        pb.environment()["MTMD_CROP_THRESHOLD_PIXELS"] = "200000"
        pb.environment()["MTMD_FULLIMAGE_MAX_PIXELS"] = "1048576"
    }

    // Qwen2VL/2.5VL/3VL (and any other model going through set_limit_image_tokens): use
    // llama.cpp's own upstream --image-max-tokens equivalent instead of a custom patch -
    // it's baked in at model-load time via custom_image_max_tokens, before any per-request
    // preprocessing runs, so there's no ordering/no-op risk the way a runtime override has.
    // "only used by vision models with dynamic resolution" per its own --help text, so
    // it's a confirmed no-op for VisionPsy (fixed-tile idefics3) and PaddleOCR-VL (reads
    // GGUF metadata directly, never touches custom_image_max_tokens at all).
    //
    // Deliberately NOT setting a min-tokens floor: forcing crops up to a token count they
    // don't natively have measured worse on both axes (accuracy AND latency) than leaving
    // Qwen's own stock 8-token floor alone - a real single-field crop from this phone's
    // camera resolution naturally sits around 70-95 tokens already, well above that floor,
    // so nothing needs to be forced up.
    //
    // 256 is a starting point (validated elsewhere to preserve accuracy on a full card
    // while being faster than the stock ~4096-token ceiling) - worth confirming on this
    // phone's own hardware rather than assumed to transfer as-is.
    private fun applyImageTokenCapEnv(pb: ProcessBuilder, tokenCap: Int) {
        pb.environment()["LLAMA_ARG_IMAGE_MAX_TOKENS"] = tokenCap.toString()
    }

    /** Crop doc types: 256 tokens is verified — tested warm (0.87s) and cold (21.2s),
     * exact match on samad_crop_clean.jpg. At 1024 it degrades (per Qwen's upstream crop
     * test: 4/6 correct vs 6/6 at 256, and 6.72s vs 1.53s) because upsampling a tiny crop
     * to fill the token budget adds only interpolated pixels, not real detail.
     *
     * pan_full: 128 tokens, verified on-device 2026-09-24 against pan_sample.png
     * (2127x1282, 10.8MP) with the lean prompt (see user_pan_full.txt) plus CPU core
     * pinning and forced flash-attention (see runOnePass()/ensureServerRunning()) - cold,
     * uncached, single request: 33.55s, all 5 fields exact match including dob, the field
     * most likely to break first. A full 128-2046 sweep on the same image showed latency
     * climbing monotonically (256->48.3s, 512->87.1s, 1024->169.9s) with every level
     * through 1024 staying 5/5 correct - so 128 was not a cliff-edge pick, just the
     * fastest point on a curve where accuracy hadn't degraded yet on this image. This
     * contradicts the original theory below (that 1024 was Qwen's grounding floor) on
     * this specific document; caveat is this is still ONE card - not yet confirmed across
     * multiple different cards, which is why it hasn't been cross-checked against the
     * other full-card doc types (aadhaar/passport/dl/tt_*) below, all of which stay at
     * the older, more conservative 1024 until they get the same on-device validation.
     *
     * Other full images: 1024 is an informed starting point, NOT yet validated on-device.
     * Qwen-VL's own load-time warning says it "requires at minimum 1024 image tokens to
     * function correctly on grounding tasks" — that floor is for localizing fields on a
     * full card, not OCR-ing a tight crop. This value needs on-device accuracy + latency
     * testing before it's confirmed optimal; it may be higher or lower than the true
     * sweet spot, same caveat pan_full carried before today's test. */
    /** pan_full only: manual switch (switchPanFullHighCap in the UI, default ON) instead
     *  of an earlier per-image blur-detection approach (Laplacian-variance sharpness
     *  scoring) tried and abandoned the same day. On-device testing 2026-09-24 found 256
     *  fixes every real misread seen at 128 on CLEAN cards too, not just blurred ones
     *  (SOHRAAB DANISH: "SOHRAB"/"AZFAL" -> correct; SAROJINI M: a wrong PAN digit ->
     *  correct) - so 128 was marginal for dense print regardless of blur, which is why
     *  blur-detection was replaced with this simpler manual toggle instead of kept as an
     *  "auto" option. Off drops back to 128 for when speed matters more than the ~10-15s
     *  accuracy premium. Every other doc type keeps its fixed cap unchanged. */
    private fun imageTokenCapFor(docType: String): Int = when {
        singleFieldCropDocTypes.contains(docType) -> 256
        docType == "pan_full" -> if (binding.switchPanFullHighCap.isChecked) 256 else 128
        else -> 1024
    }

    /** Runs one model pass (one image, one prompt+schema) and returns (rawOutput,
     *  elapsedSec), or null if the user hit Stop mid-pass. Shared by the single-image
     *  flow and the two-sided (front+back) flow below - same subprocess/kill-switch
     *  handling either way, just parameterized by which image/prompt/schema to use. */
    /** rawOutput is null when the user hit Stop mid-pass - elapsedSec is always valid
     *  either way, so a stop message can still report how long it ran for. */
    private data class PassResult(val rawOutput: String?, val elapsedSec: Double)

    private fun runOnePass(
        execPath: File, modelPath: String, mmprojPath: String, imageFile: File,
        promptText: String, schemaJson: String, schemaFileName: String,
        useGpu: Boolean, threads: Int,
        // The stage1 fine-tune (see stage1EvalDocTypes) was trained against one exact,
        // self-contained instruction block that already embeds its own schema as prose -
        // it has no notion of our app's separate system-prompt file, and grammar-
        // constrained decoding (-jf) would force valid/complete JSON regardless of what
        // the model actually produces, which defeats the whole point of measuring its
        // real parse-failure/dropped-key rate. These three params default to the
        // existing behaviour exactly, so every other call site is untouched.
        useGrammar: Boolean = true, useSystemPrompt: Boolean = true, maxTokens: Int = 512,
        tokenCap: Int = 256
    ): PassResult {
        val isPaddleOcr = isPaddleOcrModel(modelPath)
        val args = mutableListOf(
            execPath.absolutePath,
            "-m", modelPath,
            "--mmproj", mmprojPath
        )
        if (isPaddleOcr) args += "--jinja"
        if (useGrammar) {
            val schemaFile = File(cacheDir, schemaFileName)
            schemaFile.writeText(schemaJson)
            args += listOf("-jf", schemaFile.absolutePath)
        }
        args += listOf("--image", imageFile.absolutePath)
        // PaddleOCR-VL's --jinja chat template mis-renders the CLI's separate system
        // message: verbose logging (-v) showed the <__media__> image marker coming out
        // truncated mid-string ("<__media__>" -> "dia__>") whenever a system message
        // precedes the user+image one, which makes mtmd_tokenize see 0 markers against
        // the 1 loaded image and fail outright. Folding system+user into one message
        // sidesteps it - confirmed on-device: same crop/prompt now extracts correctly
        // with no error. VisionPsy doesn't use --jinja at all, so it's unaffected and
        // keeps using -sys as its own message exactly as before.
        val effectivePrompt =
            if (isPaddleOcr && useSystemPrompt) "$systemPrompt\n\n$promptText" else promptText
        if (useSystemPrompt && !isPaddleOcr) args += listOf("-sys", systemPrompt)
        args += listOf(
            "-p", effectivePrompt,
            "-n", maxTokens.toString(),
            "--temp", "0",
            // llama.cpp defaults this to 1.0 (disabled) - confirmed in common/common.h.
            // Applies regardless of temp=0/greedy decoding: it discourages exact-text
            // repetition loops, a distinct failure mode from sampling randomness.
            "--repeat-penalty", "1.1",
            "-t", threads.toString(),
            "-tb", threads.toString(),
            // Exynos 1380-specific: cores 4-7 are the 4 fast A78 cores (0-3 are the
            // slow A55s) - confirmed via /proc/cpuinfo CPU part IDs (0xd41 vs 0xd05) on
            // two separate M35 5G units. Without this, -t/-tb only sets a thread COUNT;
            // the scheduler can still migrate those threads onto the slow cores, which
            // is the most likely explanation for the 8-16 tok/s prefill-rate swings seen
            // across otherwise-identical requests all session. Revisit if ever deployed
            // to a different chipset - this range is not auto-detected.
            "-Cr", "4-7", "-Crb", "4-7", "--cpu-strict", "1", "--cpu-strict-batch", "1",
            // 'auto' (the default) was leaving this off in practice; forcing it on
            // measurably helped prefill throughput in on-device testing 2026-09-24.
            "-fa", "on"
        )
        if (useGpu) {
            // Offload the LM to GPU; vision encoder (mmproj) stays on CPU regardless.
            args += listOf("-ngl", "99", "--no-mmproj-offload")
        } else {
            // -ngl 0 only keeps weights off the GPU; op_offload would still reroute
            // large prefill matmuls to Vulkan, so disable that explicitly too.
            args += listOf("-ngl", "0", "--no-mmproj-offload", "--no-op-offload")
        }
        val pb = ProcessBuilder(args)
        pb.environment()["LD_LIBRARY_PATH"] = applicationInfo.nativeLibraryDir
        // PaddleOCR-VL only: applyDynSizeEnv's MTMD_FULLIMAGE_MAX_PIXELS overwrites (not
        // min()s) mtmd_image_preprocessor_dyn_size's pixel ceiling for any "full image"
        // input (mtmd-image.cpp ~line 923), which silently discards whatever
        // applyImageTokenCapEnv just set for Qwen - confirmed on-device 2026-09-24:
        // pan_full produced byte-identical prompt token counts (1398) at cap 1024, 512,
        // and 256 until this was gated. Qwen's own token cap is the only lever that
        // should apply to it; VisionPsy's idefics3 preprocessor never reads either var,
        // so this gate changes nothing for it.
        if (isPaddleOcr) applyDynSizeEnv(pb)
        applyImageTokenCapEnv(pb, tokenCap)
        pb.redirectErrorStream(true)

        val startNanos = System.nanoTime()
        val process = pb.start()
        currentProcess = process
        val pid = getPid(process) ?: waitForPid("libllama-mtmd-cli.so")

        if (pid == null) {
            ui.post {
                binding.tvRam.text = "RAM: unavailable (couldn't resolve child PID)"
                binding.tvCpu.text = "CPU: unavailable"
                binding.tvGpu.text = "GPU: unavailable"
            }
        }
        val monitor = if (pid != null) startMonitoring(pid, startNanos) else MonitorHandle()

        val rawOutput = process.inputStream.bufferedReader().readText()
        process.waitFor()
        monitor.stop = true
        currentProcess = null

        val elapsedSec = (System.nanoTime() - startNanos) / 1_000_000_000.0
        // A kill unblocks readText() with a truncated stream rather than an exception,
        // so this must be checked before trusting rawOutput at all - otherwise a
        // killed run could render partial/garbage JSON as if valid.
        return PassResult(if (userStopped) null else rawOutput, elapsedSec)
    }

    // --- Persistent server mode (experimental) --------------------------------------
    // runOnePass() above spawns a fresh llama-mtmd-cli process per extraction, paying a
    // full model reload (~90-102s of the original ~99-152s total, see
    // LATENCY_OPTIMIZATION.md section 2 Stage 0/1) every single time. This alternative
    // path starts llama-server ONCE and reuses it across runs over loopback HTTP -
    // combined with MTMD_MAX_LONGEST_EDGE=768 (the vision-tile fix from the same doc,
    // Stage 3) this is what actually gets a single field extraction down to ~14-16s.
    // Neither piece alone gets there: the tile fix without server mode still pays the
    // reload; server mode without the tile fix still pays the un-tiled vision-encode
    // cost. Gated behind switchServerMode so the existing, fully-validated CLI path
    // above is completely unaffected when it's off.

    private class ServerState(
        val process: Process, val pid: Long?, val port: Int,
        val modelPath: String, val mmprojPath: String, val threads: Int,
        val useGpu: Boolean, val imageTokenCap: Int
    )

    @Volatile private var serverState: ServerState? = null
    private val serverLock = Object()

    private fun findFreePort(): Int = java.net.ServerSocket(0).use { it.localPort }

    /** Starts llama-server if it isn't already running with this exact model/mmproj/
     *  threads/GPU combination - those became server-startup-time settings under this
     *  mode instead of per-request flags, so a change to any of them means the old
     *  process must be torn down and a new one started before the next request. Blocks
     *  the calling (already-background) thread until the server answers /health or
     *  startup fails. Returns the port to talk to. */
    private fun ensureServerRunning(
        execPath: File, modelPath: String, mmprojPath: String, threads: Int,
        useGpu: Boolean, imageTokenCap: Int
    ): Int {
        synchronized(serverLock) {
            val existing = serverState
            if (existing != null && existing.process.isAlive &&
                existing.modelPath == modelPath && existing.mmprojPath == mmprojPath &&
                existing.threads == threads && existing.useGpu == useGpu &&
                existing.imageTokenCap == imageTokenCap) {
                return existing.port
            }
            existing?.process?.destroyForcibly()
            serverState = null

            val port = findFreePort()
            val args = mutableListOf(
                execPath.absolutePath,
                "-m", modelPath, "--mmproj", mmprojPath,
                "--port", port.toString(), "--host", "127.0.0.1",
                "-t", threads.toString(), "-tb", threads.toString(),
                // See the matching comment in runOnePass() - same Exynos 1380-specific
                // core pinning and forced flash-attention, validated the same way.
                "-Cr", "4-7", "-Crb", "4-7", "--cpu-strict", "1", "--cpu-strict-batch", "1",
                "-fa", "on"
            )
            args += if (useGpu) listOf("-ngl", "99", "--no-mmproj-offload")
                    else listOf("-ngl", "0", "--no-mmproj-offload", "--no-op-offload")
            val isPaddleOcr = isPaddleOcrModel(modelPath)
            if (isPaddleOcr) args += "--jinja"

            val pb = ProcessBuilder(args)
            pb.environment()["LD_LIBRARY_PATH"] = applicationInfo.nativeLibraryDir
            // PaddleOCR-VL only - see the matching guard in runOnePass() for why this
            // can't also run for Qwen.
            if (isPaddleOcr) applyDynSizeEnv(pb)
            applyImageTokenCapEnv(pb, imageTokenCap)
            if (!isPaddleOcr) {
                // The actual fix under test - caps the vision-preprocessing upscale
                // target at 768px instead of the model's hard-coded 2048px default. See
                // LATENCY_OPTIMIZATION.md section 2, Stage 3, for why this is what lets
                // a small crop get proportionally fewer tiles instead of always being
                // upscaled to the same canvas size first. Only meaningful to VisionPsy's
                // idefics3 preprocessor - PaddleOCR-VL uses a different, area-based one
                // (mtmd_image_preprocessor_dyn_size) that doesn't read this var at all.
                pb.environment()["MTMD_MAX_LONGEST_EDGE"] = "768"
            }
            pb.redirectErrorStream(true)

            val process = pb.start()
            // Drain the server's own log output on its own thread so it never blocks on
            // a full stdout pipe - readiness is polled via /health below, not by
            // parsing log text (simpler, and doesn't break if the log format changes).
            // Forwarded to logcat (not discarded) so a failed startup is diagnosable -
            // this is the only place the server's own stderr/stdout ends up anywhere.
            Thread {
                try {
                    process.inputStream.bufferedReader().forEachLine { line ->
                        android.util.Log.d("LlamaServer", line)
                    }
                } catch (e: Exception) { }
            }.start()

            val pid = getPid(process) ?: waitForPid("libllama-server.so")

            // Kept even after the cleartext-traffic fix (see AndroidManifest.xml) -
            // this is what actually surfaced that bug in the first place, since the
            // previous version silently swallowed every attempt's exception and just
            // reported a bare timeout. Worth keeping so any FUTURE readiness failure is
            // diagnosable from the error text alone, without another logcat round-trip.
            var lastError: String? = null
            val ready = (1..600).any {
                Thread.sleep(50)
                if (!process.isAlive) {
                    lastError = "process exited unexpectedly"
                    return@any false
                }
                try {
                    val conn = java.net.URL("http://127.0.0.1:$port/health")
                        .openConnection() as java.net.HttpURLConnection
                    conn.connectTimeout = 200
                    conn.readTimeout = 200
                    val code = conn.responseCode
                    conn.disconnect()
                    code == 200
                } catch (e: Exception) {
                    lastError = "${e.javaClass.simpleName}: ${e.message}"
                    false
                }
            }
            if (!ready) {
                process.destroyForcibly()
                throw IllegalStateException(
                    "llama-server did not become ready on port $port" +
                    (lastError?.let { " (last health-check error: $it)" } ?: " (no response attempted - process may have exited immediately)")
                )
            }

            val state = ServerState(process, pid, port, modelPath, mmprojPath, threads, useGpu, imageTokenCap)
            serverState = state
            return port
        }
    }

    private fun stopServer() {
        synchronized(serverLock) {
            serverState?.process?.destroyForcibly()
            serverState = null
        }
    }

    /** Same shape/contract as runOnePass() (PassResult, kill-switch semantics) but talks
     *  to the persistent server over HTTP instead of spawning+reading a one-shot
     *  process. schemaJson, when non-blank, is sent as the request's json_schema field -
     *  confirmed (tools/server/server-common.cpp) to drive the same grammar-constrained
     *  decoding the CLI's -jf flag uses, so structured extraction behaves identically
     *  either way. */
    private fun runOnePassServer(
        execPath: File, modelPath: String, mmprojPath: String, imageFile: File,
        promptText: String, schemaJson: String, useGpu: Boolean, threads: Int,
        useGrammar: Boolean = true, useSystemPrompt: Boolean = true, maxTokens: Int = 512,
        tokenCap: Int = 256
    ): PassResult {
        val port = ensureServerRunning(execPath, modelPath, mmprojPath, threads, useGpu, tokenCap)
        val pid = serverState?.pid
        val startNanos = System.nanoTime()
        val monitor = if (pid != null) startMonitoring(pid, startNanos) else MonitorHandle()

        val messages = org.json.JSONArray()
        if (useSystemPrompt) {
            messages.put(org.json.JSONObject().apply {
                put("role", "system")
                put("content", systemPrompt)
            })
        }
        val b64 = android.util.Base64.encodeToString(imageFile.readBytes(), android.util.Base64.NO_WRAP)
        val contentArr = org.json.JSONArray()
            .put(org.json.JSONObject().apply { put("type", "text"); put("text", promptText) })
            .put(org.json.JSONObject().apply {
                put("type", "image_url")
                put("image_url", org.json.JSONObject().put("url", "data:image/jpeg;base64,$b64"))
            })
        messages.put(org.json.JSONObject().apply { put("role", "user"); put("content", contentArr) })

        val payload = org.json.JSONObject().apply {
            put("messages", messages)
            put("temperature", 0)
            // Matches the CLI path's --repeat-penalty 1.1 (llama.cpp defaults this to
            // 1.0/disabled - confirmed in common/common.h - so it's genuinely off unless
            // set here explicitly).
            put("repeat_penalty", 1.1)
            put("max_tokens", maxTokens)
            if (useGrammar && schemaJson.isNotBlank()) {
                put("json_schema", org.json.JSONObject(schemaJson))
            }
        }

        var rawOutput: String? = null
        try {
            val conn = java.net.URL("http://127.0.0.1:$port/v1/chat/completions")
                .openConnection() as java.net.HttpURLConnection
            conn.requestMethod = "POST"
            conn.doOutput = true
            conn.connectTimeout = 10_000
            conn.readTimeout = 300_000
            conn.setRequestProperty("Content-Type", "application/json")
            currentConnection = conn
            conn.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }

            val code = conn.responseCode
            val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
                .bufferedReader().use { it.readText() }
            if (code !in 200..299) throw IllegalStateException("Server returned HTTP $code: $body")

            rawOutput = org.json.JSONObject(body)
                .getJSONArray("choices").getJSONObject(0)
                .getJSONObject("message").getString("content")
        } catch (e: Exception) {
            // A Stop mid-request unblocks the read via disconnect(), which surfaces as
            // an IOException here - identical in spirit to how a killed CLI process
            // unblocks readText() with a truncated stream. Anything else is a real
            // error and must propagate so runExtraction()'s catch block can show it.
            if (!userStopped) throw e
        } finally {
            currentConnection = null
        }

        monitor.stop = true
        val elapsedSec = (System.nanoTime() - startNanos) / 1_000_000_000.0
        return PassResult(if (userStopped) null else rawOutput, elapsedSec)
    }

    private fun runExtraction() {
        val imageFile = pickedImageFile ?: return
        val modelName = binding.spinnerModel.selectedItem as? String ?: return
        val mmprojName = binding.spinnerMmproj.selectedItem as? String ?: return
        val docType = binding.spinnerDocType.selectedItem as? String ?: return

        val useServerMode = binding.switchServerMode.isChecked
        val execPath = File(applicationInfo.nativeLibraryDir,
            if (useServerMode) "libllama-server.so" else "libllama-mtmd-cli.so")
        if (!execPath.exists()) {
            binding.tvOutput.text = "Missing native binary at:\n${execPath.absolutePath}"
            return
        }
        val useGpu = binding.switchGpu.isChecked
        val threads = binding.seekThreads.progress + 1

        // twoSidedDocTypes currently only contains "tt_id" - every other type takes
        // the exact same single-pass path this always has.
        val twoSided = twoSidedDocTypes.contains(docType)
        val backImageFile = pickedBackImageFile
        if (twoSided && backImageFile == null) return

        userStopped = false
        binding.btnRun.isEnabled = false
        binding.btnStop.isEnabled = true
        binding.progressBar.visibility = View.VISIBLE
        binding.statsCard.visibility = View.VISIBLE
        binding.tvOutput.text = "Running..."

        Thread {
            try {
                val modelPath = File(modelsDir(), modelName).absolutePath
                val mmprojPath = File(modelsDir(), mmprojName).absolutePath
                val mode = "$docType | " +
                    (if (useGpu) "GPU (LM on Vulkan, mmproj on CPU)" else "CPU only") +
                    (if (useServerMode) " | server mode" else "")

                // Unifies the two invocation paths behind one call shape so the rest of
                // this function (single-image and two-sided alike) doesn't need to
                // duplicate the branch at every call site. schemaFileName is only
                // meaningful to the CLI path (-jf needs an actual file); server mode
                // sends schemaJson directly in the request body and ignores it.
                fun runPass(
                    img: File, promptText: String, schemaJson: String, schemaFileName: String,
                    useGrammar: Boolean = true, useSystemPrompt: Boolean = true, maxTokens: Int = 512,
                    tokenCap: Int = 256
                ): PassResult = if (useServerMode) {
                    runOnePassServer(
                        execPath, modelPath, mmprojPath, img, promptText, schemaJson,
                        useGpu, threads, useGrammar, useSystemPrompt, maxTokens, tokenCap
                    )
                } else {
                    runOnePass(
                        execPath, modelPath, mmprojPath, img, promptText, schemaJson,
                        schemaFileName, useGpu, threads, useGrammar, useSystemPrompt, maxTokens, tokenCap
                    )
                }

                if (!twoSided) {
                    val isStage1 = stage1EvalDocTypes.contains(docType)
                    val isCrop = singleFieldCropDocTypes.contains(docType)
                    val skipJsonContract = isStage1 || isCrop
                    val skipSystemPromptOnly = skipSystemPromptOnlyDocTypes.contains(docType)
                    // Captured once so pan_full's cap (see imageTokenCapFor's manual
                    // switch branch) can also be shown in the run's own output below,
                    // instead of only being knowable by re-deriving it.
                    val cap = imageTokenCapFor(docType)
                    val outcome = runPass(
                        imageFile,
                        promptFor(docType), if (skipJsonContract) "" else schemaFor(docType),
                        "extract_schema.json",
                        useGrammar = !skipJsonContract,
                        useSystemPrompt = !skipJsonContract && !skipSystemPromptOnly,
                        maxTokens = if (isStage1) 1024 else if (isCrop) 64 else 512,
                        tokenCap = cap
                    )
                    if (outcome.rawOutput == null) {
                        ui.post {
                            binding.progressBar.visibility = View.GONE
                            binding.btnRun.isEnabled = true
                            binding.btnStop.isEnabled = false
                            binding.tvOutput.text = "Stopped by user after %.1fs.".format(outcome.elapsedSec)
                            refreshDeviceInfo()
                        }
                        return@Thread
                    }
                    val cleaned = extractAnswer(outcome.rawOutput)
                    val report = if (isCrop) validateCropField(docType, cleaned) else validateExtraction(docType, cleaned)
                    val runNumber = nextRunNumber()
                    val fullOutput =
                        "[$mode | cap=$cap | %.1fs]\n\n$cleaned\n\n$report".format(outcome.elapsedSec)
                    saveRun(runNumber, docType, fullOutput)
                    ui.post {
                        binding.progressBar.visibility = View.GONE
                        binding.btnRun.isEnabled = true
                        binding.btnStop.isEnabled = false
                        binding.tvOutput.text = "Run $runNumber\n\n$fullOutput"
                        refreshDeviceInfo()
                    }
                    return@Thread
                }

                // Two-sided: front pass, then back pass, then merge + validate both
                // together. Stop during either pass aborts the whole run - it never
                // silently continues to the other side on a truncated read.
                ui.post { binding.tvOutput.text = "Running front side (1/2)..." }
                val frontOutcome = runPass(
                    imageFile,
                    promptFor("${docType}_front"), schemaFor("${docType}_front"),
                    "extract_schema_front.json",
                    tokenCap = imageTokenCapFor(docType)
                )
                if (frontOutcome.rawOutput == null) {
                    ui.post {
                        binding.progressBar.visibility = View.GONE
                        binding.btnRun.isEnabled = true
                        binding.btnStop.isEnabled = false
                        binding.tvOutput.text =
                            "Stopped by user during front side after %.1fs.".format(frontOutcome.elapsedSec)
                        refreshDeviceInfo()
                    }
                    return@Thread
                }

                ui.post { binding.tvOutput.text = "Running back side (2/2)..." }
                val backOutcome = runPass(
                    backImageFile!!,
                    promptFor("${docType}_back"), schemaFor("${docType}_back"),
                    "extract_schema_back.json",
                    tokenCap = imageTokenCapFor(docType)
                )
                if (backOutcome.rawOutput == null) {
                    ui.post {
                        binding.progressBar.visibility = View.GONE
                        binding.btnRun.isEnabled = true
                        binding.btnStop.isEnabled = false
                        binding.tvOutput.text =
                            "Stopped by user during back side after %.1fs (front already done)."
                                .format(backOutcome.elapsedSec)
                        refreshDeviceInfo()
                    }
                    return@Thread
                }

                val frontCleaned = extractAnswer(frontOutcome.rawOutput)
                val backCleaned = extractAnswer(backOutcome.rawOutput)
                val report = validateTwoSidedExtraction(docType, frontCleaned, backCleaned)
                val totalElapsed = frontOutcome.elapsedSec + backOutcome.elapsedSec
                val runNumber = nextRunNumber()
                val fullOutput =
                    "[$mode | %.1fs total: front %.1fs + back %.1fs]\n\n"
                        .format(totalElapsed, frontOutcome.elapsedSec, backOutcome.elapsedSec) +
                    "FRONT:\n$frontCleaned\n\nBACK:\n$backCleaned\n\n$report"
                saveRun(runNumber, docType, fullOutput)
                ui.post {
                    binding.progressBar.visibility = View.GONE
                    binding.btnRun.isEnabled = true
                    binding.btnStop.isEnabled = false
                    binding.tvOutput.text = "Run $runNumber\n\n$fullOutput"
                    refreshDeviceInfo()
                }
            } catch (e: Exception) {
                currentProcess = null
                ui.post {
                    binding.progressBar.visibility = View.GONE
                    binding.btnRun.isEnabled = true
                    binding.btnStop.isEnabled = false
                    if (!userStopped) binding.tvOutput.text = "Error: ${e.message}"
                }
            }
        }.start()
    }

    // --- Derived confidence ---------------------------------------------
    // The model's own self-reported confidence proved anti-correlated with
    // correctness, so confidence is computed here from format checks instead.

    private fun str(obj: org.json.JSONObject, k: String): String? =
        if (!obj.has(k) || obj.isNull(k)) null else obj.optString(k, null)

    /** Converts a date the model copied verbatim off a document into ISO form.
     *  Handles DD/MM/YYYY, DD-MM-YYYY (numeric, hyphens - the usual Indian DL format,
     *  missed until a live DL test on 2026-09-16 falsely flagged 3 correct dates as
     *  "not recognised"), DD-MMM-YYYY (3-letter English month, either case), and
     *  YYYY-MM-DD (found live on a Trinidad & Tobago National ID, 2026-09-16 - all
     *  three of its dates were falsely flagged "not recognised" because this shape
     *  had never come up on an Indian document; T&T apparently prints dates in ISO
     *  order rather than DD/MM/YYYY). The model has never once performed date-format
     *  conversion itself in testing, so every prompt asks it to copy the date exactly
     *  as printed and this always runs afterwards instead - deterministic, and free.
     *  Returns null if the shape isn't recognised. */
    private fun toIso(raw: String?): String? {
        if (raw == null) return null
        Regex("^([12][0-9]{3})-([01][0-9])-([0-3][0-9])$").find(raw)?.let { m ->
            val (y, mo, d) = m.destructured
            return "$y-$mo-$d"
        }
        Regex("^([0-3][0-9])[/-]([01][0-9])[/-]([12][0-9]{3})$").find(raw)?.let { m ->
            val (d, mo, y) = m.destructured
            return "$y-$mo-$d"
        }
        val months = mapOf(
            "JAN" to "01", "FEB" to "02", "MAR" to "03", "APR" to "04",
            "MAY" to "05", "JUN" to "06", "JUL" to "07", "AUG" to "08",
            "SEP" to "09", "OCT" to "10", "NOV" to "11", "DEC" to "12"
        )
        Regex("^([0-3][0-9])-([A-Za-z]{3})-([12][0-9]{3})$").find(raw)?.let { m ->
            val (d, mon, y) = m.destructured
            val mo = months[mon.uppercase()] ?: return null
            return "$y-$mo-$d"
        }
        return null
    }

    // A hardcoded upper year bound goes stale the moment the calendar passes it - the
    // dob check's "2025" cap was already in the past by the time this was tested live
    // (found 2026-09-16), flagging every genuinely correct current-year extraction as
    // "implausible". Compute it from the device clock instead so it never goes stale.
    private fun currentYear(): Int = java.time.Year.now().value

    private fun checkDateShape(
        problems: MutableList<String>, notes: MutableList<String>,
        obj: org.json.JSONObject, key: String, minYear: Int, maxYear: Int
    ) {
        val raw = str(obj, key) ?: return
        val iso = toIso(raw)
        if (iso == null) { problems += "$key '$raw' is not a recognised date format"; return }
        val (y, mo, d) = iso.split("-").map { it.toInt() }
        if (d !in 1..31) problems += "$key day out of range"
        if (mo !in 1..12) problems += "$key month out of range"
        if (y !in minYear..maxYear) problems += "$key year $y implausible"
        if (problems.none { it.startsWith(key) }) notes += "$key -> $iso"
    }

    // Verhoeff checksum tables - the algorithm UIDAI uses for the 12th (check) digit of
    // every Aadhaar number. Detects essentially all single-digit misreads and adjacent
    // transpositions, so it is a much stronger signal than "is this 12 digits". Table
    // values verified against the standard test vector (base "236" -> check digit "3").
    private val verhoeffD = arrayOf(
        intArrayOf(0,1,2,3,4,5,6,7,8,9), intArrayOf(1,2,3,4,0,6,7,8,9,5),
        intArrayOf(2,3,4,0,1,7,8,9,5,6), intArrayOf(3,4,0,1,2,8,9,5,6,7),
        intArrayOf(4,0,1,2,3,9,5,6,7,8), intArrayOf(5,9,8,7,6,0,4,3,2,1),
        intArrayOf(6,5,9,8,7,1,0,4,3,2), intArrayOf(7,6,5,9,8,2,1,0,4,3),
        intArrayOf(8,7,6,5,9,3,2,1,0,4), intArrayOf(9,8,7,6,5,4,3,2,1,0)
    )
    private val verhoeffP = arrayOf(
        intArrayOf(0,1,2,3,4,5,6,7,8,9), intArrayOf(1,5,7,6,2,8,3,0,9,4),
        intArrayOf(5,8,0,3,7,9,6,1,4,2), intArrayOf(8,9,1,6,0,4,3,5,2,7),
        intArrayOf(9,4,5,3,1,2,6,8,7,0), intArrayOf(4,2,8,6,5,7,3,9,0,1),
        intArrayOf(2,7,9,3,8,0,6,4,1,5), intArrayOf(7,0,4,6,9,1,3,2,5,8)
    )
    private fun verhoeffValid(number: String): Boolean {
        var c = 0
        for ((i, ch) in number.reversed().withIndex()) {
            c = verhoeffD[c][verhoeffP[i % 8][ch - '0']]
        }
        return c == 0
    }

    /** Flags any field whose value matches text from one of our own prompt files - such
     *  a value was copied from the prompt, not read from the document. Checked before
     *  every other rule: a leaked value is otherwise indistinguishable from a correct,
     *  well-formed answer. */
    private fun findLeaks(obj: org.json.JSONObject): List<String> =
        obj.keys().asSequence().mapNotNull { k ->
            val v = str(obj, k) ?: return@mapNotNull null
            val exactLeak = knownPromptLeaks.any { it.equals(v, ignoreCase = true) }
            // Contains-match, not exact-match: the failure mode isn't always "the
            // whole field IS the leaked string" - found 2026-09-16, the model glued
            // skin_colour's own prompt description onto a real value and filed it
            // under a DIFFERENT field ("COLOUR OF SKIN BROWN" under
            // social_assistance_number). An exact-match entry only catches that one
            // specific recombination; this catches the phrase regardless of what
            // else gets glued to it.
            val fragmentLeak = knownPromptLeakFragments.any { v.contains(it, ignoreCase = true) }
            if (exactLeak || fragmentLeak) "$k = '$v'" else null
        }.toList()

    private fun coverageReport(
        label: String, obj: org.json.JSONObject, readableFields: List<String>,
        problems: List<String>, notes: List<String>, oftenNullFields: List<String> = emptyList()
    ): String {
        val read = readableFields.count { str(obj, it) != null }
        val total = readableFields.size
        val nulls = readableFields.filter { str(obj, it) == null }
        val grade = when {
            problems.isNotEmpty() -> "LOW"
            read == total -> "HIGH"
            read >= total - 1 -> "MEDIUM"
            else -> "LOW"
        }
        val sb = StringBuilder("$label check: $grade - $read/$total fields read")
        if (nulls.isNotEmpty()) sb.append(", null: ${nulls.joinToString(", ")}")
        // Fields the document itself often doesn't print at all (confirmed by the
        // user for tt_id's social_assistance_number/blood_group/national_insurance_
        // number) are shown for information only - a null here is normal, not a
        // miss, so they're excluded from the read/total ratio that drives grade.
        if (oftenNullFields.isNotEmpty()) {
            sb.append("\n  often blank on this document, not graded: ")
            sb.append(oftenNullFields.joinToString(", ") { "$it=${str(obj, it) ?: "null"}" })
        }
        if (notes.isNotEmpty()) sb.append("\n").append(notes.joinToString("\n") { "  $it" })
        if (problems.isNotEmpty()) sb.append("\n").append(problems.joinToString("\n") { "  - $it" })
        return sb.toString()
    }

    /** Parses one side's raw model output into a JSONObject, checking for malformed
     *  JSON and prompt/leak fabrication. Returns (object, null) on success or
     *  (null, errorReport) on failure - shared by the single-image validator below and
     *  the two-sided (front+back) validator further down. */
    private fun parseSide(label: String, text: String): Pair<org.json.JSONObject?, String?> {
        val start = text.indexOf('{')
        val end = text.lastIndexOf('}')
        if (start < 0 || end <= start) return null to "$label check: FAILED - no JSON object found"

        val obj = try {
            org.json.JSONObject(text.substring(start, end + 1))
        } catch (e: Exception) {
            return null to "$label check: FAILED - malformed JSON (${e.message})"
        }

        val leaked = findLeaks(obj)
        if (leaked.isNotEmpty()) {
            return null to ("$label check: FABRICATED - value(s) copied from the prompt, " +
                "not read from the document:\n" +
                leaked.joinToString("\n") { "  - $it" } +
                "\nDo not trust any field in this output.")
        }
        return obj to null
    }

    /** Crop prompts return a bare value, not JSON - no parseSide/leak-check applies (there's
     *  no prompt-shaped JSON structure to echo back). Just a direct format check per field. */
    // pan_crop's prompt doesn't tell the model (or us) in advance which of the four PAN
    // fields a given crop shows, so the field type is inferred here from the shape of
    // whatever came back rather than fixed per doc type. PAN number and date shapes are
    // unambiguous; a name-shaped value can't be told apart from a parent's name-shaped
    // value by shape alone, so that case is reported as such rather than guessed.
    private fun validateCropField(docType: String, text: String): String {
        val value = text.trim()
        return when (docType) {
             "pan_crop" -> {
                 val panPattern = Regex("^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$")
                 val dobPattern = Regex("^\\d{2}/\\d{2}/\\d{4}$")
                 val namePattern = Regex("^[A-Z][A-Z. ]*[A-Z.]$")
                 when {
                     panPattern.matches(value) -> "PAN crop check: OK (PAN number)"
                     dobPattern.matches(value) -> "PAN crop check: OK (date of birth)"
                     namePattern.matches(value) ->
                         "PAN crop check: OK (name-shaped value - could be the cardholder's " +
                         "name or a parent's name; the crop alone can't tell which)"
                     else -> "PAN crop check: FAILED - \"$value\" does not match PAN number, " +
                         "date, or name format"
                 }
             }
            else -> "Unknown crop document type: $docType"
        }
    }

    private fun validateExtraction(docType: String, text: String): String {
        val (obj, err) = parseSide(docType.uppercase(), text)
        if (err != null) return err

        return when (docType) {
            "pan" -> validatePan(obj!!)
            "pan_full" -> validatePan(obj!!)
            "aadhaar" -> validateAadhaar(obj!!)
            "passport" -> validatePassport(obj!!)
            "dl" -> validateDl(obj!!)
            "tt_passport" -> validateTtPassport(obj!!)
            "tt_dl" -> validateTtDl(obj!!)
            "pan_stage1" -> validateStage1Pan(obj!!)
            "aadhaar_stage1" -> validateStage1Aadhaar(obj!!)
            else -> "Unknown document type: $docType"
        }
    }

    /** Same shape as validateExtraction, but for a document whose data is split
     *  across two separate images (front + back) - currently only tt_id. Parses each
     *  side independently (own leak-check each), then hands both objects to a
     *  type-specific validator that merges and cross-checks them. */
    private fun validateTwoSidedExtraction(docType: String, frontText: String, backText: String): String {
        val (frontObj, frontErr) = parseSide("${docType.uppercase()} FRONT", frontText)
        if (frontErr != null) return frontErr
        val (backObj, backErr) = parseSide("${docType.uppercase()} BACK", backText)
        if (backErr != null) return backErr

        return when (docType) {
            "tt_id" -> validateTtId(frontObj!!, backObj!!)
            else -> "Unknown two-sided document type: $docType"
        }
    }

    private fun validatePan(obj: org.json.JSONObject): String {
        val fields = listOf("name", "parent_name", "parent_relation", "dob", "pan_number")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "pan") problems += "document_type is not 'pan'"
        for (k in listOf("gender", "address", "expiry_date", "issue_date", "confidence")) {
            if (obj.has(k)) problems += "$k must not appear on a PAN record"
        }

        val name = str(obj, "name")
        val parent = str(obj, "parent_name")
        if (name != null && parent != null && name.equals(parent, ignoreCase = true)) {
            problems += "name and parent_name are identical"
        }

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear())

        // PAN structure: 3 series letters, entity type, surname/given-name initial, 4
        // digits, check letter.
        val pan = str(obj, "pan_number")
        if (pan != null) {
            if (!Regex("^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$").matches(pan)) {
                problems += "pan_number '$pan' is not a valid PAN structure"
            } else if (pan[3] == 'P') {
                // Individual: character 5 is meant to be the initial of the holder's
                // surname, but which WORD of "name" carries that surname is not fixed -
                // confirmed on two real cards. "D MANIKANDAN" (Tamil-style initial +
                // given name) matched its LAST word (M); "KOCHERLA SRIKANTH" (surname
                // first) matched its FIRST word (K), and both pan_number/name reads were
                // independently verified character-perfect against the physical card.
                // So: check every word in name, not one fixed position, and only note a
                // possible mismatch rather than call it a "problem" - a real, correct
                // read can legitimately match none of them for a naming style not yet
                // seen, and this check must never drag a correct read down to LOW.
                val nameInitials = name?.trim()?.split(Regex("\\s+"))
                    ?.mapNotNull { it.firstOrNull()?.uppercaseChar() }.orEmpty()
                if (nameInitials.isNotEmpty() && pan[4] !in nameInitials) {
                    notes += "note: pan_number char 5 '${pan[4]}' matches none of name's " +
                        "word-initials (${nameInitials.joinToString("")}) - " +
                        "worth a manual glance, not necessarily wrong"
                }
            }
        }

        return coverageReport("PAN", obj, fields, problems, notes)
    }

    private fun validateAadhaar(obj: org.json.JSONObject): String {
        val fields = listOf("name", "dob", "gender", "aadhaar_number", "address", "pincode")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "aadhaar") problems += "document_type is not 'aadhaar'"
        // issue_date was dropped from the schema entirely: it's printed in a corner
        // stamp too small for this vision encoder to reliably resolve, and the model
        // was fabricating a plausible-looking date rather than admitting it couldn't
        // read it (found 2026-09-16, "25/04/2017" on a card with no such date visible).
        if (obj.has("issue_date")) problems += "issue_date must not appear on an Aadhaar record"

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear())

        // The schema now accepts the number in its natural printed grouping ("1234 5678
        // 9012") as well as bare digits, because forcing bare-digits-only previously gave
        // the model no legal way to emit the number the way it naturally reads it - the
        // grammar rejected the spaced form and the value fell through into "address"
        // instead (found 2026-09-16). Normalize here rather than relying on the model to
        // strip spaces itself, the same reasoning as the date normalizer.
        val aadhaarRaw = str(obj, "aadhaar_number")
        if (aadhaarRaw != null) {
            val aadhaar = aadhaarRaw.replace(" ", "")
            if (!Regex("^[0-9]{12}$").matches(aadhaar)) {
                problems += "aadhaar_number '$aadhaarRaw' is not 12 digits"
            } else {
                notes += "aadhaar_number -> $aadhaar"
                if (!verhoeffValid(aadhaar)) {
                    problems += "aadhaar_number '$aadhaarRaw' fails the Aadhaar checksum " +
                        "(Verhoeff algorithm) - at least one digit is misread"
                }
            }
        }

        val pin = str(obj, "pincode")
        if (pin != null && !Regex("^[0-9]{6}$").matches(pin)) {
            problems += "pincode '$pin' is not 6 digits"
        }

        // Observed live, three times now on three different cards, with no address block
        // actually visible: rather than returning null as instructed, the model fabricates
        // an "address" by recombining content it already generated for OTHER fields on the
        // same record - or content belonging to a DIFFERENT field entirely. Card 1: address
        // was a verbatim duplicate of aadhaar_number. Card 2: address embedded name
        // ("Basant Raj") and dob ("01/01/2000") verbatim, plus invented filler text, with
        // pincode a plausible-looking but unconnected invented number. Card 3 (same
        // "Basant Raj" card, re-tested 2026-09-17): a DIFFERENT fabrication this time -
        // "Nawabzat R expanse / Enrolment No.: 4049/30507/00690" - no longer duplicating
        // this record's own fields, but instead referencing "Enrolment No.", which is a
        // real but DIFFERENT Aadhaar field (the enrollment ID, distinct from the Aadhaar
        // number) that has no business appearing inside an address. Confirmed by the user:
        // this card has no real address printed. Same underlying behaviour every time -
        // when this field has nothing real to point to, the model recombines whatever
        // concept is "nearby" rather than admitting it doesn't know. A prose "use null"
        // instruction has proven unreliable against this, so detect it instead: a genuine
        // address never legitimately contains the resident's own name/dob/aadhaar_number,
        // nor a reference to the enrollment-number field.
        val addr = str(obj, "address")
        val name = str(obj, "name")
        val dobRaw = str(obj, "dob")
        val aadhaarDigitsOnly = aadhaarRaw?.replace(" ", "")
        val fabricated = addr != null && (
            (aadhaarDigitsOnly != null && addr.replace(" ", "") == aadhaarDigitsOnly) ||
            (name != null && addr.contains(name, ignoreCase = true)) ||
            (dobRaw != null && addr.contains(dobRaw)) ||
            Regex("enrolment no|enrollment no|\\beid\\b", RegexOption.IGNORE_CASE).containsMatchIn(addr)
        )
        if (fabricated) {
            problems += "address reuses this record's own name/dob/aadhaar_number, or " +
                "references the enrollment-number field - almost certainly fabricated, " +
                "not a real address"
            // A PIN with no genuine address behind it has no legitimate source either.
            if (pin != null) problems += "pincode has no genuine address to derive from"
        } else if (pin != null && aadhaarDigitsOnly != null && aadhaarDigitsOnly.startsWith(pin)) {
            problems += "pincode '$pin' looks derived from aadhaar_number, not a real PIN"
        } else if (addr != null && pin != null) {
            // Found live 2026-09-17: address ended "...Andhra Pradesh - 516309" while the
            // separate pincode field said "759831" - two different numbers for what should
            // be the same PIN. A real address's own trailing PIN and the standalone
            // pincode field must agree; take the LAST 6-digit run in the address as its
            // embedded PIN, since that's where a genuine Indian address prints one.
            val addrPin = Regex("\\b[0-9]{6}\\b").findAll(addr).lastOrNull()?.value
            if (addrPin != null && addrPin != pin) {
                problems += "address ends in PIN '$addrPin' but the separate pincode field " +
                    "says '$pin' - these should be the same PIN and are not"
            }
        }

        return coverageReport("Aadhaar", obj, fields, problems, notes)
    }

    // --- Stage1 fine-tune eval (PAN/Aadhaar only, see MODEL_HANDOVER_stage1.md) -------
    // Deliberately a much lighter check than validatePan/validateAadhaar above: this
    // model's own schema (section 4 of the handover) has a different field list
    // entirely - no document_type, no parent_relation, no pincode, plus new fields
    // (signature_present, vid) - and grammar-constrained decoding is off for these two
    // doc types specifically, so unlike the stock checks, "did the model even follow
    // the schema" is itself part of what's being measured, not assumed. The three
    // things the handover doc asks us to log (parse failures, null-but-visible fields,
    // wrong-value fields) mostly can't be judged by code at all - null-but-visible and
    // wrong-value both require comparing against the physical document by eye. What
    // this can do automatically: report which schema keys came back null, flag any
    // key the model invented outside the schema (a direct format-discipline signal the
    // handover explicitly cares about), and keep the two objective structural checks
    // (PAN format, Aadhaar checksum) that don't depend on our app's own field names.

    private fun stage1SchemaCheck(obj: org.json.JSONObject, expected: List<String>): String? {
        val extra = obj.keys().asSequence().filterNot { it in expected }.toList()
        return if (extra.isEmpty()) null else
            "unexpected key(s) not in the stage1 schema: ${extra.joinToString(", ")}"
    }

    private fun validateStage1Pan(obj: org.json.JSONObject): String {
        val fields = listOf("pan_number", "name", "father_name", "date_of_birth", "signature_present")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        stage1SchemaCheck(obj, fields)?.let { problems += it }
        checkDateShape(problems, notes, obj, "date_of_birth", 1900, currentYear())

        val pan = str(obj, "pan_number")
        if (pan != null && !Regex("^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$").matches(pan)) {
            problems += "pan_number '$pan' is not a valid PAN structure"
        }

        return coverageReport("PAN (stage1)", obj, fields, problems, notes)
    }

    private fun validateStage1Aadhaar(obj: org.json.JSONObject): String {
        val fields = listOf("aadhaar_number", "vid", "name", "date_of_birth", "gender", "address")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        stage1SchemaCheck(obj, fields)?.let { problems += it }
        checkDateShape(problems, notes, obj, "date_of_birth", 1900, currentYear())

        val aadhaarRaw = str(obj, "aadhaar_number")
        if (aadhaarRaw != null) {
            val aadhaar = aadhaarRaw.replace(" ", "")
            if (!Regex("^[0-9]{12}$").matches(aadhaar)) {
                problems += "aadhaar_number '$aadhaarRaw' is not 12 digits"
            } else if (!verhoeffValid(aadhaar)) {
                problems += "aadhaar_number '$aadhaarRaw' fails the Aadhaar checksum " +
                    "(Verhoeff algorithm) - at least one digit is misread"
            }
        }

        // VID (Virtual ID) is always 16 digits per UIDAI spec when present - unlike the
        // rest of this schema, this is an objective format fact, not a borrowed
        // assumption from a different document type.
        val vid = str(obj, "vid")
        if (vid != null && !Regex("^[0-9]{16}$").matches(vid.replace(" ", ""))) {
            problems += "vid '$vid' is not 16 digits"
        }

        return coverageReport("Aadhaar (stage1)", obj, fields, problems, notes)
    }

    private fun validatePassport(obj: org.json.JSONObject): String {
        // father_name dropped: modern Indian passports don't print it on this page
        // (removed from the printed booklet after a 2018 government notification), and
        // asking for a field that isn't there is exactly the pattern that made the model
        // fabricate a PAN gender and an Aadhaar issue date earlier this session.
        val fields = listOf("name", "dob", "gender", "place_of_birth",
            "passport_number", "issue_date", "expiry_date", "issuing_authority", "nationality")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "passport") problems += "document_type is not 'passport'"

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear())
        checkDateShape(problems, notes, obj, "issue_date", 1990, currentYear())
        checkDateShape(problems, notes, obj, "expiry_date", 1990, currentYear() + 20)

        val pp = str(obj, "passport_number")
        if (pp != null && !Regex("^[A-Z][0-9]{7}$").matches(pp)) {
            problems += "passport_number '$pp' is not 1 letter + 7 digits"
        }

        // Unverified against a real sample - no passport image exists in this workspace.
        // Structural checks here are best-effort; only the field set was confirmed.
        return coverageReport("Passport", obj, fields, problems, notes)
    }

    private fun validateTtPassport(obj: org.json.JSONObject): String {
        // Trinidad & Tobago passport. Field list corrected 2026-09-16 to match what is
        // actually printed on the biodata page: type, country_code, passport_number,
        // surname, given_names (split, not one combined "name"), nationality, gender,
        // dob, place_of_birth, issue_date, issuing_authority, expiry_date. Still
        // completely unverified against a real sample - no image exists in this
        // workspace, and the exact passport_number format hasn't been confirmed, so no
        // structural regex is enforced on it (schema only bounds its length).
        val fields = listOf("type", "country_code", "passport_number", "surname",
            "given_names", "nationality", "gender", "dob", "place_of_birth",
            "issue_date", "issuing_authority", "expiry_date")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "tt_passport") {
            problems += "document_type is not 'tt_passport'"
        }

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear())
        checkDateShape(problems, notes, obj, "issue_date", 1990, currentYear())
        checkDateShape(problems, notes, obj, "expiry_date", 1990, currentYear() + 20)

        val surname = str(obj, "surname")
        val given = str(obj, "given_names")
        if (surname != null && given != null && surname.equals(given, ignoreCase = true)) {
            problems += "surname and given_names are identical"
        }

        val cc = str(obj, "country_code")
        if (cc != null && !Regex("^[A-Z]{3}$").matches(cc)) {
            problems += "country_code '$cc' is not a 3-letter code"
        }

        notes += "passport_number format unverified for this country - no structural check applied"

        return coverageReport("TT Passport", obj, fields, problems, notes)
    }

    private fun validateTtDl(obj: org.json.JSONObject): String {
        // Trinidad & Tobago driving permit, added 2026-09-16. Field set given directly
        // by the user - permit_number, name, dob, gender ("SEX"), address, issue_date,
        // expiry_date, payment_date, transaction_code ("TR"), class ("CLASS"). name and
        // address are printed on the card with NO label at all, confirmed by the user.
        // Completely unverified against a real sample.
        val fields = listOf("permit_number", "name", "dob", "gender", "address",
            "issue_date", "expiry_date", "payment_date", "transaction_code", "class")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "tt_dl") problems += "document_type is not 'tt_dl'"

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear())
        checkDateShape(problems, notes, obj, "issue_date", 1990, currentYear())
        checkDateShape(problems, notes, obj, "expiry_date", 1990, currentYear() + 20)
        checkDateShape(problems, notes, obj, "payment_date", 1990, currentYear())

        // Same duplication artifact confirmed live on the Indian DL (issue_date and
        // expiry_date coming back byte-identical) - applied here proactively since the
        // date layout is the same shape.
        val issueRaw = str(obj, "issue_date")
        val expiryRaw = str(obj, "expiry_date")
        if (issueRaw != null && expiryRaw != null && issueRaw == expiryRaw) {
            problems += "issue_date and expiry_date are identical ('$issueRaw') - " +
                "likely one was duplicated rather than genuinely read"
        }

        // Same address-fabrication pattern confirmed live on Aadhaar and applied
        // proactively to the Indian DL - a genuine address never legitimately contains
        // the holder's own name/dob/permit_number.
        val addr = str(obj, "address")
        val name = str(obj, "name")
        val dobRaw = str(obj, "dob")
        val permit = str(obj, "permit_number")
        val fabricated = addr != null && (
            (name != null && addr.contains(name, ignoreCase = true)) ||
            (dobRaw != null && addr.contains(dobRaw)) ||
            (permit != null && addr.replace(" ", "").replace("-", "")
                .contains(permit.replace(" ", "").replace("-", "")))
        )
        if (fabricated) {
            problems += "address reuses this record's own name/dob/permit_number - " +
                "almost certainly fabricated, not a real address"
        }

        notes += "permit_number format unverified for this document - no structural check applied"

        return coverageReport("TT Driving Permit", obj, fields, problems, notes)
    }

    private fun validateTtId(front: org.json.JSONObject, back: org.json.JSONObject): String {
        // Trinidad & Tobago National Identification Card, added 2026-09-16. The ONLY
        // two-sided document type: front carries just name, back carries everything
        // else, both confirmed by the user. name is printed (unlabelled) on both
        // sides, so cross-checking the two reads catches a misread neither side alone
        // could. social_assistance_number, blood_group and national_insurance_number
        // are commonly blank on real cards (confirmed by the user) - kept out of the
        // grading ratio via oftenNullFields so a genuinely correct read isn't dragged
        // down just because those three are null. Completely unverified against a
        // real sample.
        if (str(front, "document_type") != "tt_id_front") {
            return "TT_ID check: FAILED - front image's document_type is not 'tt_id_front'"
        }
        if (str(back, "document_type") != "tt_id_back") {
            return "TT_ID check: FAILED - back image's document_type is not 'tt_id_back'"
        }

        val backFields = listOf("dob", "issue_date", "expiry_date", "registration_number",
            "citizenship_status", "place_of_birth", "gender", "eye_colour", "height_cm",
            "social_assistance_number", "skin_colour", "blood_group",
            "national_insurance_number")
        val merged = org.json.JSONObject()
        merged.put("document_type", "tt_id")
        merged.put("name", front.opt("name") ?: org.json.JSONObject.NULL)
        for (k in backFields) merged.put(k, back.opt(k) ?: org.json.JSONObject.NULL)

        val coreFields = listOf("name", "dob", "issue_date", "expiry_date",
            "registration_number", "citizenship_status", "place_of_birth", "gender",
            "eye_colour", "height_cm", "skin_colour")
        val oftenNullFields = listOf(
            "social_assistance_number", "blood_group", "national_insurance_number")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        checkDateShape(problems, notes, merged, "dob", 1900, currentYear())
        checkDateShape(problems, notes, merged, "issue_date", 1990, currentYear())
        checkDateShape(problems, notes, merged, "expiry_date", 1990, currentYear() + 20)

        // Same duplication artifact confirmed live on the Indian DL - applied here
        // proactively since the date layout is the same shape.
        val issueRaw = str(merged, "issue_date")
        val expiryRaw = str(merged, "expiry_date")
        if (issueRaw != null && expiryRaw != null && issueRaw == expiryRaw) {
            problems += "issue_date and expiry_date are identical ('$issueRaw') - " +
                "likely one was duplicated rather than genuinely read"
        }

        // name is printed on both sides of the same physical card. Corrected
        // 2026-09-16: a real card showed "Lynette Herbert" on front vs "Herbert
        // Lynette" on back - same two words, given-name/surname order swapped between
        // the sides. So compare word SETS, not literal string equality - only a real
        // wording difference (not just order) is a genuine misread signal. Same
        // reasoning as PAN's surname-initial check, which also had to stop assuming
        // one fixed word order.
        val frontName = str(front, "name")
        val backName = str(back, "name")
        if (frontName != null && backName != null) {
            val frontWords = frontName.trim().split(Regex("\\s+")).map { it.lowercase() }.toSet()
            val backWords = backName.trim().split(Regex("\\s+")).map { it.lowercase() }.toSet()
            if (frontWords != backWords) {
                problems += "front name '$frontName' does not match back name '$backName'"
            } else if (frontName != backName) {
                notes += "front name '$frontName' and back name '$backName' use the same " +
                    "words in a different order - likely a display convention, not a misread"
            }
        }

        // social_assistance_number, blood_group and national_insurance_number are
        // known-high-risk for fabrication when genuinely absent (confirmed live
        // 2026-09-16: blood_group came back "B+" on a card with no blood group
        // printed anywhere). There's no internal-consistency signal to catch this the
        // way the address checks elsewhere in this file do, so it can only be flagged
        // for manual verification, not caught automatically.
        for (k in oftenNullFields) {
            val v = str(merged, k)
            if (v != null) {
                notes += "note: $k = '$v' - this field is known to sometimes be " +
                    "fabricated when genuinely absent from the card; verify against " +
                    "the physical document"
            }
        }

        return coverageReport("TT National ID", merged, coreFields, problems, notes, oftenNullFields)
    }

    private fun validateDl(obj: org.json.JSONObject): String {
        // Field list corrected 2026-09-17 to match a real Indian DL: license_number,
        // name, dob, address (its PIN code is embedded in this text, not a separate
        // field), issue_date, validity_nt/validity_tr (split from a single expiry_date
        // - Indian DLs print separate validity dates for Non-Transport and Transport
        // vehicle categories; validity_tr is commonly blank), blood_group (commonly
        // blank), organ_donor (Y/N). gender, a standalone pincode field, and
        // issuing_authority were removed entirely - confirmed not present on this card.
        val fields = listOf("license_number", "name", "dob", "address", "issue_date",
            "validity_nt", "organ_donor", "vehicle_classes")
        val oftenNullFields = listOf("validity_tr", "blood_group")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "dl") problems += "document_type is not 'dl'"

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear())
        checkDateShape(problems, notes, obj, "issue_date", 1990, currentYear())
        checkDateShape(problems, notes, obj, "validity_nt", 1990, currentYear() + 20)
        checkDateShape(problems, notes, obj, "validity_tr", 1990, currentYear() + 20)

        // A DL's validity period is normally years long - issue_date and validity_nt
        // being byte-identical is a strong tell that the model duplicated one into the
        // other rather than genuinely reading a separate field (this exact pattern was
        // found live 2026-09-16, back when this field was still called expiry_date).
        val issueRaw = str(obj, "issue_date")
        val validityNtRaw = str(obj, "validity_nt")
        val validityTrRaw = str(obj, "validity_tr")
        if (issueRaw != null && validityNtRaw != null && issueRaw == validityNtRaw) {
            problems += "issue_date and validity_nt are identical ('$issueRaw') - " +
                "likely one was duplicated rather than genuinely read"
        }
        // Less clear-cut than issue==validity_nt: NT and TR could legitimately share a
        // date if both endorsements were renewed together, so this is only a note.
        if (validityNtRaw != null && validityTrRaw != null && validityNtRaw == validityTrRaw) {
            notes += "note: validity_nt and validity_tr are identical ('$validityNtRaw') - " +
                "possible, but worth a manual glance since these cover different vehicle categories"
        }

        // Same fabrication pattern confirmed live on Aadhaar - a genuine address never
        // legitimately contains the record's own name/dob/license_number.
        val addr = str(obj, "address")
        val name = str(obj, "name")
        val dobRaw = str(obj, "dob")
        val license = str(obj, "license_number")
        val fabricated = addr != null && (
            (name != null && addr.contains(name, ignoreCase = true)) ||
            (dobRaw != null && addr.contains(dobRaw)) ||
            (license != null && addr.replace(" ", "").replace("-", "")
                .contains(license.replace(" ", "").replace("-", "")))
        )
        if (fabricated) {
            problems += "address reuses this record's own name/dob/license_number - " +
                "almost certainly fabricated, not a real address"
        }

        // validity_tr and blood_group are commonly blank on real cards - excluded from
        // the grading ratio via oftenNullFields. Both are also known-fabrication-risk
        // fields (same pattern confirmed live on tt_id's blood_group), so flag them for
        // manual verification whenever non-null rather than trusting them silently.
        for (k in oftenNullFields) {
            val v = str(obj, k)
            if (v != null) {
                notes += "note: $k = '$v' - this kind of field is known to sometimes be " +
                    "fabricated when genuinely absent; verify against the physical document"
            }
        }

        return coverageReport("DL", obj, fields, problems, notes, oftenNullFields)
    }

    private class MonitorHandle { @Volatile var stop = false }

    /** java.lang.Process.pid() isn't exposed by this compileSdk's Android stub. Android's
     *  ProcessBuilder implementation still stores the child's pid in a "pid" field on the
     *  returned Process object, so pull it via reflection (the standard pre-API-26 trick). */
    private fun getPid(process: Process): Long? = try {
        val field = process.javaClass.getDeclaredField("pid")
        field.isAccessible = true
        field.getInt(process).toLong()
    } catch (e: Exception) {
        null
    }

    /** Fallback if reflection ever fails on some OEM build: resolve via `pidof`. */
    private fun waitForPid(processName: String): Long? {
        repeat(20) {
            try {
                val p = ProcessBuilder("/system/bin/sh", "-c", "pidof $processName").start()
                val out = p.inputStream.bufferedReader().readText().trim()
                p.waitFor()
                val pid = out.split(Regex("\\s+")).firstOrNull()?.toLongOrNull()
                if (pid != null) return pid
            } catch (e: Exception) {
                // fall through to retry
            }
            Thread.sleep(50)
        }
        return null
    }

    private fun startMonitoring(pid: Long, startNanos: Long): MonitorHandle {
        val handle = MonitorHandle()
        var lastCpuTicks = -1L
        var lastWallNanos = startNanos

        Thread {
            while (!handle.stop) {
                val elapsedS = (System.nanoTime() - startNanos) / 1e9
                val ramMb = readVmRssMb(pid)
                val (cpuPct, newLastTicks, newLastWall) = readCpuPercent(pid, lastCpuTicks, lastWallNanos)
                lastCpuTicks = newLastTicks
                lastWallNanos = newLastWall
                val gpuBusy = readFile("/sys/kernel/gpu/gpu_busy")?.trim()
                val gpuClock = readFile("/sys/kernel/gpu/gpu_clock")?.trim()

                ui.post {
                    binding.tvElapsed.text = "Elapsed: %.1fs".format(elapsedS)
                    binding.tvRam.text = if (ramMb != null) "RAM: %.0f MB".format(ramMb) else "RAM: --"
                    binding.tvCpu.text = if (cpuPct != null) "CPU: %.0f%%".format(cpuPct) else "CPU: --"
                    binding.tvGpu.text = "GPU: ${gpuBusy ?: "--"}  (clock ${gpuClock ?: "--"})"
                }
                Thread.sleep(300)
            }
        }.start()
        return handle
    }

    private fun readFile(path: String): String? = try {
        File(path).readText()
    } catch (e: Exception) {
        null
    }

    private fun readVmRssMb(pid: Long): Double? {
        val status = readFile("/proc/$pid/status") ?: return null
        val line = status.lines().firstOrNull { it.startsWith("VmRSS:") } ?: return null
        val kb = line.substringAfter("VmRSS:").trim().split(Regex("\\s+")).firstOrNull()?.toLongOrNull()
        return kb?.let { it / 1024.0 }
    }

    /** Returns (cpuPercentSinceLastSample, thisSampleTicks, thisSampleWallNanos). */
    private fun readCpuPercent(pid: Long, lastTicks: Long, lastWallNanos: Long): Triple<Double?, Long, Long> {
        val nowWallNanos = System.nanoTime()
        val stat = try {
            RandomAccessFile("/proc/$pid/stat", "r").use { it.readLine() }
        } catch (e: Exception) {
            null
        } ?: return Triple(null, lastTicks, nowWallNanos)

        // Fields after the comm field "(name)" are space-separated; utime=14th, stime=15th (1-indexed overall).
        val afterComm = stat.substringAfter(") ")
        val fields = afterComm.split(" ")
        val utime = fields.getOrNull(11)?.toLongOrNull() ?: return Triple(null, lastTicks, nowWallNanos)
        val stime = fields.getOrNull(12)?.toLongOrNull() ?: return Triple(null, lastTicks, nowWallNanos)
        val totalTicks = utime + stime

        if (lastTicks < 0) return Triple(null, totalTicks, nowWallNanos)

        val deltaTicks = totalTicks - lastTicks
        val deltaWallSeconds = (nowWallNanos - lastWallNanos) / 1e9
        if (deltaWallSeconds <= 0) return Triple(null, totalTicks, nowWallNanos)

        val cpuPercent = (deltaTicks / clkTck.toDouble()) / deltaWallSeconds * 100.0
        return Triple(cpuPercent, totalTicks, nowWallNanos)
    }

    /** Strips llama.cpp's startup log preamble, keeping just the generated answer. */
    /**
     * llama-mtmd-cli's startup log includes an unrelated example conversation
     * ("Hello" / "Hi there" / "How are you?") dumped as a chat-template preview,
     * followed later by the real timestamped log lines, then the actual answer.
     * Filtering line-by-line let that example conversation leak through (its lines
     * have no timestamp prefix). Anchoring on the LAST log/warning line instead
     * skips both the log preamble and the earlier template dump in one step.
     */
    private fun extractAnswer(raw: String): String {
        val logLinePattern = Regex("^\\d+\\.\\d+\\.\\d+\\.\\d+\\s+[IWE]\\s")
        val lines = raw.lines()
        val lastLogIndex = lines.indexOfLast { logLinePattern.containsMatchIn(it) || it.trimStart().startsWith("WARN:") || it.trim().startsWith("For normal use cases") }
        val answerLines = if (lastLogIndex >= 0) lines.drop(lastLogIndex + 1) else lines
        val text = answerLines.joinToString("\n").trim()
        return text.ifEmpty { raw.trim() }
    }
}
