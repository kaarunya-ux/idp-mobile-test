package com.modeltesting.vlmtester

import java.io.File
import java.security.MessageDigest
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [PromptFile], and for the prompt files shipped in `src/main/assets/prompts`. */
class PromptFileTest {
    private val promptDir = File("src/main/assets/prompts")

    /**
     * SHA-256 of each prompt text exactly as it was validated on a phone, before the files were
     * renamed to `<name>.v1.md` and given front matter. Changing a prompt means adding a new
     * version file and re-running the evaluation (LLM-02, ML-12), not editing v1: if one of these
     * fails, a validated prompt was changed.
     */
    private val validatedBodyHashes = mapOf(
        "system" to "c055e9d51a9c906936e80e6b02b6e86d2708c1e9df1500091395abc238819622",
        "user_aadhaar_compact" to "4d1185ab2d049516d81adc9458f25a1d96c8902a1efd61304cb0568013a54715",
        "user_aadhaar" to "c7e0a030a029bd1cb824f308ff3704f0f3a23f06e203030713bd510ad6d2ea26",
        "user_aadhaar_qwen" to "64adf914a9d9e93078a08a25fbdfa082e673dfdc97c5e1b6dd7800d7f8fcbe92",
        "user_aadhaar_stage1" to "177fe0a6c7d854ca5d6d4bada0f4aeca72808a68f3088bf89c398e9d41b63069",
        "user_dl_compact" to "7e1f3e325ebe17dfe9f7d0219b05a3a77c61acecd33991a7594d69973c325a86",
        "user_dl" to "2b9ce1a7895ccf31d03fcb57c79eae7e8f3b1bbfe076adb506d7f57c3b5e9ad3",
        "user_pan" to "b6137099e21cd8011870d7ccad8616803ff7d5f5d09dd7be0cf5dba65b64fbc9",
        "user_pan_crop" to "19b68765c1c80ee95c7a7d6dcff50c5d2503533392ec4265cb264b2066448c5f",
        "user_pan_full" to "3f0048d6a7cb1aed1c08ad59040306a939c464cabf29b38d44c771de31be9585",
        "user_pan_full_lfm2" to "06ce2a0713a61e58d6975c109f83f202b010f66322ba767a981973344fb5aca7",
        "user_pan_stage1" to "5331e76b69f4c7757d955358b0f921389ee9b3ddc81708402e76b49b21852abe",
        "user_passport_compact" to "59087453545f76b68f3f2706565093ca60e9b39a66a051e6e5ce37ce61fe0411",
        "user_passport" to "c122dcef952244eb152c73050a63b60beb20a82115825ff7be863937960798bc",
        "user_tt_dl" to "7947bb63ea743ba8957b2ca24ccc96a33f64db30fe62593167403d64eeba326b",
        "user_tt_id_back" to "5e32ed9feda5323293517f2cc815030225dac00ba9e8e988cf416db093129bad",
        "user_tt_id_front" to "3a7595a6bb3498d4510ab77d78a513b3a743fa2b543f373f563f5432888e4892",
        "user_tt_passport" to "4b0bb3e64b9d8044089d7da06fec0258459cb26f2885ab53dc2892a9dfa52905"
    )

    private fun sha256(text: String): String =
        MessageDigest.getInstance("SHA-256").digest(text.toByteArray(Charsets.UTF_8))
            .joinToString("") { "%02x".format(it) }

    // --- parse -----------------------------------------------------------------------------

    @Test
    fun `parse reads the front matter and returns the body untouched`() {
        val text = "---\nmodel: any\ntemperature: 0\nschema: pan_full\n---\nLine one\n\nLine two\n"
        val parsed = PromptFile.parse(text)
        assertEquals("any", parsed.model)
        assertEquals("0", parsed.temperature)
        assertEquals("pan_full", parsed.schema)
        assertEquals("Line one\n\nLine two\n", parsed.body)
    }

    @Test
    fun `parse keeps a body that itself contains a --- line`() {
        val body = "Rules:\n---\nmore rules\n---\nend\n"
        val parsed = PromptFile.parse("---\nmodel: any\ntemperature: 0\nschema: none\n---\n$body")
        assertEquals(body, parsed.body)
    }

    @Test
    fun `parse keeps leading blank lines and a missing final newline in the body`() {
        val parsed = PromptFile.parse("---\nmodel: any\ntemperature: 0\nschema: none\n---\n\n  indented, no newline")
        assertEquals("\n  indented, no newline", parsed.body)
    }

    @Test
    fun `parse fails when there is no front matter`() {
        for (text in listOf("", "just a prompt\n", "--\nmodel: any\n---\nx")) {
            var message = ""
            try {
                PromptFile.parse(text)
            } catch (e: IllegalArgumentException) {
                message = e.message.orEmpty()
            }
            assertTrue(text, message.startsWith("prompt file has no front matter"))
        }
    }

