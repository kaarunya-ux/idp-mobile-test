package com.modeltesting.vlmtester

import android.graphics.Bitmap
import android.graphics.BitmapFactory
import android.graphics.Canvas
import android.graphics.Color
import android.graphics.Paint
import android.graphics.RectF
import java.io.File
import java.io.FileOutputStream
import kotlin.math.roundToInt

/**
 * The Bitmap work that shrinks a photo before a model reads it (the sizes come from ModelProfiles,
 * ImageSizing and ExtractionPlan) and the PAN field canvas. Moved out of MainActivity without changing
 * a line of the image handling; it needs Android's Bitmap classes, so unit tests cannot run it and
 * only a phone proves it (compare the resized files before and after a change). The resized copies go
 * to [cacheDir], the last field canvas to [filesDir].
 */
class ImagePreparation(private val cacheDir: File, private val filesDir: File) {
    /** Resize for the Qwen pan_full profile - see useQwenPanProfile(). */
    fun downscaleForQwenPan(imageFile: File, tokenCap: Int): File {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imageFile.absolutePath, bounds)
        val edge = ImageSizing.qwenPanTargetEdge(bounds.outWidth, bounds.outHeight, tokenCap) ?: return imageFile
        return downscaleForMiniCpm(imageFile, edge, "qwen_resized.jpg")
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
    fun downscaleForMiniCpm(imageFile: File, longestEdge: Int = 560, outName: String = "minicpm_resized.jpg"): File {
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
    fun downscaleForLfm2(imageFile: File, longestEdge: Int): File =
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
    fun downscaleForQwen(
        imageFile: File,
        tokenCap: Int,
        outName: String = "qwen_resized.jpg",
        headroom: Double = 2.0
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
        Band(0.20f, 0.30f, 0.72f, 0.52f), // "Permanent Account Number Card" + PAN number
        Band(0.00f, 0.52f, 0.50f, 0.68f), // name
        Band(0.00f, 0.66f, 0.50f, 0.82f), // father's name
        Band(0.00f, 1.00f - 0.20f, 0.50f, 1.00f) // date of birth
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
    fun buildFieldCanvasFile(imageFile: File, size: Int = 448): File {
        val bounds = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(imageFile.absolutePath, bounds)
        var sample = 1
        // Keep the decode small enough for memory but never below ~1600px on the long edge:
        // the bands are cut from real pixels, that is the whole point.
        while (maxOf(bounds.outWidth, bounds.outHeight) / (sample * 2) >= 1600) sample *= 2
        val card = BitmapFactory.decodeFile(
            imageFile.absolutePath,
            BitmapFactory.Options().apply { inSampleSize = sample }
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
        runCatching { outFile.copyTo(File(filesDir, "last_canvas.jpg"), overwrite = true) }
        return outFile
    }

    /** Reports the pixel dimensions of an image file without decoding its full
     *  contents, for the run log. Cheap (bounds-only decode) and the single most
     *  useful number for reasoning about MiniCPM-V's vision cost: its slicer picks
     *  tile count purely from width*height vs 448^2 (mtmd-image.cpp:684-687), so
     *  seeing the actual dimensions makes the tile count derivable from the log
     *  instead of guessed at. */
    fun imageDims(f: File): String {
        val o = BitmapFactory.Options().apply { inJustDecodeBounds = true }
        BitmapFactory.decodeFile(f.absolutePath, o)
        return if (o.outWidth > 0) "${o.outWidth}x${o.outHeight}" else "unknown"
    }
}
