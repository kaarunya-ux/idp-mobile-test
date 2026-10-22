package com.modeltesting.vlmtester

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for [ModelProfiles]. The numeric constants were measured on a phone (see the
 * comments in ModelProfiles); pinning them here means changing one is a deliberate, reviewed act.
 */
class ModelProfilesTest {
    private val cropTypes = setOf("pan_crop")

    // --- detection by file name ---------------------------------------------------------

    @Test
    fun `detects the Qwen models by file name in any case`() {
        assertTrue(ModelProfiles.isQwen("Qwen3-VL-4B-Instruct-Q4_K_M.gguf"))
        assertTrue(ModelProfiles.isQwen("qwen3-vl-2b-instruct-q4_k_m.gguf"))
        assertTrue(ModelProfiles.isQwen("QWEN3-VL-2B.GGUF"))
    }

    @Test
    fun `detects LFM2 and MiniCPM by file name in any case`() {
        assertTrue(ModelProfiles.isLfm2("LFM2-VL-3B-Q4_K_M.gguf"))
        assertTrue(ModelProfiles.isLfm2("lfm2-vl-3b.gguf"))
        assertTrue(ModelProfiles.isMiniCpm("minicpm-v-4.6-q4_k_m.gguf"))
        assertTrue(ModelProfiles.isMiniCpm("MiniCPM-V-4.6.gguf"))
    }

    @Test
    fun `does not detect a model from another family`() {
        for (name in listOf("VisionPsy-Nano-Q4.gguf", "PaddleOCR-VL-Q4.gguf", "model.gguf", "")) {
            assertFalse(name, ModelProfiles.isQwen(name))
            assertFalse(name, ModelProfiles.isLfm2(name))
            assertFalse(name, ModelProfiles.isMiniCpm(name))
        }
    }

    // --- profile selection --------------------------------------------------------------

    @Test
    fun `selects the Qwen PAN profile only for pan_full with a Qwen model`() {
        assertTrue(ModelProfiles.useQwenPanProfile("pan_full", "qwen3-vl-4b.gguf"))
        assertFalse(ModelProfiles.useQwenPanProfile("pan", "qwen3-vl-4b.gguf"))
        assertFalse(ModelProfiles.useQwenPanProfile("aadhaar", "qwen3-vl-4b.gguf"))
        assertFalse(ModelProfiles.useQwenPanProfile("pan_full", "lfm2-vl-3b.gguf"))
        assertFalse(ModelProfiles.useQwenPanProfile("pan_full", "minicpm-v.gguf"))
    }

    @Test
    fun `selects the LFM2 PAN profile only for pan_full with an LFM2 model`() {
        assertTrue(ModelProfiles.useLfm2PanProfile("pan_full", "lfm2-vl-3b.gguf"))
        assertFalse(ModelProfiles.useLfm2PanProfile("pan", "lfm2-vl-3b.gguf"))
        assertFalse(ModelProfiles.useLfm2PanProfile("aadhaar", "lfm2-vl-3b.gguf"))
        assertFalse(ModelProfiles.useLfm2PanProfile("pan_full", "qwen3-vl-4b.gguf"))
    }

    @Test
    fun `selects the Qwen Aadhaar profile only for aadhaar with a Qwen model`() {
        assertTrue(ModelProfiles.useQwenAadhaarProfile("aadhaar", "qwen3-vl-2b.gguf"))
        assertFalse(ModelProfiles.useQwenAadhaarProfile("aadhaar_stage1", "qwen3-vl-2b.gguf"))
        assertFalse(ModelProfiles.useQwenAadhaarProfile("pan_full", "qwen3-vl-2b.gguf"))
        assertFalse(ModelProfiles.useQwenAadhaarProfile("aadhaar", "lfm2-vl-3b.gguf"))
    }

    @Test
    fun `the Qwen 2B and 4B models get the same profiles`() {
        for (docType in listOf("pan_full", "aadhaar", "pan", "dl", "passport")) {
            assertEquals(
                docType,
                ModelProfiles.useQwenPanProfile(docType, "qwen3-vl-4b.gguf"),
                ModelProfiles.useQwenPanProfile(docType, "qwen3-vl-2b.gguf")
            )
            assertEquals(
                docType,
                ModelProfiles.useQwenAadhaarProfile(docType, "qwen3-vl-4b.gguf"),
                ModelProfiles.useQwenAadhaarProfile(docType, "qwen3-vl-2b.gguf")
            )
        }
    }

    // --- measured constants (pinned) -----------------------------------------------------

    @Test
    fun `keeps the measured profile constants`() {
        assertEquals(128, ModelProfiles.LFM2_PAN_IMAGE_TOKEN_CAP)
        assertEquals(256, ModelProfiles.QWEN_PAN_IMAGE_TOKEN_CAP)
        assertEquals(480, ModelProfiles.QWEN_PAN_CARD_EDGE)
        assertEquals(1000, ModelProfiles.QWEN_PAN_CROP_MAX_EDGE)
        assertEquals(1024, ModelProfiles.QWEN_SERVER_IMAGE_TOKEN_CAP)
    }

    // --- image-token cap ------------------------------------------------------------------

    @Test
    fun `gives single-field crops a cap of 256 whatever the toggle says`() {
        assertEquals(256, ModelProfiles.imageTokenCapFor("pan_crop", cropTypes, panFullHighCap = true))
        assertEquals(256, ModelProfiles.imageTokenCapFor("pan_crop", cropTypes, panFullHighCap = false))
    }

    @Test
    fun `gives pan_full 256 or 128 depending on the high-cap toggle`() {
        assertEquals(256, ModelProfiles.imageTokenCapFor("pan_full", cropTypes, panFullHighCap = true))
        assertEquals(128, ModelProfiles.imageTokenCapFor("pan_full", cropTypes, panFullHighCap = false))
    }

    @Test
    fun `gives every other document type a cap of 1024 whatever the toggle says`() {
        for (docType in listOf("aadhaar", "pan", "passport", "dl", "tt_id")) {
            assertEquals(docType, 1024, ModelProfiles.imageTokenCapFor(docType, cropTypes, panFullHighCap = true))
            assertEquals(docType, 1024, ModelProfiles.imageTokenCapFor(docType, cropTypes, panFullHighCap = false))
        }
    }
}
