package com.modeltesting.vlmtester

import com.modeltesting.vlmtester.ExtractionPlan.ImagePrep
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for [ExtractionPlan]. The expected values were written from the chain of `if`
 * statements in `runExtraction` as it was before the decisions were moved.
 */
class ExtractionPlanTest {
    private val sets = ExtractionPlan.DocTypeSets(
        singleFieldCrop = setOf("pan_crop"),
        stage1 = setOf("pan_stage1", "aadhaar_stage1"),
        skipSystemPromptOnly = setOf("pan_full")
    )

    private val qwen4b = "Qwen3-VL-4B-Instruct-Q4_K_M.gguf"
    private val qwen2b = "Qwen3-VL-2B-Instruct-Q4_K_M.gguf"
    private val lfm2 = "LFM2-VL-3B-Q4_K_M.gguf"
    private val miniCpm = "MiniCPM-V-4.6-Q4_K_M.gguf"
    private val other = "VisionPsy-Nano-Q5_K_M.gguf"

    private fun plan(docType: String, model: String, highCap: Boolean = true, canvas: Boolean = false) =
        ExtractionPlan.forDocument(docType, model, sets, highCap, canvas)

    // --- the validated profiles ---------------------------------------------------------------

    @Test
    fun `Qwen with pan_full gets the one-line grammar, a fixed cap and no system prompt`() {
        for (highCap in listOf(true, false)) {
            val plan = plan("pan_full", qwen4b, highCap)
            assertEquals(ImagePrep.QWEN_PAN, plan.imagePrep)
            assertEquals(256, plan.tokenCap)
            assertEquals("prompts/user_pan_full.v1.md", plan.promptAsset)
            assertFalse(plan.trimPrompt)
            assertTrue(plan.sendsSchema)
            assertTrue(plan.useGrammar)
            assertFalse(plan.useSystemPrompt)
            assertEquals(200, plan.maxTokens)
            assertEquals("grammars/pan_full_qwen.gbnf", plan.grammarAsset)
            assertEquals("pan", plan.validationDocumentType)
            assertFalse(plan.isCrop)
        }
    }

    @Test
    fun `Qwen with aadhaar gets the flag prompt and grammar, a 560 px image and a cap of 1024`() {
        val plan = plan("aadhaar", qwen2b)
        assertEquals(ImagePrep.QWEN_DOC, plan.imagePrep)
        assertEquals(560, plan.maxEdge)
        assertEquals(1024, plan.tokenCap)
        assertEquals("prompts/user_aadhaar_qwen.v1.md", plan.promptAsset)
        assertFalse(plan.trimPrompt)
        assertTrue(plan.sendsSchema)
        assertTrue(plan.useGrammar)
        assertTrue(plan.useSystemPrompt)
        assertEquals(512, plan.maxTokens)
        assertEquals("grammars/aadhaar_qwen.gbnf", plan.grammarAsset)
        assertEquals("aadhaar", plan.validationDocumentType)
    }

    @Test
    fun `LFM2 with pan_full gets its own prompt, trimmed, its grammar and a cap of 128`() {
        for (highCap in listOf(true, false)) {
            val plan = plan("pan_full", lfm2, highCap)
            assertEquals(ImagePrep.LFM2, plan.imagePrep)
            assertEquals(128, plan.tokenCap)
            assertEquals("prompts/user_pan_full_lfm2.v1.md", plan.promptAsset)
            assertTrue(plan.trimPrompt)
            assertEquals("grammars/pan_full_lfm2.gbnf", plan.grammarAsset)
            assertEquals("pan", plan.validationDocumentType)
            assertFalse(plan.useSystemPrompt)
            assertEquals(200, plan.maxTokens)
            assertEquals(500, plan.maxEdge)
        }
    }

    @Test
    fun `LFM2 with aadhaar, dl or passport gets a shrunk image and the compact prompt and grammar`() {
        for (docType in listOf("aadhaar", "dl", "passport")) {
            val plan = plan(docType, lfm2)
            assertEquals(docType, ImagePrep.LFM2, plan.imagePrep)
            assertEquals(docType, 560, plan.maxEdge)
            assertEquals(docType, 1024, plan.tokenCap)
            assertEquals(docType, "prompts/user_${docType}_compact.v1.md", plan.promptAsset)
            assertEquals(docType, "grammars/${docType}_compact.gbnf", plan.grammarAsset)
            assertEquals(docType, docType, plan.validationDocumentType)
            assertEquals(docType, 512, plan.maxTokens)
            assertTrue(docType, plan.compact)
            assertTrue(docType, plan.useGrammar)
            assertFalse(docType, plan.useSystemPrompt)
        }
    }