    @Test
    fun `parse fails when the front matter is not closed`() {
        var message = ""
        try {
            PromptFile.parse("---\nmodel: any\ntemperature: 0\nschema: none\nthe prompt text\n")
        } catch (e: IllegalArgumentException) {
            message = e.message.orEmpty()
        }
        assertEquals("prompt file front matter is not closed with ---", message)
    }

    @Test
    fun `parse fails when a required field is missing`() {
        for (missing in listOf("model", "temperature", "schema")) {
            val fields = mapOf("model" to "any", "temperature" to "0", "schema" to "none") - missing
            val header = fields.entries.joinToString("\n") { "${it.key}: ${it.value}" }
            var message = ""
            try {
                PromptFile.parse("---\n$header\n---\nbody\n")
            } catch (e: IllegalArgumentException) {
                message = e.message.orEmpty()
            }
            assertEquals("prompt front matter is missing '$missing'", message)
        }
    }

    // --- names and provenance --------------------------------------------------------------

    @Test
    fun `asset names follow the name dot v n dot md pattern`() {
        val pattern = Regex("^prompts/[a-z0-9_]+\\.v[0-9]+\\.md$")
        assertTrue(PromptFile.SYSTEM, pattern.matches(PromptFile.SYSTEM))
        for (name in listOf("pan_full", "aadhaar_qwen", "tt_id_front")) {
            assertTrue(name, pattern.matches(PromptFile.userAsset(name)))
        }
        assertEquals("prompts/user_pan_full.v1.md", PromptFile.userAsset("pan_full"))
    }

    @Test
    fun `versionOf gives the base name in the standard's form`() {
        assertEquals("user_pan_full.v1", PromptFile.versionOf("prompts/user_pan_full.v1.md"))
        assertEquals("system.v1", PromptFile.versionOf(PromptFile.SYSTEM))
        assertEquals("field-read.v2", PromptFile.versionOf("field-read.v2.md"))
    }

    @Test
    fun `provenance names the prompt and, when used, the system prompt`() {
        assertEquals(
            " | promptVersion=user_pan_full.v1 | systemPromptVersion=system.v1",
            PromptFile.provenance("prompts/user_pan_full.v1.md", PromptFile.SYSTEM)
        )
        assertEquals(
            " | promptVersion=user_pan_full.v1",
            PromptFile.provenance("prompts/user_pan_full.v1.md", null)
        )
    }

    // --- the shipped files -----------------------------------------------------------------

    @Test
    fun `every shipped prompt is a versioned md file with front matter`() {
        val files = promptDir.listFiles().orEmpty().map { it.name }.sorted()
        assertEquals(validatedBodyHashes.keys.map { "$it.v1.md" }.sorted(), files)
        for (file in promptDir.listFiles().orEmpty()) {
            val parsed = PromptFile.parse(file.readText(Charsets.UTF_8))
            assertEquals(file.name, "0", parsed.temperature)
            assertTrue(file.name, parsed.model.isNotBlank())
            assertTrue(file.name, parsed.schema.isNotBlank())
        }
    }

    @Test
    fun `no old unversioned txt prompt is left behind`() {
        assertTrue(promptDir.listFiles().orEmpty().none { it.name.endsWith(".txt") })
    }

    @Test
    fun `every prompt body is byte for byte the text that was validated`() {
        for ((name, expected) in validatedBodyHashes) {
            val body = PromptFile.body(File(promptDir, "$name.v1.md").readText(Charsets.UTF_8))
            assertEquals(name, expected, sha256(body))
        }
    }

    @Test
    fun `the schema named in each front matter exists, or is none`() {
        val schemas = File("src/main/assets/schemas").listFiles().orEmpty().map { it.name.removeSuffix(".json") }
        for (file in promptDir.listFiles().orEmpty()) {
            val schema = PromptFile.parse(file.readText(Charsets.UTF_8)).schema
            assertTrue("${file.name}: $schema", schema == "none" || schema in schemas)
        }
    }

    @Test
    fun `every prompt and grammar the extraction plan can ask for exists`() {
        val sets = ExtractionPlan.DocTypeSets(
            setOf("pan_crop"),
            setOf("pan_stage1", "aadhaar_stage1"),
            setOf("pan_full")
        )
        val docTypes = listOf(
            "pan",
            "aadhaar",
            "passport",
            "dl",
            "pan_full",
            "pan_crop",
            "pan_stage1",
            "aadhaar_stage1"
        )
        val models = listOf("qwen3-vl-4b.gguf", "lfm2-vl-3b.gguf", "minicpm-v.gguf", "visionpsy.gguf")
        for (docType in docTypes) {
            for (model in models) {
                val plan = ExtractionPlan.forDocument(
                    docType,
                    model,
                    sets,
                    panFullHighCap = true,
                    useFieldCanvas = false
                )
                assertTrue(plan.promptAsset, File("src/main/assets/${plan.promptAsset}").exists())
                plan.grammarAsset?.let { assertTrue(it, File("src/main/assets/$it").exists()) }
            }
        }
        assertTrue(File("src/main/assets/${PromptFile.SYSTEM}").exists())
        assertFalse(File("src/main/assets/prompts/system.txt").exists())
    }
}
