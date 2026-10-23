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

    private val modelRegistry: List<ModelRegistry.Entry> by lazy { ModelRegistry.parse(loadAsset("models.yaml")) }

    private val systemPrompt: String by lazy { PromptFile.body(loadAsset(PromptFile.SYSTEM)) }

    private fun loadAsset(path: String): String =
        assets.open(path).bufferedReader().use { it.readText() }

    private fun promptFor(docType: String): String = PromptFile.body(loadAsset(PromptFile.userAsset(docType)))
    private fun schemaFor(docType: String): String = loadAsset("schemas/$docType.json")

    // The lists of prompt text that must never appear in an output live in OutputParser.

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
        binding.switchServerMode.setOnCheckedChangeListener { _, serverMode ->
            // A warm server keeps the whole model in memory (about 2.2 GB for LFM2-VL). Turning server
            // mode off must not leave it running; on its own thread because a start in progress holds
            // the server lock for its whole duration.
            if (!serverMode && !binding.btnStop.isEnabled) Thread { stopServer() }.start()
            warmUpQwenServer()
        }
        refreshDeviceInfo()
        setupThreadSlider()
        setupGpuSwitch()

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
    // readable without storage permission) and writes <filesDir>/ocr_results.txt (private
    // storage: it holds recognised document text): per image the decoded size, OCR time,
    // and every recognised line with its bounding box. Read it from a debug build with
    //   adb shell run-as com.modeltesting.vlmtester cat files/ocr_results.txt
    private val ocrRecognizer by lazy {
        TextRecognition.getClient(TextRecognizerOptions.DEFAULT_OPTIONS)
    }

    private fun handleOcrIntent(i: Intent?) {
        if (i?.getBooleanExtra("ocr_run", false) != true) return
        i.removeExtra("ocr_run")
        Thread {
            try { runOcrOnFolder() }
            catch (e: Exception) {
                File(filesDir, "ocr_results.txt").writeText("OCR run failed: ${e.javaClass.simpleName}\n")
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
        File(filesDir, "ocr_results.txt").writeText(sb.toString())
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

    // The shipped llama.cpp build is CPU only (see GpuSupport). Without a GPU backend the switch
    // would silently run on the CPU while the run log said GPU, so it is switched off and says why.
    private fun setupGpuSwitch() {
        val libraries = File(applicationInfo.nativeLibraryDir).list()?.toList().orEmpty()
        if (!GpuSupport.isAvailable(libraries)) {
            binding.switchGpu.isChecked = false
            binding.switchGpu.isEnabled = false
            binding.switchGpu.text = GpuSupport.UNAVAILABLE_LABEL
        }
    }

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

    private fun formatBytes(bytes: Long): String = ByteFormat.format(bytes)

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

    // Private storage, not the external folder: the log holds extracted document fields.
    // Pull it from a debug build with: adb shell run-as com.modeltesting.vlmtester cat files/run_log.txt
    private fun runLogFile(): File = File(filesDir, "run_log.txt")

    private fun saveRun(runNumber: Int, docType: String, body: String) {
        RunLog(runLogFile()).appendRun(runNumber, docType, java.util.Date(), body)
    }

    private fun populateModelSpinners() {
        val choices = ModelFiles.choices(modelsDir().listFiles()?.map { it.name }.orEmpty())
        val lmModels = choices.languageModels
        val mmprojModels = choices.projectors

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

    // Detection of the Qwen, LFM2 and MiniCPM models by file name, and their per-document
    // settings, live in ModelProfiles (pure and unit-tested; the measurements are in its comments).
    private fun isMiniCpmModel(modelPath: String): Boolean = ModelProfiles.isMiniCpm(File(modelPath).name)

    private fun isLfm2Model(modelPath: String): Boolean = ModelProfiles.isLfm2(File(modelPath).name)

    private fun isQwenModel(modelPath: String): Boolean = ModelProfiles.isQwen(File(modelPath).name)

    private fun useLfm2PanProfile(docType: String, modelPath: String): Boolean =
        ModelProfiles.useLfm2PanProfile(docType, File(modelPath).name)

    private val lfm2PanImageTokenCap = ModelProfiles.LFM2_PAN_IMAGE_TOKEN_CAP

    private fun useQwenPanProfile(docType: String, modelPath: String): Boolean =
        ModelProfiles.useQwenPanProfile(docType, File(modelPath).name)

    private val qwenPanImageTokenCap = ModelProfiles.QWEN_PAN_IMAGE_TOKEN_CAP
    private val qwenPanCardEdge = ModelProfiles.QWEN_PAN_CARD_EDGE
    private val qwenPanCropMaxEdge = ModelProfiles.QWEN_PAN_CROP_MAX_EDGE

    private fun useQwenAadhaarProfile(docType: String, modelPath: String): Boolean =
        ModelProfiles.useQwenAadhaarProfile(docType, File(modelPath).name)

    private val qwenServerImageTokenCap = ModelProfiles.QWEN_SERVER_IMAGE_TOKEN_CAP

    /** Identifies one instruction prompt (system message if any + user text) so the server
     *  warm-up and a real scan agree on whether that prefix is already cached. */
    private fun warmKey(useSystem: Boolean, promptText: String): Int =
        (if (useSystem) systemPrompt else "").hashCode() * 31 + promptText.hashCode()

    /** Resize for the Qwen pan_full profile - see useQwenPanProfile(). */
    private fun downscaleForQwenPan(imageFile: File, tokenCap: Int): File {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imageFile.absolutePath, bounds)
        val edge = ImageSizing.qwenPanTargetEdge(bounds.outWidth, bounds.outHeight, tokenCap) ?: return imageFile
        return downscaleForMiniCpm(imageFile, edge, "qwen_resized.jpg")
    }

    private fun withDocumentType(text: String, type: String): String =
        ExtractionRules.withDocumentType(text, type)

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
        val plan = ImageSizing.resizePlan(bounds.outWidth, bounds.outHeight, longestEdge) ?: return imageFile
        val decoded = BitmapFactory.decodeFile(
            imageFile.absolutePath,
            BitmapFactory.Options().apply { this.inSampleSize = plan.inSampleSize }
        ) ?: return imageFile
        val scaled = Bitmap.createScaledBitmap(decoded, plan.targetWidth, plan.targetHeight, true)
        if (scaled !== decoded) decoded.recycle()

        val outFile = File(cacheDir, outName)
        FileOutputStream(outFile).use { out -> scaled.compress(Bitmap.CompressFormat.JPEG, 92, out) }
        scaled.recycle()
        return outFile
    }

    /** LFM2-VL: the longest edge comes from the plan (ModelProfiles.LFM2_PAN_MAX_EDGE for pan_full,
     *  LFM2_DOCUMENT_MAX_EDGE for aadhaar, dl and passport). It used to be 1280 on the assumption
     *  that the model shrinks the image itself; the libraries built from llama.cpp 000bee5 tile a
     *  large image instead, which made LFM2 about 3x slower (see the comments on those constants
     *  and docs/mobile-extraction/ON_DEVICE_LATENCY_AND_ACCURACY_FINDINGS.md). */
    private fun downscaleForLfm2(imageFile: File, longestEdge: Int): File =
        downscaleForMiniCpm(imageFile, longestEdge, "lfm2_resized.jpg")

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
        val edge = ImageSizing.qwenTargetEdge(bounds.outWidth, bounds.outHeight, tokenCap, headroom)
            ?: return imageFile
        return downscaleForMiniCpm(imageFile, edge, outName)
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
        // Kept next to run_log.txt (private storage) so a bad crop can be inspected after the fact.
        try { outFile.copyTo(File(filesDir, "last_canvas.jpg"), overwrite = true) }
        catch (_: Exception) {}
        return outFile
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
    private fun imageTokenCapFor(docType: String): Int =
        ModelProfiles.imageTokenCapFor(docType, singleFieldCropDocTypes, binding.switchPanFullHighCap.isChecked)

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
        val grammarArgs = when {
            grammarText != null -> {
                val grammarFile = File(cacheDir, "extract_grammar.gbnf")
                grammarFile.writeText(grammarText)
                listOf("--grammar-file", grammarFile.absolutePath)
            }
            useGrammar -> {
                val schemaFile = File(cacheDir, schemaFileName)
                schemaFile.writeText(schemaJson)
                listOf("-jf", schemaFile.absolutePath)
            }
            else -> emptyList()
        }
        // The CLI path sends the system prompt as its own -sys message. MiniCPM-V needs neither
        // --jinja nor a folded system prompt here - confirmed on-device 2026-09-29 via a direct
        // A/B (adb shell, bypassing the app): --jinja+fold and plain -sys/-p produced
        // byte-identical, correct output on the same image, and only the --jinja run's
        // chat-template log showed the <think> wrapper - the CLI's default templating never
        // invokes MiniCPM's thinking-mode template in the first place. --jinja, folding and
        // enable_thinking:false ARE still needed in the SERVER-mode path (runOnePassServer):
        // llama-server's OpenAI-style endpoint renders a messages array through the model's real
        // template.
        val args = LlamaLaunch.cliArgs(
            LlamaLaunch.Cli(
                execPath = execPath.absolutePath,
                modelPath = modelPath,
                mmprojPath = mmprojPath,
                imagePath = imageFile.absolutePath,
                prompt = promptText,
                systemPrompt = if (useSystemPrompt) systemPrompt else null,
                maxTokens = maxTokens,
                threads = threads,
                useGpu = useGpu,
                family = LlamaLaunch.Family(
                    isMiniCpm = isMiniCpmModel(modelPath),
                    isLfm2 = isLfm2Model(modelPath),
                    isQwen = isQwenModel(modelPath),
                ),
                grammarArgs = grammarArgs,
            ),
        )
        val pb = ProcessBuilder(args)
        pb.environment().putAll(
            LlamaLaunch.environment(applicationInfo.nativeLibraryDir, tokenCap, isQwenModel(modelPath)),
        )
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

    private fun cliDiag(raw: String?): String = OutputParser.cliDiag(raw)

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
     *  The cap is applied as a STARTUP env var (LlamaLaunch.environment on the server's
     *  ProcessBuilder), not a per-request field - so today, flipping pan_full's 128/256
     *  toggle tears down a running server and pays a full model reload. Measured
     *  2026-09-29: that reload alone is ~24.6s.
     *
     *  But the cap is only actually READ by models that route their image sizing
     *  through set_limit_image_tokens() - i.e. Qwen's dyn_size preprocessor. MiniCPM-V's
     *  llava_uhd/minicpmv slicer never reads
     *  LLAMA_ARG_IMAGE_MAX_TOKENS at all (same reason MTMD_MAX_LONGEST_EDGE is gated
     *  off for it). Restarting for a setting the model ignores is a pure 24.6s
     *  waste, so only treat the cap as restart-worthy for models that use it. */
    private fun capAffectsServer(modelPath: String): Boolean =
        !isMiniCpmModel(modelPath)

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
        // See capAffectsServer() - for MiniCPM-V the cap is inert, so it must
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
            val isMiniCpm = isMiniCpmModel(modelPath)
            val isLfm2 = isLfm2Model(modelPath)
            val isQwen = isQwenModel(modelPath)
            val args = LlamaLaunch.serverArgs(
                LlamaLaunch.Server(
                    execPath = execPath.absolutePath,
                    modelPath = modelPath,
                    mmprojPath = mmprojPath,
                    port = port,
                    threads = threads,
                    useGpu = useGpu,
                    family = LlamaLaunch.Family(isMiniCpm = isMiniCpm, isLfm2 = isLfm2, isQwen = isQwen),
                    jinja = isMiniCpm || isLfm2,
                ),
            )
            val pb = ProcessBuilder(args)
            pb.environment().putAll(
                LlamaLaunch.environment(
                    applicationInfo.nativeLibraryDir,
                    imageTokenCap,
                    isQwen,
                    capVisionEdge = !isMiniCpm && !isLfm2,
                ),
            )
            pb.redirectErrorStream(true)

            val process = pb.start()
            // Drain the server's own log output on its own thread so it never blocks on
            // a full stdout pipe - readiness is polled via /health below, not by
            // parsing log text (simpler, and doesn't break if the log format changes).
            // Forwarded to logcat on debuggable builds only, so a failed startup is
            // diagnosable in development but server output (which can echo request
            // content) never reaches the log of a release build. Always drained.
            val forwardServerLog = (applicationInfo.flags and android.content.pm.ApplicationInfo.FLAG_DEBUGGABLE) != 0
            Thread {
                try {
                    process.inputStream.bufferedReader().forEachLine { line ->
                        if (forwardServerLog) android.util.Log.d("LlamaServer", line)
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
        if (!ModelFiles.projectorMatches(modelName, mmprojName)) return
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
                    if (docType == "aadhaar") PromptFile.body(loadAsset(PromptFile.userAsset("aadhaar_qwen"))) else promptFor("pan_full")
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
                val payload = LlamaRequest.warmUpPayload(
                    if (useSystem) systemPrompt else null,
                    userText,
                    b64,
                    warmGrammar,
                )
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
                RunLog(runLogFile()).appendStatus(statusPrefix, java.util.Date(), summary)
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
            warmKey(useSystemPrompt && !isMiniCpmModel(modelPath), promptText))
        val startNanos = System.nanoTime()
        val monitor = if (pid != null) startMonitoring(pid, startNanos) else MonitorHandle()

        // A separate system message goes through the GGUF chat-template rendering path
        // server-side, which corrupts the image marker for MiniCPM-V (found when its first
        // server-mode run came back "no JSON object found" 2026-09-29), so for MiniCPM-V the
        // system prompt is folded into the user text.
        val isJinjaModel = isMiniCpmModel(modelPath)
        val isLfm2 = isLfm2Model(modelPath)
        val b64 = android.util.Base64.encodeToString(imageFile.readBytes(), android.util.Base64.NO_WRAP)
        val payload = LlamaRequest.scanPayload(
            LlamaRequest.Scan(
                systemPrompt = if (useSystemPrompt) systemPrompt else "",
                promptText = promptText,
                imageBase64 = b64,
                useSystemPrompt = useSystemPrompt,
                foldSystemPrompt = isJinjaModel,
                imageFirst = isLfm2,
                maxTokens = maxTokens,
                grammarText = grammarText,
                jsonSchema = if (useGrammar) schemaJson else null,
                thinkingOff = !isLfm2,
            ),
        )

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
            usageSummary = LlamaRequest.usageSummary(body)

            rawOutput = LlamaRequest.contentOf(body)
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
                // A one-shot scan loads the whole model again; a warm server left from an earlier scan
                // would hold a second copy (about 2.2 GB for LFM2-VL) and the phone starts swapping.
                if (!useServerMode) stopServer()
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
                    ModelRegistry.provenance(ModelRegistry.find(modelRegistry, modelName)) +
                    ExtractionPlan.profileSuffix(docType, modelName)
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
                    // Everything that depends only on the document type and the model (image
                    // preparation, prompt, grammar, token limits, how the answer is checked) is
                    // decided in ExtractionPlan, which is unit-tested.
                    val plan = ExtractionPlan.forDocument(
                        docType,
                        modelName,
                        ExtractionPlan.DocTypeSets(
                            singleFieldCrop = singleFieldCropDocTypes,
                            stage1 = stage1EvalDocTypes,
                            skipSystemPromptOnly = skipSystemPromptOnlyDocTypes,
                        ),
                        binding.switchPanFullHighCap.isChecked,
                        useFieldCanvas,
                    )
                    val cap = plan.tokenCap
                    val useCanvas = plan.useCanvas
                    val prepStart = System.nanoTime()
                    val passImageFile = when (plan.imagePrep) {
                        ExtractionPlan.ImagePrep.CANVAS -> buildFieldCanvasFile(imageFile)
                        ExtractionPlan.ImagePrep.MINICPM -> downscaleForMiniCpm(imageFile)
                        ExtractionPlan.ImagePrep.LFM2 -> downscaleForLfm2(imageFile, plan.maxEdge)
                        ExtractionPlan.ImagePrep.QWEN_PAN -> downscaleForQwenPan(imageFile, cap)
                        ExtractionPlan.ImagePrep.QWEN -> downscaleForQwen(imageFile, cap)
                        ExtractionPlan.ImagePrep.NONE -> imageFile
                    }
                    val prepSec = (System.nanoTime() - prepStart) / 1_000_000_000.0
                    val outcome = runPass(
                        passImageFile,
                        PromptFile.body(loadAsset(plan.promptAsset)).let { if (plan.trimPrompt) it.trim() else it },
                        if (plan.sendsSchema) schemaFor(docType) else "",
                        "extract_schema.json",
                        useGrammar = plan.useGrammar,
                        useSystemPrompt = plan.useSystemPrompt,
                        maxTokens = plan.maxTokens,
                        tokenCap = cap,
                        grammarText = plan.grammarAsset?.let { loadAsset(it) }
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
                    val report = if (plan.isCrop) validateCropField(docType, cleaned)
                        else validateExtraction(docType,
                            plan.validationDocumentType?.let { withDocumentType(cleaned, it) } ?: cleaned,
                            plan.compact)
                    val runNumber = nextRunNumber()
                    // Header is formatted on its own and the model output is concatenated
                    // afterwards, rather than interpolated into the same format string.
                    // $cleaned and $report are model-derived text: any literal '%' in them
                    // (a name, a validation note) would otherwise be read as a format
                    // specifier and throw or mangle the saved run. Pre-existing shape,
                    // but this line was already being rewritten for the diagnostics.
                    val promptVersions = PromptFile.provenance(
                        plan.promptAsset,
                        if (plan.useSystemPrompt) PromptFile.SYSTEM else null,
                    )
                    val header = "[$mode | cap=$cap$promptVersions | %.1fs]".format(outcome.elapsedSec)
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

    private fun str(obj: org.json.JSONObject, k: String): String? = ExtractionRules.str(obj, k)

    /** Date normaliser: see ExtractionRules.toIso. */
    private fun toIso(raw: String?): String? = ExtractionRules.toIso(raw)

    // A hardcoded upper year bound goes stale the moment the calendar passes it - the
    // dob check's "2025" cap was already in the past by the time this was tested live
    // (found 2026-09-16), flagging every genuinely correct current-year extraction as
    // "implausible". Compute it from the device clock instead so it never goes stale.
    private fun currentYear(): Int = java.time.Year.now().value

    private fun checkDateShape(
        problems: MutableList<String>, notes: MutableList<String>,
        obj: org.json.JSONObject, key: String, minYear: Int, maxYear: Int
    ) = ExtractionRules.checkDateShape(problems, notes, obj, key, minYear, maxYear)

    // Aadhaar checksum: see ExtractionRules.verhoeffValid.
    private fun verhoeffValid(number: String): Boolean = ExtractionRules.verhoeffValid(number)


    private fun coverageReport(
        label: String, obj: org.json.JSONObject, readableFields: List<String>,
        problems: List<String>, notes: List<String>, oftenNullFields: List<String> = emptyList()
    ): String = ExtractionRules.coverageReport(label, obj, readableFields, problems, notes, oftenNullFields)

    private fun parseSide(label: String, text: String): Pair<org.json.JSONObject?, String?> =
        OutputParser.parseSide(label, text)

    private fun validateCropField(docType: String, text: String): String = PanRules.validateCrop(docType, text)

    private fun validateExtraction(docType: String, text: String, compact: Boolean = false): String {
        val (obj, err) = parseSide(docType.uppercase(), text)
        if (err != null) return err

        return when (docType) {
            "pan" -> validatePan(obj!!)
            "pan_full" -> validatePan(obj!!)
            "aadhaar" -> validateAadhaar(obj!!, compact)
            "passport" -> validatePassport(obj!!, compact)
            "dl" -> validateDl(obj!!, compact)
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

    private fun validatePan(obj: org.json.JSONObject): String =
        PanRules.validate(obj, currentYear())

    private fun validateAadhaar(obj: org.json.JSONObject, compact: Boolean = false): String =
        AadhaarRules.validate(obj, currentYear(), compact)

    private fun validateStage1Pan(obj: org.json.JSONObject): String = Stage1Rules.validatePan(obj, currentYear())

    private fun validateStage1Aadhaar(obj: org.json.JSONObject): String =
        Stage1Rules.validateAadhaar(obj, currentYear())

    private fun validatePassport(obj: org.json.JSONObject, compact: Boolean = false): String =
        PassportRules.validate(obj, currentYear(), compact)

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

    private fun validateDl(obj: org.json.JSONObject, compact: Boolean = false): String =
        DlRules.validate(obj, currentYear(), compact)

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

    private fun readVmRssMb(pid: Long): Double? =
        readFile("/proc/$pid/status")?.let { ProcessStats.parseVmRssMb(it) }

    /** Returns (cpuPercentSinceLastSample, thisSampleTicks, thisSampleWallNanos). */
    private fun readCpuPercent(pid: Long, lastTicks: Long, lastWallNanos: Long): Triple<Double?, Long, Long> {
        val nowWallNanos = System.nanoTime()
        val stat = try {
            RandomAccessFile("/proc/$pid/stat", "r").use { it.readLine() }
        } catch (e: Exception) {
            null
        }
        val sample = ProcessStats.cpuSample(stat, lastTicks, lastWallNanos, nowWallNanos, clkTck)
        return Triple(sample.percent, sample.ticks, sample.wallNanos)
    }

    private fun extractAnswer(raw: String): String = OutputParser.extractAnswer(raw)
}