    @Test
    fun `MiniCPM and Qwen get the compact prompt for dl and passport, MiniCPM also for aadhaar`() {
        for (model in listOf(miniCpm, qwen4b, qwen2b)) {
            for (docType in listOf("dl", "passport")) {
                val plan = plan(docType, model)
                assertTrue("$model $docType", plan.compact)
                assertEquals("prompts/user_${docType}_compact.v1.md", plan.promptAsset)
                assertEquals("grammars/${docType}_compact.gbnf", plan.grammarAsset)
                assertEquals(docType, plan.validationDocumentType)
                assertFalse("$model $docType", plan.useSystemPrompt)
                assertEquals(1024, plan.tokenCap)
            }
        }
        val miniAadhaar = plan("aadhaar", miniCpm)
        assertTrue(miniAadhaar.compact)
        assertEquals("prompts/user_aadhaar_compact.v1.md", miniAadhaar.promptAsset)
        assertEquals("grammars/aadhaar_compact.gbnf", miniAadhaar.grammarAsset)
        assertEquals(ImagePrep.MINICPM, miniAadhaar.imagePrep)
        assertEquals(ImagePrep.QWEN_DOC, plan("dl", qwen4b).imagePrep)
    }

    @Test
    fun `Qwen keeps its own aadhaar profile and other families and documents keep the full prompt`() {
        val qwenAadhaar = plan("aadhaar", qwen4b)
        assertFalse(qwenAadhaar.compact)
        assertEquals("prompts/user_aadhaar_qwen.v1.md", qwenAadhaar.promptAsset)
        assertEquals("grammars/aadhaar_qwen.gbnf", qwenAadhaar.grammarAsset)
        for (docType in listOf("aadhaar", "dl", "passport")) {
            val plan = plan(docType, other)
            assertFalse(docType, plan.compact)
            assertEquals("prompts/user_$docType.v1.md", plan.promptAsset)
            assertTrue(docType, plan.useSystemPrompt)
            assertNull(docType, plan.grammarAsset)
        }
        for (model in listOf(lfm2, miniCpm, qwen4b)) {
            for (docType in listOf("pan", "pan_full", "tt_dl", "tt_passport", "pan_crop")) {
                assertFalse("$model $docType", plan(docType, model).compact)
            }
        }
    }

    @Test
    fun `LFM2 with other document types still gets the image as it is`() {
        for (docType in listOf("pan", "tt_dl", "tt_passport", "tt_id_front", "pan_crop")) {
            val plan = plan(docType, lfm2)
            assertEquals(docType, ImagePrep.NONE, plan.imagePrep)
            assertEquals(docType, 0, plan.maxEdge)
        }
    }

    @Test
    fun `only LFM2 plans carry a longest edge and the other models keep their image preparation`() {
        for (docType in listOf("aadhaar", "dl", "passport", "pan_full")) {
            assertEquals(docType, 0, plan(docType, miniCpm).maxEdge)
            assertEquals(docType, 0, plan(docType, other).maxEdge)
        }
        assertEquals(ImagePrep.MINICPM, plan("dl", miniCpm).imagePrep)
        assertEquals(ImagePrep.NONE, plan("dl", other).imagePrep)
    }

    @Test
    fun `Qwen with dl or passport gets a shrunk image at 560 px and keeps the compact prompt`() {
        for (model in listOf(qwen4b, qwen2b)) {
            for (docType in listOf("dl", "passport")) {
                val plan = plan(docType, model)
                assertEquals("$model $docType", ImagePrep.QWEN_DOC, plan.imagePrep)
                assertEquals("$model $docType", 560, plan.maxEdge)
                assertEquals("$model $docType", 1024, plan.tokenCap)
                assertTrue("$model $docType", plan.compact)
                assertEquals("prompts/user_${docType}_compact.v1.md", plan.promptAsset)
            }
        }
    }

    @Test
    fun `Qwen pan_full and the other document types keep their own image preparation`() {
        val aadhaar = plan("aadhaar", qwen4b)
        assertEquals(ImagePrep.QWEN_DOC, aadhaar.imagePrep)
        assertEquals(560, aadhaar.maxEdge)
        assertFalse(aadhaar.compact)
        val pan = plan("pan_full", qwen4b)
        assertEquals(ImagePrep.QWEN_PAN, pan.imagePrep)
        assertEquals(0, pan.maxEdge)
        for (docType in listOf("pan", "tt_dl", "tt_passport", "pan_crop")) {
            assertEquals(docType, ImagePrep.QWEN, plan(docType, qwen4b).imagePrep)
        }
    }

    @Test
    fun `the Qwen 2B and 4B models are planned identically`() {
        for (docType in listOf("pan_full", "aadhaar", "pan", "dl", "passport", "pan_crop", "pan_stage1")) {
            assertEquals(docType, plan(docType, qwen4b), plan(docType, qwen2b))
        }
    }

