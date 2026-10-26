package com.modeltesting.vlmtester

import java.io.File
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [ModelRegistry], and for the registry file shipped in `src/main/assets`. */
class ModelRegistryTest {
    private val sample = """
        # a comment
        models:
          - name: demo-model
            version: "1.2"
            source: example/demo
            sha256: null
            runtime: llama.cpp
            device: cpu
            file: demo-q4.gguf
            mmproj: mmproj-demo-q8.gguf
          - name: bare-model
            runtime: llama.cpp
            device: cpu
    """.trimIndent()

    private fun failure(text: String): String = try {
        ModelRegistry.parse(text)
        ""
    } catch (e: IllegalArgumentException) {
        e.message.orEmpty()
    }

    // --- parse -------------------------------------------------------------------------------

    @Test
    fun `parse reads every field, treats null as unknown and strips quotes`() {
        val entries = ModelRegistry.parse(sample)
        assertEquals(2, entries.size)
        assertEquals(
            ModelRegistry.Entry(
                "demo-model",
                "1.2",
                "example/demo",
                null,
                "llama.cpp",
                "cpu",
                "demo-q4.gguf",
                "mmproj-demo-q8.gguf"
            ),
            entries[0]
        )
        assertEquals(ModelRegistry.Entry("bare-model", null, null, null, "llama.cpp", "cpu", null, null), entries[1])
    }

    @Test
    fun `parse ignores blank lines and comments`() {
        val entries = ModelRegistry.parse(
            "\n# top\n\nmodels:\n\n  # inside\n  - name: a\n    runtime: r\n    device: cpu\n"
        )
        assertEquals(listOf("a"), entries.map { it.name })
    }

    @Test
    fun `parse names the line of an unknown field`() {
        val message = failure("models:\n  - name: a\n    colour: red\n")
        assertEquals("model registry line 3: unknown or malformed field 'colour: red'", message)
    }

    @Test
    fun `parse rejects a repeated field, a missing header and stray lines`() {
        assertTrue(failure("models:\n  - name: a\n    name: b\n").contains("appears twice"))
        assertTrue(failure("  - name: a\n").contains("expected 'models:' first"))
        assertTrue(failure("models:\nrandom text\n").contains("unexpected line"))
        assertTrue(failure("").contains("has no 'models:' list"))
    }

    @Test
    fun `parse rejects an entry without a name, a runtime or a device`() {
        assertEquals("registry entry 1 is missing 'name'", failure("models:\n  - runtime: r\n    device: cpu\n"))
        assertEquals("registry entry 1 is missing 'runtime'", failure("models:\n  - name: a\n    device: cpu\n"))
        assertEquals("registry entry 1 is missing 'device'", failure("models:\n  - name: a\n    runtime: r\n"))
    }

    // --- find and provenance -------------------------------------------------------------------

    @Test
    fun `find matches the language-model file name exactly`() {
        val entries = ModelRegistry.parse(sample)
        assertEquals("demo-model", ModelRegistry.find(entries, "demo-q4.gguf")?.name)
        assertNull(ModelRegistry.find(entries, "DEMO-Q4.GGUF"))
        assertNull(ModelRegistry.find(entries, "mmproj-demo-q8.gguf"))
        assertNull(ModelRegistry.find(entries, "other.gguf"))
    }

    @Test
    fun `provenance names the registry model, its version and runtime`() {
        val entries = ModelRegistry.parse(sample)
        assertEquals(" | registryModel=demo-model@1.2 | runtime=llama.cpp", ModelRegistry.provenance(entries[0]))
        assertEquals(
            " | registryModel=bare-model@unversioned | runtime=llama.cpp",
            ModelRegistry.provenance(entries[1])
        )
    }

    @Test
    fun `provenance adds the start of the checksum once it is known`() {
        val sha = "0123456789abcdef".repeat(4)
        val entry = ModelRegistry.Entry("m", "1", null, sha, "llama.cpp", "cpu", "m.gguf", null)
        assertEquals(" | registryModel=m@1 | runtime=llama.cpp | sha256=0123456789ab", ModelRegistry.provenance(entry))
    }

    @Test
    fun `provenance says unregistered for a model that is not in the registry`() {
        assertEquals(" | registryModel=unregistered", ModelRegistry.provenance(null))
    }

    // --- the shipped file ----------------------------------------------------------------------

    private val shipped by lazy { ModelRegistry.parse(File("src/main/assets/models.yaml").readText(Charsets.UTF_8)) }

    @Test
    fun `the shipped registry parses and every model uses llama cpp on the CPU`() {
        assertTrue(shipped.isNotEmpty())
        assertTrue(shipped.all { it.runtime == "llama.cpp" && it.device == "cpu" })
    }

    @Test
    fun `registry names and files are unique`() {
        assertEquals(shipped.size, shipped.map { it.name }.toSet().size)
        val files = shipped.mapNotNull { it.file }
        assertEquals(files.size, files.toSet().size)
    }

    @Test
    fun `a registered checksum is a 64 digit hex string, and a file always comes with its projector`() {
        for (entry in shipped) {
            entry.sha256?.let { assertTrue(entry.name, Regex("^[0-9a-f]{64}$").matches(it)) }
            assertEquals(entry.name, entry.file == null, entry.mmproj == null)
            entry.mmproj?.let { assertTrue(entry.name, it.startsWith("mmproj-")) }
        }
    }

    @Test
    fun `the models named in the project docs are registered with the facts the docs give`() {
        val byName = shipped.associateBy { it.name }
        assertEquals("minicpm-v-4.6-q4_k_m.gguf", byName.getValue("minicpm-v-4.6").file)
        assertEquals("ggml-org/MiniCPM-V-4.6-GGUF", byName.getValue("minicpm-v-4.6").source)
        assertEquals("4.6", byName.getValue("minicpm-v-4.6").version)
        assertEquals("LFM2-VL-3B-Q4_K_M.gguf", byName.getValue("lfm2-vl-3b").file)
        assertEquals("LiquidAI", byName.getValue("lfm2-vl-3b").source)
        assertEquals("qwen3vl-2b-instruct-q4_k_m.gguf", byName.getValue("qwen3-vl-2b-instruct").file)
        assertEquals("Qwen", byName.getValue("qwen3-vl-4b-instruct").source)
    }

    @Test
    fun `only checksums computed from real files are registered`() {
        // Computed on the phone with sha256sum on 2026-10-22. The LFM2 value agrees with the
        // shortened checksum printed in LFM2_VL_3B_LATENCY_FINDINGS.md (07b435d8...6f3ffe).
        val byName = shipped.associateBy { it.name }
        assertEquals(
            "07b435d85daec5dec0e2be6644f36201d1a0dff8904d83e41175ba2d3b6f3ffe",
            byName.getValue("lfm2-vl-3b").sha256
        )
        assertEquals(
            "b1a5aa76b5ef039c2e579272ea33d4bbed7e79b49bb3ff1efdb23316d6af5199",
            byName.getValue("minicpm-v-4.6").sha256
        )
        assertEquals(
            "089d75c52f4b7ffc56ba998ffc50aae89fcafc755f9e7208aacca281dca6c2ae",
            byName.getValue("qwen3-vl-2b-instruct").sha256
        )
        assertNull(byName.getValue("qwen3-vl-4b-instruct").sha256)
        assertNull(byName.getValue("visionpsy-nano-460m-flash").sha256)
    }

    @Test
    fun `the 4B has no file name because none is written down anywhere`() {
        assertNull(shipped.first { it.name == "qwen3-vl-4b-instruct" }.file)
    }
}
