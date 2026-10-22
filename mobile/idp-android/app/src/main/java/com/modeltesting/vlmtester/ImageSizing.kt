package com.modeltesting.vlmtester

import kotlin.math.sqrt

/**
 * The arithmetic behind the image resizing that MainActivity does with Bitmaps: when to
 * shrink, to what size, and how far the decoder can sub-sample. Pure, so it runs in plain
 * JVM unit tests; the Bitmap decode, scale and save stay in MainActivity.
 *
 * Moved out of MainActivity without changing the arithmetic.
 */
object ImageSizing {
    /** Target size for a resize, and the decoder's power-of-two sub-sampling to use first. */
    data class ResizePlan(val targetWidth: Int, val targetHeight: Int, val inSampleSize: Int)

    /** Plan for capping the longest edge at [longestEdge], or null to leave the image alone. */
    fun resizePlan(width: Int, height: Int, longestEdge: Int): ResizePlan? {
        if (width <= 0 || height <= 0 || maxOf(width, height) <= longestEdge) return null

        val scale = longestEdge.toFloat() / maxOf(width, height)
        val targetW = (width * scale).toInt().coerceAtLeast(1)
        val targetH = (height * scale).toInt().coerceAtLeast(1)

        var inSampleSize = 1
        while (width / (inSampleSize * 2) >= targetW && height / (inSampleSize * 2) >= targetH) {
            inSampleSize *= 2
        }
        return ResizePlan(targetW, targetH, inSampleSize)
    }

    /**
     * Qwen3-VL: longest edge to shrink to, or null to leave the image alone. The model keeps
     * [headroom] times its own pixel budget (tokenCap x 32x32 px), so only images holding more
     * than that are shrunk, and only down to that. The PAN profile passes a headroom of 1.0:
     * the server now runs at one 1024 cap for all document types, so the model no longer does
     * that shrink for it.
     */
    fun qwenTargetEdge(width: Int, height: Int, tokenCap: Int, headroom: Double = 2.0): Int? {
        if (width <= 0 || height <= 0) return null
        val keepPixels = headroom * tokenCap * 32 * 32
        val pixels = width.toDouble() * height
        if (pixels <= keepPixels) return null
        val scale = sqrt(keepPixels / pixels)
        return (maxOf(width, height) * scale).toInt()
    }

    /**
     * Qwen3-VL PAN profile: a tight card crop is sent at the fixed card width; anything larger
     * than the crop limit is a camera photo and is pre-sized to the exact token budget.
     * Null means leave the image alone.
     */
    fun qwenPanTargetEdge(width: Int, height: Int, tokenCap: Int): Int? {
        if (width <= 0 || height <= 0) return null
        return if (maxOf(width, height) > ModelProfiles.QWEN_PAN_CROP_MAX_EDGE) {
            qwenTargetEdge(width, height, tokenCap, headroom = 1.0)
        } else {
            ModelProfiles.QWEN_PAN_CARD_EDGE
        }
    }
}
