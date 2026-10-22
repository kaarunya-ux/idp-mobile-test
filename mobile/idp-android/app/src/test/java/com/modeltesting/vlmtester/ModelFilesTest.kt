package com.modeltesting.vlmtester

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [ModelFiles]. */
class ModelFilesTest {
    @Test
    fun `choices splits models from projectors and sorts each list`() {
        val choices = ModelFiles.choices(
            listOf(
                "qwen3-vl-4b-instruct-q4_k_m.gguf",
                "mmproj-qwen3-vl-4b-instruct-q8_0.gguf",
                "lfm2-vl-3b.gguf",
                "mmproj-lfm2-vl-3b.gguf",
                "mmproj-qwen3-vl-2b-instruct-q8_0.gguf"
            )
        )
        assertEquals(listOf("lfm2-vl-3b.gguf", "qwen3-vl-4b-instruct-q4_k_m.gguf"), choices.languageModels)
        assertEquals(
            listOf(
                "mmproj-lfm2-vl-3b.gguf",
                "mmproj-qwen3-vl-2b-instruct-q8_0.gguf",
                "mmproj-qwen3-vl-4b-instruct-q8_0.gguf"
            ),
            choices.projectors
        )
    }

    @Test
    fun `choices ignores files that are not gguf`() {
        val choices = ModelFiles.choices(listOf("notes.txt", "model.bin", "ocr_in", "run_log.txt", "a.gguf.part"))
        assertTrue(choices.languageModels.isEmpty())
        assertTrue(choices.projectors.isEmpty())
    }

    @Test
    fun `choices of an empty folder is empty`() {
        val choices = ModelFiles.choices(emptyList())
        assertTrue(choices.languageModels.isEmpty())
        assertTrue(choices.projectors.isEmpty())
    }

    @Test
    fun `projectorMatches pairs a model with the projector of the same size`() {
        val model4b = "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
        assertTrue(ModelFiles.projectorMatches(model4b, "mmproj-Qwen3-VL-4B-Instruct-Q8_0.gguf"))
        assertFalse(ModelFiles.projectorMatches(model4b, "mmproj-Qwen3-VL-2B-Instruct-Q8_0.gguf"))
    }

    @Test
    fun `projectorMatches compares the model name with the projector name ignoring case`() {
        assertTrue(
            ModelFiles.projectorMatches("Qwen3-VL-2B-Instruct-Q4.gguf", "MMPROJ-QWEN3-VL-2B-INSTRUCT-Q8_0.gguf")
        )
    }

    @Test
    fun `projectorMatches looks for the Instruct marker with its exact case`() {
        // Existing behaviour: "-INSTRUCT" is not recognised as the marker, so the whole file name
        // (with its ".gguf") has to appear in the projector name, which it does not here.
        assertFalse(
            ModelFiles.projectorMatches("QWEN3-VL-2B-INSTRUCT-Q4.gguf", "mmproj-qwen3-vl-2b-instruct-q8_0.gguf")
        )
    }

    @Test
    fun `projectorMatches pairs a model without Instruct only with a projector holding its whole file name`() {
        // Existing behaviour: with no "-Instruct" in the name the whole file name must appear in
        // the projector name, so a projector with a different quantisation suffix does not match.
        assertTrue(ModelFiles.projectorMatches("lfm2-vl-3b.gguf", "mmproj-lfm2-vl-3b.gguf"))
        assertFalse(ModelFiles.projectorMatches("lfm2-vl-3b.gguf", "mmproj-lfm2-vl-3b-q8_0.gguf"))
        assertTrue(ModelFiles.projectorMatches("lfm2-vl-3b-Instruct.gguf", "mmproj-lfm2-vl-3b-q8_0.gguf"))
    }
}