    // --- other models and document types ------------------------------------------------------

    @Test
    fun `MiniCPM gets the smaller image and no grammar profile`() {
        val plan = plan("pan_full", miniCpm, highCap = false)
        assertEquals(ImagePrep.MINICPM, plan.imagePrep)
        assertEquals(128, plan.tokenCap)
        assertEquals("prompts/user_pan_full.v1.md", plan.promptAsset)
        assertNull(plan.grammarAsset)
        assertNull(plan.validationDocumentType)
        assertEquals(256, plan("pan_full", miniCpm, highCap = true).tokenCap)
    }

    @Test
    fun `the field canvas is used only for pan_full with MiniCPM when asked`() {
        assertEquals(ImagePrep.CANVAS, plan("pan_full", miniCpm, canvas = true).imagePrep)
        assertTrue(plan("pan_full", miniCpm, canvas = true).useCanvas)
        assertEquals(ImagePrep.MINICPM, plan("aadhaar", miniCpm, canvas = true).imagePrep)
        assertEquals(ImagePrep.QWEN_PAN, plan("pan_full", qwen4b, canvas = true).imagePrep)
        assertFalse(plan("pan_full", miniCpm, canvas = false).useCanvas)
    }

    @Test
    fun `a model of another family gets the image as it is`() {
        val plan = plan("pan_full", other)
        assertEquals(ImagePrep.NONE, plan.imagePrep)
        assertNull(plan.grammarAsset)
        assertNull(plan.validationDocumentType)
        assertEquals(256, plan.tokenCap)
    }

    @Test
    fun `other document types use the default prompt, a cap of 1024 and the full output budget`() {
        for (docType in listOf("dl", "passport", "pan", "aadhaar")) {
            val model = if (docType in listOf("aadhaar", "dl", "passport")) other else qwen4b
            val plan = plan(docType, model)
            assertEquals("prompts/user_$docType.v1.md", plan.promptAsset)
            assertEquals(1024, plan.tokenCap)
            assertEquals(512, plan.maxTokens)
            assertTrue(plan.sendsSchema)
            assertTrue(plan.useSystemPrompt)
            assertNull(plan.grammarAsset)
            assertNull(plan.validationDocumentType)
        }
    }

    @Test
    fun `a single-field crop skips the schema, the grammar and the system prompt`() {
        val plan = plan("pan_crop", qwen4b)
        assertTrue(plan.isCrop)
        assertFalse(plan.sendsSchema)
        assertFalse(plan.useGrammar)
        assertFalse(plan.useSystemPrompt)
        assertEquals(64, plan.maxTokens)
        assertEquals(256, plan.tokenCap)
    }

    @Test
    fun `the stage1 evaluation skips the schema and gets a long output budget`() {
        for (docType in listOf("pan_stage1", "aadhaar_stage1")) {
            val plan = plan(docType, other)
            assertFalse(plan.isCrop)
            assertFalse(plan.sendsSchema)
            assertFalse(plan.useGrammar)
            assertFalse(plan.useSystemPrompt)
            assertEquals(1024, plan.maxTokens)
        }
    }

    @Test
    fun `a model name matching both LFM2 and Qwen gets the LFM2 settings first`() {
        val plan = plan("pan_full", "lfm2-qwen-hybrid.gguf")
        assertEquals(ImagePrep.LFM2, plan.imagePrep)
        assertEquals(128, plan.tokenCap)
        assertEquals("grammars/pan_full_lfm2.gbnf", plan.grammarAsset)
        assertEquals("prompts/user_pan_full_lfm2.v1.md", plan.promptAsset)
    }

    // --- profileSuffix ----------------------------------------------------------------------

    @Test
    fun `profileSuffix names the validated profile in the run log header`() {
        assertEquals(" | profile=qwen-pan-oneline-480px", ExtractionPlan.profileSuffix("pan_full", qwen4b))
        assertEquals(" | profile=lfm2-bul2-grammar", ExtractionPlan.profileSuffix("pan_full", lfm2))
        assertEquals(" | profile=qwen-aadhaar-flag-fullsize", ExtractionPlan.profileSuffix("aadhaar", qwen2b))
    }

    @Test
    fun `profileSuffix is empty when no validated profile applies`() {
        assertEquals("", ExtractionPlan.profileSuffix("pan_full", miniCpm))
        assertEquals("", ExtractionPlan.profileSuffix("dl", qwen4b))
        assertEquals("", ExtractionPlan.profileSuffix("tt_id", qwen4b))
        assertEquals("", ExtractionPlan.profileSuffix("pan_full", other))
    }
}
