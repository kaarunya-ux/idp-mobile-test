package com.modeltesting.vlmtester

import android.app.ActivityManager
import android.content.Context
import android.content.Intent
import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
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
import com.google.android.gms.tasks.Tasks
import com.google.mlkit.vision.common.InputImage
import com.google.mlkit.vision.text.TextRecognition
import com.google.mlkit.vision.text.latin.TextRecognizerOptions
import com.modeltesting.vlmtester.databinding.ActivityMainBinding
import java.io.File
import java.io.FileOutputStream
import java.io.RandomAccessFile
import kotlin.math.roundToInt

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
                ) {
                    updateForDocType()
                    warmUpQwenServer()
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
            }
        // Qwen3-VL runs best on a persistent server (see useQwenPanProfile), so picking a Qwen
        // model turns server mode on and starts warming it up. The switch stays the person's to
        // turn off, and nothing here turns it off for other models. The chosen pair is
        // remembered so reopening the app warms the same model again.
        val prefs = getSharedPreferences("vlm_tester", Context.MODE_PRIVATE)
        binding.spinnerModel.onItemSelectedListener =
            object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(
                    parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long
                ) {
                    val name = binding.spinnerModel.selectedItem as? String ?: return
                    prefs.edit().putString("last_model", name).apply()
                    if (isQwenModel(name)) binding.switchServerMode.isChecked = true
                    warmUpQwenServer()
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
            }
        binding.spinnerMmproj.onItemSelectedListener =
            object : android.widget.AdapterView.OnItemSelectedListener {
                override fun onItemSelected(
                    parent: android.widget.AdapterView<*>?, view: View?, position: Int, id: Long
                ) {
                    val name = binding.spinnerMmproj.selectedItem as? String ?: return
                    prefs.edit().putString("last_mmproj", name).apply()
                    warmUpQwenServer()
                }
                override fun onNothingSelected(parent: android.widget.AdapterView<*>?) {}
            }
        binding.switchServerMode.setOnCheckedChangeListener { _, _ -> warmUpQwenServer() }
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

        handleOcrIntent(intent)
    }

    override fun onNewIntent(intent: Intent) {
        super.onNewIntent(intent)
        handleOcrIntent(intent)
    }

    // ---- OCR-first experiment (branch 2b-withOCR) -------------------------------------
    // Headless trigger so OCR quality/latency can be measured over adb on a folder of test
    // images without tapping through the UI:
    //   adb shell am start -n com.modeltesting.vlmtester/.MainActivity --ez ocr_run true
    // Reads every image in <externalFilesDir>/ocr_in (adb push them there - that folder is
    // readable without storage permission) and writes <externalFilesDir>/ocr_results.txt:
    // per image the decoded size, OCR time, and every recognised line with its bounding box.
    private val ocrRecognizer by lazy {
        TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
    }

    private fun handleOcrIntent(i: Intent?) {
        if (i?.getBooleanExtra("ocr_run", false) != true) return
        i.removeExtra("ocr_run")
        Thread {
            try { runOcrOnFolder() }
            catch (e: Exception) {
                File(getExternalFilesDir(null), "ocr_results.txt").writeText("OCR run failed: $e\n")
            }
        }.start()
    }

    private fun runOcrOnFolder() {
        val inDir = File(getExternalFilesDir(null), "ocr_in")
        val files = inDir.listFiles { f ->
            f.extension.lowercase() in setOf("jpg", "jpeg", "png", "webp")
        }?.sortedBy { it.name }.orEmpty()

        // Blocks the worker thread until ML Kit finishes; must never run on the main thread.
        fun ocr(bmp: Bitmap): Pair<com.google.mlkit.vision.text.Text, Double> {
            val t0 = System.nanoTime()
            val res = Tasks.await(ocrRecognizer.process(InputImage.fromBitmap(bmp, 0)))
            return res to (System.nanoTime() - t0) / 1e6
        }

        val sb = StringBuilder("OCR run: ${files.size} images from ${inDir.absolutePath}\n")
        // First call includes ML Kit's model initialisation; time it separately so the
        // per-image numbers below are the warm cost a second scan would see.
        files.firstOrNull()?.let { f ->
            BitmapFactory.decodeFile(f.absolutePath)?.let { bmp ->
                sb.append("first call (includes init): %.0f ms\n".format(ocr(bmp).second))
                bmp.recycle()
            }
        }
        for (f in files) {
            val bmp = BitmapFactory.decodeFile(f.absolutePath) ?: continue
            val (res, ms) = ocr(bmp)
            sb.append("=== ${f.name} ${bmp.width}x${bmp.height} ocr_ms=%.0f\n".format(ms))
            for (block in res.textBlocks) for (line in block.lines) {
                val r = line.boundingBox
                sb.append("[${r?.left},${r?.top},${r?.right},${r?.bottom}] ${line.text}\n")
            }
            bmp.recycle()
        }
        File(getExternalFilesDir(null), "ocr_results.txt").writeText(sb.toString())
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

        // Reselect what was used last time. Set here, before onCreate attaches the spinner
        // listeners, so the first selection event already reports the restored choice.
        val prefs = getSharedPreferences("vlm_tester", Context.MODE_PRIVATE)
        prefs.getString("last_model", null)?.let { lmModels.indexOf(it) }
            ?.takeIf { it >= 0 }?.let { binding.spinnerModel.setSelection(it) }
        prefs.getString("last_mmproj", null)?.let { mmprojModels.indexOf(it) }
            ?.takeIf { it >= 0 }?.let { binding.spinnerMmproj.setSelection(it) }
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

    // MiniCPM-V 4.6 also ships a real custom Jinja chat template (confirmed via its GGUF's
    // tokenizer.chat_template metadata, not plain ChatML), so it needs --jinja the same way
    // PaddleOCR-VL does. Its mtmd projector type is "minicpmv4_6" - a distinct preprocessing
    // path from both PaddleOCR-VL's area-based one and VisionPsy's idefics3, so it's kept as
    // its own flag rather than folded into isPaddleOcr, even where the two currently need
    // identical handling (--jinja, system-prompt folding).
    private fun isMiniCpmModel(modelPath: String): Boolean =
        File(modelPath).name.contains("minicpm", ignoreCase = true)

    /** LFM2-VL-3B (Liquid AI), Q4_K_M LM + Q8_0 mmproj. Its image path is NOT tiled below
     *  512x512 and its token count follows --image-max-tokens smoothly (about 0.15s per
     *  image token on this phone), so it gets its own settings instead of the generic ones:
     *  128 image tokens, -c 4096 (the GGUF's default context is 128,000), the spaced
     *  single-line grammar and the bullet prompt (assets/grammars/pan_full_lfm2.gbnf and
     *  assets/prompts/user_pan_full_lfm2.txt).
     *  All of it measured on-device 2026-09-30 and written up in
     *  LFM2_VL_3B_LATENCY_FINDINGS.md: 24/25 fields on the five test cards at 39.7s
     *  cold, 23/25 on full-card images at 35.7s cold / 33.3s warm server. */
    private fun isLfm2Model(modelPath: String): Boolean =
        File(modelPath).name.contains("lfm2", ignoreCase = true)

    /** Qwen3-VL (2B and 4B). Its GGUF also declares a 262,144-token context, so with no -c
     *  llama.cpp's auto-fit picks one from free memory: on this 7.6 GB phone the 4B got a
     *  26,368-token context (about 3.9 GB of KV cache beside 2.5 GB of weights; 39.6 GB
     *  projected at the full default) and took 111.5s against 83-99s with -c 4096 on the
     *  same card and settings, adjacent runs. Heat moved the same configuration by about
     *  8s, so the real saving is somewhere in 12-28s. With -c 4096 the process held about
     *  3.5 GB resident (2.3 GB of mapped weights, 1.2 GB heap). Measured 2026-10-06 in the
     *  adb harness on the 4B; the 2B was not re-measured. Real prompts here are well under
     *  4096 tokens even for the 1024-image-token doc types (about 1024 + 600 text + 512
     *  output). */
    private fun isQwenModel(modelPath: String): Boolean =
        File(modelPath).name.contains("qwen", ignoreCase = true)

    /** pan_full + LFM2-VL only - the one doc type whose LFM2 prompt/grammar are validated. */
    private fun useLfm2PanProfile(docType: String, modelPath: String): Boolean =
        docType == "pan_full" && isLfm2Model(modelPath)

    /** Image-token cap for the LFM2 pan_full profile. 196 -> 128 -> 64 gave 55 -> 44 -> 31s,
     *  but 64 lost parent_name/parent_relation (card e), so 128 is the floor that kept
     *  accuracy. The app's 128/256 pan_full toggle does not apply: 256 was never tested on
     *  LFM2 and would add ~0.15s per extra token (~+19s). */
    private val lfm2PanImageTokenCap = 128

    /** pan_full + Qwen3-VL only: warm server + a one-line GBNF (assets/grammars/pan_full_qwen.gbnf)
     *  + a card image sent at 480px. On the 4B (Q4_K_M + Q8_0 mmproj) in the adb harness,
     *  2026-10-06, on the four cards of pan_test_images: 44.6s average (41.4-50.3), 20/20 fields,
     *  against 82-85s for the one-shot CLI at full size. The pieces, each measured on its own:
     *  the warm server reuses the 110-token instruction prefix and skips the model load
     *  (about 85s -> 73s), the one-line grammar saves output tokens, and 480px sends about
     *  135 image tokens instead of 240. 448px (about 98-120 tokens) was 41.0s but misread the
     *  PAN on the smallest card, so do not go lower. 512px was 50.1s, 20/20, with more margin -
     *  change qwenPanCardEdge if 480 proves too thin on real scans. The image-token cap
     *  stays at the 256 baseline and ignores the 128/256 toggle (see lfm2PanImageTokenCap). */
    private fun useQwenPanProfile(docType: String, modelPath: String): Boolean =
        docType == "pan_full" && isQwenModel(modelPath)

    private val qwenPanImageTokenCap = 256
    private val qwenPanCardEdge = 480

    /** The 480px rule is only validated on tight card crops (the four test cards are 600-860px
     *  on the long edge, the card filling the frame). A camera photo with the card at 30-50% of
     *  the frame would shrink the card to a few hundred pixels of unreadable print, and the
     *  grammar would then force a PAN-shaped answer anyway. Anything larger than this is treated
     *  as a camera photo and takes the old, validated path (downscaleForQwen, 256 tokens). A
     *  stopgap until the card is detected and cropped before resizing. */
    private val qwenPanCropMaxEdge = 1000

    /** Aadhaar + Qwen3-VL: the whole picture at full size (no shrinking - fewer image tokens
     *  costs accuracy on the small print), a one-line GBNF (assets/grammars/aadhaar_qwen.gbnf),
     *  and a prompt that makes the model state explicitly whether an address block is printed
     *  (assets/prompts/user_aadhaar_qwen.txt). The grammar ties the flag to the answer:
     *  address_visible=true forces a real address string (15+ chars) and an optional PIN;
     *  false forces address and pincode to null. Mac benchmark 2026-10-07/08, 6 test cards,
     *  same 4B model: today's request (schema, no flag) 31/36 fields and the model returned a
     *  null address on 2 cards that have one; one-line grammar alone 31/36 (same misses); flag
     *  + new prompt 35/36 with the flag right on all 6 cards. The one miss is a name read
     *  "Jasjit" for "Jasjot", which reproduces in every configuration (the model's own reading).
     *  The address-or-null decision is borderline and flipped between the Mac and the phone on
     *  one card, so this must be confirmed on the phone. Image-token cap stays at the 1024 that
     *  imageTokenCapFor() gives every non-PAN type: the tallest test card (about 1,600 tokens
     *  at full size) is capped to roughly 1,000 and still read 6/6 on the Mac. */
    private fun useQwenAadhaarProfile(docType: String, modelPath: String): Boolean =
        docType == "aadhaar" && isQwenModel(modelPath)

    /** One server cap for every Qwen doc type, so switching between PAN and Aadhaar never
     *  restarts the server (the cap is a start-up setting, see capAffectsServer). Sizing is
     *  done in the app per doc type instead: the PAN profile sends a 480px card, or a photo
     *  pre-sized to the 256-token budget (downscaleForQwenPan), so this ceiling is not binding
     *  for it, and Aadhaar needs up to 1024. */
    private val qwenServerImageTokenCap = 1024

    /** Identifies one instruction prompt (system message if any + user text) so the server
     *  warm-up and a real scan agree on whether that prefix is already cached. */
    private fun warmKey(useSystem: Boolean, promptText: String): Int =
        (if (useSystem) systemPrompt else "").hashCode() * 31 + promptText.hashCode()

    /** Resize for the Qwen pan_full profile - see useQwenPanProfile(). */
    private fun downscaleForQwenPan(imageFile: File, tokenCap: Int): File {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imageFile.absolutePath, bounds)
        if (bounds.outWidth <= 0 || bounds.outHeight <= 0) return imageFile
        return if (maxOf(bounds.outWidth, bounds.outHeight) > qwenPanCropMaxEdge)
            // Pre-sized to exactly the token budget (headroom 1.0): the server now runs at one
            // 1024 cap for all doc types, so the model no longer does this shrink for us.
            downscaleForQwen(imageFile, tokenCap, headroom = 1.0)
        else
            downscaleForMiniCpm(imageFile, qwenPanCardEdge, "qwen_resized.jpg")
    }

    /** The LFM2 grammar has no document_type key (the BUL2 prompt does not ask for it) but
     *  validatePan() requires document_type == "pan". It is added only for the check; the
     *  output shown to the user is the model's own, unmodified text. The Qwen PAN and
     *  Aadhaar grammars are the same shape (no document_type), so they use this too. */
    private fun withDocumentType(text: String, type: String): String {
        val start = text.indexOf('{')
        val end = text.lastIndexOf('}')
        if (start < 0 || end <= start) return text
        return try {
            val obj = org.json.JSONObject(text.substring(start, end + 1))
            if (!obj.has("document_type")) obj.put("document_type", type)
            obj.toString()
        } catch (e: Exception) { text }
    }

    /** MiniCPM-V only: its adaptive slicer (mtmd_image_preprocessor_llava_uhd, the
     *  minicpmv-specific branch) splits the image into roughly ceil(width*height / 448^2)
     *  tiles (hardcoded max 9, see mtmd-image.cpp), each costing ~12-13s through the
     *  CPU-only vision encoder - confirmed on-device 2026-09-29: a 7-tile photo spent
     *  92.7s of a 102.2s total run purely on vision encoding. Capping the longest edge
     *  before sending cuts tile count directly (unlike the app's image-token-cap toggle,
     *  which is a confirmed no-op for this model - see imageTokenCapFor). 896px (2x the
     *  448 tile size) was tried first: consistent ~4 tiles, ~60s total, not aggressive
     *  enough. 560px (1.25x tile size) targets 1-2 tiles instead - NOT yet validated for
     *  whether it costs legibility on small print (the PAN number) at typical
     *  camera-capture resolution; needs on-device accuracy re-verification, same caution
     *  as every other untested cap value in this file.
     *  Writes to a separate cache file rather than overwriting imageFile, since other
     *  models keep using the original, full-resolution photo. */
    private fun downscaleForMiniCpm(
        imageFile: File, longestEdge: Int = 560, outName: String = "minicpm_resized.jpg"
    ): File {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imageFile.absolutePath, bounds)
        val w = bounds.outWidth
        val h = bounds.outHeight
        if (w <= 0 || h <= 0 || maxOf(w, h) <= longestEdge) return imageFile

        val scale = longestEdge.toFloat() / maxOf(w, h)
        val targetW = (w * scale).toInt().coerceAtLeast(1)
        val targetH = (h * scale).toInt().coerceAtLeast(1)

        var inSampleSize = 1
        while (w / (inSampleSize * 2) >= targetW && h / (inSampleSize * 2) >= targetH) {
            inSampleSize *= 2
        }
        val decoded = BitmapFactory.decodeFile(
            imageFile.absolutePath,
            BitmapFactory.Options().apply { this.inSampleSize = inSampleSize }
        ) ?: return imageFile
        val scaled = Bitmap.createScaledBitmap(decoded, targetW, targetH, true)
        if (scaled !== decoded) decoded.recycle()

        val outFile = File(cacheDir, outName)
        FileOutputStream(outFile).use { out -> scaled.compress(Bitmap.CompressFormat.JPEG, 92, out) }
        scaled.recycle()
        return outFile
    }

    /** LFM2-VL: the model resizes to its own ~131,072-pixel budget (128 tokens) anyway, so a
     *  12MP camera photo only costs extra JPEG decode and upload time. 1280px is well above
     *  what it keeps (about 460x290 for a card), so this never removes detail it would use. */
    private fun downscaleForLfm2(imageFile: File): File =
        downscaleForMiniCpm(imageFile, 1280, "lfm2_resized.jpg")

    /** Qwen3-VL: the model resizes every image to its own pixel budget (tokenCap x 32x32 px,
     *  since patch 16 x 2x2 merge) however large it arrives, so a camera photo only adds JPEG
     *  decode and resize time before the encoder starts. Measured on the 4B 2026-10-06: image
     *  encoding began at 8.4s for a 4000x2502 photo against 3.5s for an 860x538 card, with
     *  the same 240 image tokens either way (about 5s). Only images holding more than TWICE
     *  the budget are shrunk, and only down to twice the budget, so the model still resamples
     *  from more pixels than it keeps and never sees less than it would have. Do NOT push this
     *  toward the budget itself: sending the card at 448px (about 120 tokens) was 22s faster
     *  but misread a PAN number on the smallest test card (2 of its 10 characters, still a
     *  valid PAN format). The 860x538 test cards are below the threshold and pass through
     *  untouched. Not validated end-to-end on real camera photos. */
    private fun downscaleForQwen(
        imageFile: File, tokenCap: Int, outName: String = "qwen_resized.jpg", headroom: Double = 2.0
    ): File {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imageFile.absolutePath, bounds)
        val w = bounds.outWidth
        val h = bounds.outHeight
        if (w <= 0 || h <= 0) return imageFile
        val keepPixels = headroom * tokenCap * 32 * 32
        val pixels = w.toDouble() * h
        if (pixels <= keepPixels) return imageFile
        val scale = Math.sqrt(keepPixels / pixels)
        return downscaleForMiniCpm(imageFile, (maxOf(w, h) * scale).toInt(), outName)
    }

    /** A field region as fractions of the FRONT of the card (0..1), so it holds for any
     *  card size. Bands are deliberately generous: a first version with tight boxes clipped
     *  the PAN number on one test card and the DOB / father's name on another, and the
     *  model then returned null or INVENTED a value instead of failing. */
    private data class Band(val x0: Float, val y0: Float, val x1: Float, val y1: Float)

    private val panFrontBands = listOf(
        Band(0.20f, 0.30f, 0.72f, 0.52f),  // "Permanent Account Number Card" + PAN number
        Band(0.00f, 0.52f, 0.50f, 0.68f),  // name
        Band(0.00f, 0.66f, 0.50f, 0.82f),  // father's name
        Band(0.00f, 1.00f - 0.20f, 0.50f, 1.00f)  // date of birth
    )

    /** pan_full + MiniCPM-V only. MiniCPM-V's slicer always makes >= 3 tiles for any image
     *  over 448px on a side (~7s each), but a single 448x448 image is exactly one tile.
     *  So instead of shrinking the whole photo and letting the slicer cut it (two
     *  resamples, and tiles spent on the QR code / background / back of the card), cut the
     *  field regions from the ORIGINAL pixels, stack them into one 448x448 canvas (one
     *  resample, about 1.0-1.35x) and send that.
     *  Measured in the adb test harness 2026-09-30 on 5 clean front-card crops, 25 runs:
     *  avg 22.4s vs ~41s, 92% of fields / 60% of documents correct (see
     *  MINICPM_LATENCY_FINDINGS.md). NOT validated on real camera photos.
     *  ASSUMES [imageFile] is the front of the card and nothing else - the bands are
     *  fractions of the whole picture, so a photo with the card small in the frame, or
     *  both sides in one image, puts them on the wrong text. Returns the file to send. */
    private fun buildFieldCanvasFile(imageFile: File, size: Int = 448): File {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imageFile.absolutePath, bounds)
        var sample = 1
        // Keep the decode small enough for memory but never below ~1600px on the long edge:
        // the bands are cut from real pixels, that is the whole point.
        while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= 1600) sample *= 2
        val card = BitmapFactory.decodeFile(
            imageFile.absolutePath, BitmapFactory.Options().apply { inSampleSize = sample }
        ) ?: return imageFile

        val out = Bitmap.createBitmap(size, size, Bitmap.Config.ARGB_8888)
        val canvas = Canvas(out).apply { drawColor(Color.WHITE) }
        val paint = Paint(Paint.FILTER_BITMAP_FLAG or Paint.ANTI_ALIAS_FLAG)
        var y = 0f
        for (b in panFrontBands) {
            val x0 = (b.x0 * card.width).roundToInt().coerceIn(0, card.width - 1)
            val y0 = (b.y0 * card.height).roundToInt().coerceIn(0, card.height - 1)
            val x1 = (b.x1 * card.width).roundToInt().coerceIn(x0 + 1, card.width)
            val y1 = (b.y1 * card.height).roundToInt().coerceIn(y0 + 1, card.height)
            val crop = Bitmap.createBitmap(card, x0, y0, x1 - x0, y1 - y0)
            val scale = minOf(size.toFloat() / crop.width, 1.35f)
            val dw = crop.width * scale
            val dh = crop.height * scale
            canvas.drawBitmap(crop, null, RectF(0f, y, dw, y + dh), paint)
            y += dh + 3f
            crop.recycle()
        }
        card.recycle()

        val outFile = File(cacheDir, "field_canvas.jpg")
        FileOutputStream(outFile).use { o -> out.compress(Bitmap.CompressFormat.JPEG, 95, o) }
        out.recycle()
        // Kept next to run_log.txt so a bad crop can be inspected after the fact.
        try { outFile.copyTo(File(getExternalFilesDir(null), "last_canvas.jpg"), overwrite = true) }
        catch (_: Exception) {}
        return outFile
    }

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

    /** Qwen3-VL: keep the model's threads on the four performance cores only (Cortex-A78,
     *  cores 4-7 on the Exynos 1380; cores 0-3 are the slow A55s - see the -Cr comment in
     *  runOnePass). Running on the efficiency cores too was tried and is slower. The
     *  -Cr/--cpu-strict flags do nothing in this build (GGML_OPENMP=ON: the mask is computed
     *  and never applied, QWEN3_VL_TWEAKS_LOG.md section 1), so OpenMP is told directly.
     *  In the adb harness 2026-10-06 (4B, same two variables) this changed no timing: the
     *  scheduler was already keeping the 4 threads on the fast cores. So it is a guard against
     *  a thread drifting onto an efficiency core, not a speed-up. Hard-coded for this chipset,
     *  like the -Cr flags - revisit before running on a different phone. */
    private fun applyPerformanceCoreAffinityEnv(pb: ProcessBuilder) {
        pb.environment()["OMP_PLACES"] = "{4},{5},{6},{7}"
        pb.environment()["OMP_PROC_BIND"] = "true"
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
     *  either way, so a stop message can still report how long it ran for.
     *  diag is a short, human-readable diagnostics string written into run_log.txt
     *  alongside the result: image dimensions actually sent, whether the server was
     *  cold or warm, and (server mode) the upstream usage token counts. The token
     *  counts matter because prompt_tokens is dominated by vision tokens, which is
     *  the closest available proxy for how many 448px tiles the slicer produced. */
    private data class PassResult(
        val rawOutput: String?, val elapsedSec: Double, val diag: String = ""
    )

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
        tokenCap: Int = 256,
        // LFM2 pan_full: a hand-written GBNF (--grammar-file) instead of the JSON schema
        // (-jf). null for every other model/doc type, which keep -jf exactly as before.
        grammarText: String? = null
    ): PassResult {
        val isPaddleOcr = isPaddleOcrModel(modelPath)
        val args = mutableListOf(
            execPath.absolutePath,
            "-m", modelPath,
            "--mmproj", mmprojPath
        )
        if (isPaddleOcr) args += "--jinja"
        if (grammarText != null) {
            val grammarFile = File(cacheDir, "extract_grammar.gbnf")
            grammarFile.writeText(grammarText)
            args += listOf("--grammar-file", grammarFile.absolutePath)
        } else if (useGrammar) {
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
        // keeps using -sys as its own message exactly as before. MiniCPM-V does NOT need
        // this fold (or --jinja at all) on the CLI path specifically - confirmed on-device
        // 2026-09-29 via a direct A/B (adb shell, bypassing the app): --jinja+fold and
        // plain -sys/-p produced byte-identical, correct output on the same image, and
        // only the --jinja run's chat-template log showed the <think> wrapper - meaning
        // the CLI's default templating (used when --jinja is absent) never invokes
        // MiniCPM's thinking-mode chat template in the first place. --jinja/fold/
        // enable_thinking:false are still needed in the SERVER-mode path
        // (runOnePassServer) - llama-server's OpenAI-style endpoint requires --jinja to
        // render a messages array through the model's real template at all, which is
        // where the image-marker-corruption and thinking-mode bugs actually showed up.
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
            "-fa", "on",
            // llama.cpp runs an extra warm-up forward pass on every model load by
            // default. Measured on-device 2026-09-29 with MiniCPM-V 4.6 / pan_full:
            // 58.6s with it vs 54.5s without (-4.1s), and byte-identical output under
            // --temp 0. Only costs anything on the CLI path, which reloads the model
            // every run - a warm server pays it once at startup and is unaffected.
            "--no-warmup"
        )
        // MiniCPM-V only: with no -c, llama.cpp uses the model's full 262144-token context
        // and reserves a ~3 GB KV cache up front (1.9s just to allocate). Real usage is
        // ~400 tokens, so cap it. Measured on-device 2026-09-30, MiniCPM-V 4.6 / pan_full,
        // same card, byte-identical output: 44.3s -> 38.1s (crop), 48.2s -> 42.5s avg
        // over 4 camera-frame simulations. (-fit off was also tried: no gain, not used.)
        if (isMiniCpmModel(modelPath)) args += listOf("-c", "4096")
        // LFM2-VL: the GGUF's default context is 128,000 tokens, so the same unneeded KV
        // allocation applies. Real prompts are 190-260 tokens; every LFM2 measurement in
        // LFM2_VL_3B_LATENCY_FINDINGS.md ran with -c 4096.
        if (isLfm2Model(modelPath)) args += listOf("-c", "4096")
        // Qwen3-VL: same unneeded KV allocation, and on the 4B it also swapped - see isQwenModel().
        if (isQwenModel(modelPath)) args += listOf("-c", "4096")
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
        if (isQwenModel(modelPath)) applyPerformanceCoreAffinityEnv(pb)
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
        return PassResult(
            if (userStopped) null else rawOutput, elapsedSec,
            "img=${imageDims(imageFile)} ${cliDiag(rawOutput)}"
        )
    }

    /** Pulls llama.cpp's own per-stage timing summary out of the CLI's merged
     *  stdout+stderr so the run log records where the wall-clock actually went,
     *  instead of one opaque total. The binary already prints these
     *  ("llama_perf_context_print: prompt eval time = ... / N tokens (... tok/s)"
     *  and the matching "eval time" line) on every run; extractAnswer() strips them
     *  as preamble, so before this they were parsed and thrown away. prompt eval is
     *  where vision encoding lands, which is the split needed to tell "too many
     *  vision tiles" apart from "too many generated tokens" - the two call for
     *  opposite fixes. Returns a short one-line summary, or a marker if the binary
     *  didn't emit the block. */
    private fun cliDiag(raw: String?): String {
        if (raw.isNullOrEmpty()) return "perf: no output"
        val lines = raw.lineSequence().filter { it.contains("llama_perf_context_print") }.toList()
        if (lines.isEmpty()) return "perf: not reported by binary"
        fun grab(label: String): String {
            val l = lines.firstOrNull { it.contains(label) } ?: return "$label=?"
            val ms = Regex("= *([0-9.]+) ms").find(l)?.groupValues?.get(1) ?: return "$label=?"
            val n = Regex("/ *([0-9]+) (tokens|runs)").find(l)?.groupValues?.get(1) ?: return "$label=?"
            return "$label=${ms}ms/$n"
        }
        return "perf: ${grab("prompt eval time")} ${grab("eval time")}"
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
    ) {
        /** warmKey()s of the instruction prompts this server has already been sent (by the
         *  launch warm-up or by a real scan), so a warm-up is never repeated for a prompt whose
         *  prefix the server already has cached. A set, not a flag: PAN and Aadhaar share one
         *  server but have different instruction text. */
        val warmedKeys: MutableSet<Int> = java.util.concurrent.ConcurrentHashMap.newKeySet()
    }

    /** Number of warm-up requests in flight; a Run tapped while this is above zero queues
     *  behind them on the server's single slot, and says so on screen. */
    private val warmingUpCount = java.util.concurrent.atomic.AtomicInteger(0)

    /** Whether a change to the image-token cap should force the server to restart.
     *
     *  The cap is applied as a STARTUP env var (applyImageTokenCapEnv on the server's
     *  ProcessBuilder), not a per-request field - so today, flipping pan_full's 128/256
     *  toggle tears down a running server and pays a full model reload. Measured
     *  2026-09-29: that reload alone is ~24.6s.
     *
     *  But the cap is only actually READ by models that route their image sizing
     *  through set_limit_image_tokens() - i.e. Qwen's dyn_size preprocessor. MiniCPM-V's
     *  llava_uhd/minicpmv slicer and PaddleOCR's area-based one never read
     *  LLAMA_ARG_IMAGE_MAX_TOKENS at all (same reason MTMD_MAX_LONGEST_EDGE is gated
     *  off for them). Restarting for a setting the model ignores is a pure 24.6s
     *  waste, so only treat the cap as restart-worthy for models that use it. */
    private fun capAffectsServer(modelPath: String): Boolean =
        !isPaddleOcrModel(modelPath) && !isMiniCpmModel(modelPath)

    @Volatile private var serverState: ServerState? = null
    private val serverLock = Object()

    private fun findFreePort(): Int = java.net.ServerSocket(0).use { it.localPort }

    /** What ensureServerRunning() hands back so a run can be attributed to either a
     *  reused (warm) server or a freshly-started (cold) one. This distinction was
     *  invisible in run_log.txt and turned out to explain the whole latency
     *  bimodality: server-mode runs cluster at either ~45-50s (warm reuse, no model
     *  load) or ~80-103s (cold start, full 529MB LM + 728MB mmproj load off flash).
     *  coldStartSec is the time actually spent inside the /health readiness poll
     *  above, i.e. spawn + model load + backend init. */
    private class ServerHandle(val port: Int, val coldStart: Boolean, val coldStartSec: Double)

    /** Reports the pixel dimensions of an image file without decoding its full
     *  contents, for the run log. Cheap (bounds-only decode) and the single most
     *  useful number for reasoning about MiniCPM-V's vision cost: its slicer picks
     *  tile count purely from width*height vs 448^2 (mtmd-image.cpp:684-687), so
     *  seeing the actual dimensions makes the tile count derivable from the log
     *  instead of guessed at. */
    private fun imageDims(f: File): String {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(f.absolutePath, o)
        return if (o.outWidth > 0) "${o.outWidth}x${o.outHeight}" else "unknown"
    }

    /** Starts llama-server if it isn't already running with this exact model/mmproj/
     *  threads/GPU combination - those became server-startup-time settings under this
     *  mode instead of per-request flags, so a change to any of them means the old
     *  process must be torn down and a new one started before the next request. Blocks
     *  the calling (already-background) thread until the server answers /health or
     *  startup fails. Returns the port to talk to. */
    private fun ensureServerRunning(
        execPath: File, modelPath: String, mmprojPath: String, threads: Int,
        useGpu: Boolean, imageTokenCap: Int
    ): ServerHandle {
        // See capAffectsServer() - for MiniCPM-V/PaddleOCR the cap is inert, so it must
        // not be part of the reuse comparison or every toggle press costs a full reload.
        val capIsStartupSetting = capAffectsServer(modelPath)
        synchronized(serverLock) {
            val existing = serverState
            if (existing != null && existing.process.isAlive &&
                existing.modelPath == modelPath && existing.mmprojPath == mmprojPath &&
                existing.threads == threads && existing.useGpu == useGpu &&
                (!capIsStartupSetting || existing.imageTokenCap == imageTokenCap)) {
                return ServerHandle(existing.port, coldStart = false, coldStartSec = 0.0)
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
            val isMiniCpm = isMiniCpmModel(modelPath)
            val isLfm2 = isLfm2Model(modelPath)
            if (isPaddleOcr || isMiniCpm || isLfm2) args += "--jinja"
            // Same 262144-token default-context KV waste as runOnePass() - see comment there.
            if (isMiniCpm) args += listOf("-c", "4096")
            // LFM2-VL: the exact server configuration measured in
            // LFM2_VL_3B_LATENCY_FINDINGS.md section 10 (33.3s per scan warm against 35.7s cold
            // CLI, identical output): -c 4096, one slot, no warm-up decode at startup.
            if (isLfm2) args += listOf("-c", "4096", "-np", "1", "--no-warmup")
            // Qwen3-VL: the LFM2 server configuration above (one slot, -c 4096, no warm-up) for the
            // same reason - see isQwenModel(). Timed in server mode on the 4B 2026-10-06: see
            // useQwenPanProfile(). The first request after a start is slow (~85s) because the
            // weights are paged in lazily under --no-warmup; warmUpQwenServer() absorbs that.
            // --cache-ram: llama-server keeps each finished prompt's KV state in RAM for
            // reuse, up to 8192 MiB by default - more than this phone has. Each PAN scan adds
            // roughly 40 MiB (about 260 tokens at ~150 KB/token), so a long session would
            // grow into the 3.5 GB the model already holds. 512 MiB keeps the last dozen or so.
            if (isQwenModel(modelPath)) args += listOf("-c", "4096", "-np", "1", "--no-warmup", "--cache-ram", "512")

            val pb = ProcessBuilder(args)
            pb.environment()["LD_LIBRARY_PATH"] = applicationInfo.nativeLibraryDir
            // PaddleOCR-VL only - see the matching guard in runOnePass() for why this
            // can't also run for Qwen.
            if (isPaddleOcr) applyDynSizeEnv(pb)
            applyImageTokenCapEnv(pb, imageTokenCap)
            if (isQwenModel(modelPath)) applyPerformanceCoreAffinityEnv(pb)
            if (!isPaddleOcr && !isMiniCpm && !isLfm2) {
                // The actual fix under test - caps the vision-preprocessing upscale
                // target at 768px instead of the model's hard-coded 2048px default. See
                // LATENCY_OPTIMIZATION.md section 2, Stage 3, for why this is what lets
                // a small crop get proportionally fewer tiles instead of always being
                // upscaled to the same canvas size first. Only meaningful to VisionPsy's
                // idefics3 preprocessor - PaddleOCR-VL and MiniCPM-V both use their own
                // different preprocessors (area-based / minicpmv4_6 slicing respectively)
                // that don't read this var at all.
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
            val coldStartNanos = System.nanoTime()
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
            val coldStartSec = (System.nanoTime() - coldStartNanos) / 1_000_000_000.0
            return ServerHandle(port, coldStart = true, coldStartSec = coldStartSec)
        }
    }

    private fun stopServer() {
        synchronized(serverLock) {
            serverState?.process?.destroyForcibly()
            serverState = null
        }
    }

    /** Qwen3-VL pan_full only: starts the server as soon as a Qwen model is selected (and
     *  again whenever the app is reopened with it selected) and sends it one throwaway scan
     *  of a blank card, so the person's first real scan is already warm. Measured on the 4B
     *  2026-10-06: the first request after a server start took about 85s against 41-50s warm.
     *  The server runs --no-warmup, so its weights are paged in from flash lazily on that first
     *  request, and the instruction prefix is not cached yet; the blank scan pays both here.
     *  Holds the model's ~3.5 GB of RAM for as long as the app is open. A real Run tapped
     *  meanwhile is not lost: it queues behind the warm-up on the server's single slot. Every
     *  failure is shown as text and swallowed - the real scan retries the same start itself. */
    private fun warmUpQwenServer(retryOnFailure: Boolean = true) {
        if (!binding.switchServerMode.isChecked) return
        val modelName = binding.spinnerModel.selectedItem as? String ?: return
        val mmprojName = binding.spinnerMmproj.selectedItem as? String ?: return
        val docType = binding.spinnerDocType.selectedItem as? String ?: return
        // PAN and Aadhaar share one server (qwenServerImageTokenCap) but have different
        // instruction text, so each is warmed once - see ServerState.warmedKeys.
        if (docType != "pan_full" && docType != "aadhaar") return
        val modelPath = File(modelsDir(), modelName).absolutePath
        if (!isQwenModel(modelPath)) return
        // The projector must belong to this model (4B with 4B, 2B with 2B); a mismatched pair
        // cannot load, and the run itself reports that.
        if (!mmprojName.contains(modelName.substringBefore("-Instruct"), ignoreCase = true)) return
        val mmprojPath = File(modelsDir(), mmprojName).absolutePath
        val execPath = File(applicationInfo.nativeLibraryDir, "libllama-server.so")
        if (!execPath.exists()) return
        val useGpu = binding.switchGpu.isChecked
        val threads = binding.seekThreads.progress + 1
        val statusPrefix = "Qwen3-VL warm-up"

        Thread {
            var warmedState: ServerState? = null
            var warmedKeyUsed = 0
            val counted = java.util.concurrent.atomic.AtomicBoolean(false)
            try {
                // Only claim the screen if no run is in progress; a result already on screen
                // is replaced by this, but it is saved in run_log.txt either way.
                ui.post {
                    if (!binding.btnStop.isEnabled)
                        binding.tvOutput.text = "$statusPrefix: loading the model (about a minute). " +
                            "Scans are fast once this finishes."
                }
                val startNanos = System.nanoTime()
                ensureServerRunning(execPath, modelPath, mmprojPath, threads, useGpu, qwenServerImageTokenCap)
                val state = serverState ?: return@Thread
                // Exactly the request shape the real scan will send: PAN has no system message,
                // Aadhaar does (systemPrompt), then the instruction text, then the image.
                val useSystem = docType == "aadhaar"
                val userText =
                    if (docType == "aadhaar") loadAsset("prompts/user_aadhaar_qwen.txt") else promptFor("pan_full")
                val warmGrammar =
                    loadAsset(if (docType == "aadhaar") "grammars/aadhaar_qwen.gbnf" else "grammars/pan_full_qwen.gbnf")
                val key = warmKey(useSystem, userText)
                if (!state.warmedKeys.add(key)) return@Thread
                warmedState = state; warmedKeyUsed = key
                warmingUpCount.incrementAndGet(); counted.set(true)
                val loadedSec = (System.nanoTime() - startNanos) / 1_000_000_000.0

                val blank = File(cacheDir, "warmup_blank.jpg")
                if (!blank.exists()) {
                    val bmp = Bitmap.createBitmap(qwenPanCardEdge, qwenPanCardEdge * 5 / 8, Bitmap.Config.ARGB_8888)
                    Canvas(bmp).drawColor(Color.WHITE)
                    FileOutputStream(blank).use { bmp.compress(Bitmap.CompressFormat.JPEG, 90, it) }
                    bmp.recycle()
                }
                val b64 = android.util.Base64.encodeToString(blank.readBytes(), android.util.Base64.NO_WRAP)
                // Same request shape as runOnePassServer() for pan_full on Qwen (instructions
                // first, then the image) - the point is to leave the identical instruction
                // prefix in the server's cache. Output is discarded, so it is kept short.
                val content = org.json.JSONArray()
                    .put(org.json.JSONObject().put("type", "text").put("text", userText))
                    .put(org.json.JSONObject().put("type", "image_url").put("image_url",
                        org.json.JSONObject().put("url", "data:image/jpeg;base64,$b64")))
                val messages = org.json.JSONArray()
                if (useSystem) messages.put(org.json.JSONObject().put("role", "system").put("content", systemPrompt))
                messages.put(org.json.JSONObject().put("role", "user").put("content", content))
                val payload = org.json.JSONObject().apply {
                    put("messages", messages)
                    put("temperature", 0)
                    put("repeat_penalty", 1.1)
                    put("max_tokens", 24)
                    put("grammar", warmGrammar)
                    put("chat_template_kwargs", org.json.JSONObject().put("enable_thinking", false))
                }
                val requestStart = System.nanoTime()
                val conn = java.net.URL("http://127.0.0.1:${state.port}/v1/chat/completions")
                    .openConnection() as java.net.HttpURLConnection
                conn.requestMethod = "POST"
                conn.doOutput = true
                conn.connectTimeout = 10_000
                conn.readTimeout = 300_000
                conn.setRequestProperty("Content-Type", "application/json")
                conn.outputStream.use { it.write(payload.toString().toByteArray(Charsets.UTF_8)) }
                val code = conn.responseCode
                val body = (if (code in 200..299) conn.inputStream else conn.errorStream)
                    .bufferedReader().use { it.readText() }
                if (code !in 200..299) throw IllegalStateException("HTTP $code: $body")
                val requestSec = (System.nanoTime() - requestStart) / 1_000_000_000.0
                val totalSec = (System.nanoTime() - startNanos) / 1_000_000_000.0

                val summary = "server up %.1fs, warm-up scan %.1fs, total %.1fs".format(loadedSec, requestSec, totalSec)
                runLogFile().appendText("=== $statusPrefix | ${java.util.Date()} ===\n$summary\n\n")
                ui.post {
                    // Replace only our own message - never a result or a run in progress.
                    if (binding.tvOutput.text.startsWith(statusPrefix))
                        binding.tvOutput.text = "$statusPrefix done ($summary). Qwen3-VL is ready."
                }
            } catch (e: Exception) {
                // Forget the key so a retry (or the next doc-type change) can warm this prompt again.
                warmedState?.warmedKeys?.remove(warmedKeyUsed)
                ui.post {
                    if (binding.tvOutput.text.startsWith(statusPrefix))
                        binding.tvOutput.text = "$statusPrefix failed: ${e.message}" +
                            (if (retryOnFailure) " - trying once more in 10 s" else "")
                }
                if (retryOnFailure) {
                    try { Thread.sleep(10_000) } catch (_: InterruptedException) { }
                    ui.post { warmUpQwenServer(retryOnFailure = false) }
                }
            } finally {
                if (counted.get()) warmingUpCount.decrementAndGet()
            }
        }.start()
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
        tokenCap: Int = 256,
        // LFM2 pan_full: sent as the request's raw "grammar" field (same GBNF the CLI path
        // gets via --grammar-file) instead of json_schema. null for everything else.
        grammarText: String? = null
    ): PassResult {
        // Taken BEFORE ensureServerRunning() on purpose. The per-request timer below
        // deliberately starts after it, because that is the number the live stats card
        // and the old run_log entries were reporting - and it is the right number for
        // "how long does an extraction take once the model is resident". But it means
        // a COLD start's model load was never counted anywhere: the pre-existing
        // "server mode | 102.8s" entries in run_log.txt are request time only, so their
        // real wall-clock cost was up to a full model load higher and the log gave no
        // way to tell the two apart. totalStartNanos captures the honest end-to-end
        // figure so both numbers are now visible side by side.
        val totalStartNanos = System.nanoTime()
        // Qwen: one start-up cap for every doc type so PAN <-> Aadhaar never restarts the server;
        // images are sized in the app per doc type instead (see qwenServerImageTokenCap).
        val serverCap = if (isQwenModel(modelPath)) qwenServerImageTokenCap else tokenCap
        val server = ensureServerRunning(execPath, modelPath, mmprojPath, threads, useGpu, serverCap)
        val port = server.port
        val pid = serverState?.pid
        // A real scan also leaves this prompt's prefix in the server's cache, so a later warm-up
        // for the same prompt would be wasted work.
        serverState?.warmedKeys?.add(
            warmKey(useSystemPrompt && !(isPaddleOcrModel(modelPath) || isMiniCpmModel(modelPath)), promptText))
        val startNanos = System.nanoTime()
        val monitor = if (pid != null) startMonitoring(pid, startNanos) else MonitorHandle()

        // Same --jinja image-marker corruption as runOnePass()'s isJinjaModel fold-in (see
        // its comment): a separate system message renders fine as JSON here too, but goes
        // through the same GGUF chat-template rendering path server-side, so it was never
        // actually exempt - this server path just hadn't been tested against a model that
        // hits it until MiniCPM-V's first server-mode run came back "no JSON object found"
        // 2026-09-29. Fold for the same model set as the CLI path.
        val isJinjaModel = isPaddleOcrModel(modelPath) || isMiniCpmModel(modelPath)
        val messages = org.json.JSONArray()
        if (useSystemPrompt && !isJinjaModel) {
            messages.put(org.json.JSONObject().apply {
                put("role", "system")
                put("content", systemPrompt)
            })
        }
        val b64 = android.util.Base64.encodeToString(imageFile.readBytes(), android.util.Base64.NO_WRAP)
        val effectivePrompt =
            if (isJinjaModel && useSystemPrompt) "$systemPrompt\n\n$promptText" else promptText
        val isLfm2 = isLfm2Model(modelPath)
        val textPart = org.json.JSONObject().apply { put("type", "text"); put("text", effectivePrompt) }
        val imagePart = org.json.JSONObject().apply {
            put("type", "image_url")
            put("image_url", org.json.JSONObject().put("url", "data:image/jpeg;base64,$b64"))
        }
        // LFM2-VL: image FIRST, instructions after - the order every validated LFM2 result
        // used. Instructions-first lost a field (22/25 vs 23/25, an invented PAN on card a)
        // and saved no time: this server build cached 1 token of ~190 either way.
        val contentArr =
            if (isLfm2) org.json.JSONArray().put(imagePart).put(textPart)
            else org.json.JSONArray().put(textPart).put(imagePart)
        messages.put(org.json.JSONObject().apply { put("role", "user"); put("content", contentArr) })

        val payload = org.json.JSONObject().apply {
            put("messages", messages)
            put("temperature", 0)
            // Matches the CLI path's --repeat-penalty 1.1 (llama.cpp defaults this to
            // 1.0/disabled - confirmed in common/common.h - so it's genuinely off unless
            // set here explicitly).
            put("repeat_penalty", 1.1)
            put("max_tokens", maxTokens)
            if (grammarText != null) {
                put("grammar", grammarText)
            } else if (useGrammar && schemaJson.isNotBlank()) {
                put("json_schema", org.json.JSONObject(schemaJson))
            }
            // MiniCPM-V 4.6's chat template defaults to thinking mode ("thinking = 1" in
            // the server's own startup log) - confirmed on-device 2026-09-29: every
            // pan_full request hit the full max_tokens=512 ceiling with the model still
            // inside its <think> block, so the actual JSON answer either barely made it
            // out (slow but valid) or got truncated entirely ("no JSON object found"),
            // depending on how long that image's reasoning ran. server-common.cpp reads
            // this as a JSON bool (via .dump(), not a string) and overrides the
            // template's default per-request. Sent unconditionally - harmless for models
            // whose template doesn't support thinking, and there's no reason any of these
            // structured-extraction doc types would want reasoning tokens anyway.
            // Not sent for LFM2: its template has no thinking mode, and the validated
            // server runs did not send it.
            if (!isLfm2) put("chat_template_kwargs", org.json.JSONObject().put("enable_thinking", false))
        }

        var rawOutput: String? = null
        var usageSummary = "usage: unavailable"
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

            // llama-server returns an OpenAI-style "usage" block on every request.
            // prompt_tokens is dominated by the vision tokens produced by the image
            // preprocessor, so it is the only direct, per-run measurement of how much
            // image the model actually ingested - previously nothing recorded this, so
            // a latency change from a resolution/tile change could never be confirmed
            // from the log. Absent on an error body, hence the safe fallback above.
            runCatching {
                val u = org.json.JSONObject(body).optJSONObject("usage")
                if (u != null) {
                    val pt = u.optInt("prompt_tokens", -1)
                    val ct = u.optInt("completion_tokens", -1)
                    usageSummary = "prompt_tokens=$pt completion_tokens=$ct"
                }
                // cache_n is how many prompt tokens the server reused instead of
                // recomputing (the instruction text, ~110 on a warm PAN scan); prompt_ms and
                // predicted_ms split the request into reading and writing time.
                val tm = org.json.JSONObject(body).optJSONObject("timings")
                if (tm != null) {
                    usageSummary += " cache_n=${tm.optInt("cache_n", -1)}" +
                        " prompt_ms=${tm.optDouble("prompt_ms", -1.0).toLong()}" +
                        " predicted_ms=${tm.optDouble("predicted_ms", -1.0).toLong()}"
                }
            }

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
        val serverDesc = if (server.coldStart)
            "server=COLD(started in %.1fs)".format(server.coldStartSec)
        else
            "server=WARM(reused, no model load)"
        val totalSec = (System.nanoTime() - totalStartNanos) / 1_000_000_000.0
        val diag = "img=${imageDims(imageFile)} $serverDesc $usageSummary total_wall=%.1fs".format(totalSec)
        return PassResult(if (userStopped) null else rawOutput, elapsedSec, diag)
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
        val useFieldCanvas = binding.switchFieldCanvas.isChecked
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
        // A warm-up still in flight holds the server's single slot; this scan queues behind it.
        if (warmingUpCount.get() > 0)
            binding.tvOutput.text = "Running... the model is still warming up, so this scan waits " +
                "for it to finish first (about a minute at most)."

        Thread {
            try {
                val modelPath = File(modelsDir(), modelName).absolutePath
                val mmprojPath = File(modelsDir(), mmprojName).absolutePath
                val mode = "$docType | " +
                    (if (useGpu) "GPU (LM on Vulkan, mmproj on CPU)" else "CPU only") +
                    (if (useServerMode) " | server mode" else "") +
                    // Model identity was previously absent from every logged run, which
                    // made it impossible to tell a MiniCPM-V result from a Qwen3-VL one
                    // after the fact - several conclusions in HANDOVER.md and
                    // LATENCY_OPTIMIZATION.md rest on runs that can't actually be
                    // attributed to a model. Both filenames plus the thread count now go
                    // into every run so a log line is self-describing.
                    " | model=$modelName | mmproj=$mmprojName | threads=$threads" +
                    (if (useLfm2PanProfile(docType, modelPath)) " | profile=lfm2-bul2-grammar" else "") +
                    (if (useQwenPanProfile(docType, modelPath)) " | profile=qwen-pan-oneline-${qwenPanCardEdge}px" else "") +
                    (if (useQwenAadhaarProfile(docType, modelPath)) " | profile=qwen-aadhaar-flag-fullsize" else "")
                // Unifies the two invocation paths behind one call shape so the rest of
                // this function (single-image and two-sided alike) doesn't need to
                // duplicate the branch at every call site. schemaFileName is only
                // meaningful to the CLI path (-jf needs an actual file); server mode
                // sends schemaJson directly in the request body and ignores it.
                fun runPass(
                    img: File, promptText: String, schemaJson: String, schemaFileName: String,
                    useGrammar: Boolean = true, useSystemPrompt: Boolean = true, maxTokens: Int = 512,
                    tokenCap: Int = 256, grammarText: String? = null
                ): PassResult = if (useServerMode) {
                    runOnePassServer(
                        execPath, modelPath, mmprojPath, img, promptText, schemaJson,
                        useGpu, threads, useGrammar, useSystemPrompt, maxTokens, tokenCap,
                        grammarText
                    )
                } else {
                    runOnePass(
                        execPath, modelPath, mmprojPath, img, promptText, schemaJson,
                        schemaFileName, useGpu, threads, useGrammar, useSystemPrompt, maxTokens, tokenCap,
                        grammarText
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
                    // LFM2 pan_full has its own validated profile (128 image tokens, bullet
                    // prompt, GBNF grammar) - see isLfm2Model(). Everything else is unchanged.
                    val lfm2Pan = useLfm2PanProfile(docType, modelPath)
                    val qwenPan = useQwenPanProfile(docType, modelPath)
                    val qwenAadhaar = useQwenAadhaarProfile(docType, modelPath)
                    val cap = if (lfm2Pan) lfm2PanImageTokenCap
                        else if (qwenPan) qwenPanImageTokenCap
                        else imageTokenCapFor(docType)
                    val useCanvas = useFieldCanvas && docType == "pan_full" && isMiniCpmModel(modelPath)
                    val prepStart = System.nanoTime()
                    val passImageFile = when {
                        useCanvas -> buildFieldCanvasFile(imageFile)
                        isMiniCpmModel(modelPath) -> downscaleForMiniCpm(imageFile)
                        lfm2Pan -> downscaleForLfm2(imageFile)
                        qwenPan -> downscaleForQwenPan(imageFile, cap)
                        isQwenModel(modelPath) -> downscaleForQwen(imageFile, cap)
                        else -> imageFile
                    }
                    val prepSec = (System.nanoTime() - prepStart) / 1_000_000_000.0
                    val outcome = runPass(
                        passImageFile,
                        // trim(): the validated BUL2 runs fed the prompt through shell
                        // $(cat ...), which drops the file's trailing newline.
                        if (lfm2Pan) loadAsset("prompts/user_pan_full_lfm2.txt").trim()
                        else if (qwenAadhaar) loadAsset("prompts/user_aadhaar_qwen.txt")
                        else promptFor(docType),
                        if (skipJsonContract) "" else schemaFor(docType),
                        "extract_schema.json",
                        useGrammar = !skipJsonContract,
                        useSystemPrompt = !skipJsonContract && !skipSystemPromptOnly,
                        // pan_full's own schema (document_type/pan_number/name/parent_name/
                        // parent_relation/dob) only ever needs ~70-150 tokens for a real
                        // answer (confirmed on-device 2026-09-29: a successful MiniCPM-V
                        // extraction used exactly 73) - 512 was generous headroom for other
                        // models but let MiniCPM-V's runaway thinking-mode decode burn the
                        // full budget before the enable_thinking:false fix. Kept as a safety
                        // cap now that thinking is disabled, not the primary fix for that.
                        maxTokens = if (isStage1) 1024 else if (isCrop) 64
                                    else if (docType == "pan_full") 200 else 512,
                        tokenCap = cap,
                        grammarText = if (lfm2Pan) loadAsset("grammars/pan_full_lfm2.gbnf")
                            else if (qwenPan) loadAsset("grammars/pan_full_qwen.gbnf")
                            else if (qwenAadhaar) loadAsset("grammars/aadhaar_qwen.gbnf")
                            else null
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
                    val report = if (isCrop) validateCropField(docType, cleaned)
                        else validateExtraction(docType,
                            if (lfm2Pan || qwenPan) withDocumentType(cleaned, "pan")
                            else if (qwenAadhaar) withDocumentType(cleaned, "aadhaar")
                            else cleaned)
                    val runNumber = nextRunNumber()
                    // Header is formatted on its own and the model output is concatenated
                    // afterwards, rather than interpolated into the same format string.
                    // $cleaned and $report are model-derived text: any literal '%' in them
                    // (a name, a validation note) would otherwise be read as a format
                    // specifier and throw or mangle the saved run. Pre-existing shape,
                    // but this line was already being rewritten for the diagnostics.
                    val header = "[$mode | cap=$cap | %.1fs]".format(outcome.elapsedSec)
                    // Image-prep time (canvas build or downscale) happens BEFORE the timer in
                    // runOnePass starts, so it is added back here to give the real end-to-end
                    // time a user waits for.
                    val prepDiag = "prep=${if (useCanvas) "canvas" else "downscale"} %.2fs total_incl_prep=%.1fs"
                        .format(prepSec, prepSec + outcome.elapsedSec)
                    val fullOutput = "$header\n${outcome.diag} $prepDiag\n\n$cleaned\n\n$report"
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
                // MiniCPM-V needs the same downscale here that the single-pass branch
                // above applies - without it both sides go out at full camera
                // resolution, which drives the slicer to its hardcoded 9-tile ceiling
                // (mtmd-image.cpp:684) and costs roughly 10 vision encodes instead of
                // 2-3, i.e. well over a minute per side. Previously only the
                // single-pass branch called this at all.
                val sideCap = imageTokenCapFor(docType)
                val frontImg: File = when {
                    isMiniCpmModel(modelPath) -> downscaleForMiniCpm(imageFile)
                    isQwenModel(modelPath) -> downscaleForQwen(imageFile, sideCap, "qwen_resized_front.jpg")
                    else -> imageFile
                }
                val backImg: File = when {
                    isMiniCpmModel(modelPath) -> downscaleForMiniCpm(backImageFile!!)
                    isQwenModel(modelPath) -> downscaleForQwen(backImageFile!!, sideCap, "qwen_resized_back.jpg")
                    else -> backImageFile!!
                }
                ui.post { binding.tvOutput.text = "Running front side (1/2)..." }
                val frontOutcome = runPass(
                    frontImg,
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
                    backImg,
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
                    "[$mode | %.1fs total: front %.1fs + back %.1fs]\nFRONT: %s\nBACK: %s\n\n"
                        .format(
                            totalElapsed, frontOutcome.elapsedSec, backOutcome.elapsedSec,
                            frontOutcome.diag, backOutcome.diag
                        ) +
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
        // Qwen profile only: address_visible=false means the card genuinely prints no address, so
        // address/pincode are not misses - leave them out of the graded fields (shown for
        // information instead) so a front-only card can grade HIGH.
        val addressDeclaredAbsent = obj.has("address_visible") && !obj.optBoolean("address_visible", false)
        val fields = if (addressDeclaredAbsent) listOf("name", "dob", "gender", "aadhaar_number")
                     else listOf("name", "dob", "gender", "aadhaar_number", "address", "pincode")
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

        // Qwen profile: the address_visible flag must agree with what was returned. The grammar
        // already forces these when it can; the app re-checks them so a scan that breaks the
        // contract is flagged instead of trusted. A genuine block-wide copy is also expected to
        // contain its own PIN, and an address with a PIN in it but a null pincode field is wrong.
        if (obj.has("address_visible")) {
            if (obj.optBoolean("address_visible", false)) {
                if (addr == null || addr.length < 15) {
                    problems += "address_visible is true but the address is missing or too short"
                } else {
                    val embeddedPin = Regex("\\b[0-9]{6}\\b").findAll(addr).lastOrNull()?.value
                    if (pin != null && !addr.contains(pin)) {
                        problems += "address does not contain the pincode '$pin'"
                    } else if (pin == null && embeddedPin != null) {
                        problems += "address contains the PIN '$embeddedPin' but the pincode field is null"
                    } else if (pin == null) {
                        notes += "address read, but no 6-digit PIN was found in it - check the address is complete"
                    }
                }
            } else if (addr != null || pin != null) {
                problems += "address_visible is false but an address or pincode was returned"
            }
        }

        return coverageReport(
            "Aadhaar", obj, fields, problems, notes,
            oftenNullFields = if (addressDeclaredAbsent) listOf("address", "pincode") else emptyList()
        )
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
