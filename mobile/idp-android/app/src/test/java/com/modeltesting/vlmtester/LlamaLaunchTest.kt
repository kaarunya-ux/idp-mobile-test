package com.modeltesting.vlmtester

import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for [LlamaLaunch]. The expected lists were written out by hand from the code that
 * built these command lines inside MainActivity before they were moved, flag by flag and in order.
 */
class LlamaLaunchTest {
    private val none = LlamaLaunch.Family(isMiniCpm = false, isLfm2 = false, isQwen = false)
    private val qwen = LlamaLaunch.Family(isMiniCpm = false, isLfm2 = false, isQwen = true)
    private val lfm2 = LlamaLaunch.Family(isMiniCpm = false, isLfm2 = true, isQwen = false)
    private val miniCpm = LlamaLaunch.Family(isMiniCpm = true, isLfm2 = false, isQwen = false)

    private fun cli(
        family: LlamaLaunch.Family = none,
        systemPrompt: String? = null,
        useGpu: Boolean = false,
        grammarArgs: List<String> = emptyList()
    ) = LlamaLaunch.Cli(
        execPath = "/lib/libllama-mtmd-cli.so",
        modelPath = "/m/model.gguf",
        mmprojPath = "/m/mmproj-model.gguf",
        imagePath = "/cache/card.jpg",
        prompt = "READ THE CARD",
        systemPrompt = systemPrompt,
        maxTokens = 512,
        threads = 4,
        useGpu = useGpu,
        family = family,
        grammarArgs = grammarArgs
    )

    private fun server(family: LlamaLaunch.Family = none, useGpu: Boolean = false, jinja: Boolean = false) =
        LlamaLaunch.Server(
            execPath = "/lib/libllama-server.so",
            modelPath = "/m/model.gguf",
            mmprojPath = "/m/mmproj-model.gguf",
            port = 8123,
            threads = 4,
            useGpu = useGpu,
            family = family,
            jinja = jinja
        )

    private val fastCores =
        listOf("-Cr", "4-7", "-Crb", "4-7", "--cpu-strict", "1", "--cpu-strict-batch", "1", "-fa", "on")

    // --- cliArgs ----------------------------------------------------------------------------

    @Test
    fun `cliArgs builds the one-shot command line in the original order`() {
        val args = LlamaLaunch.cliArgs(
            cli(systemPrompt = "SYS", grammarArgs = listOf("-jf", "/cache/schema.json"))
        )
        val expected = listOf(
            "/lib/libllama-mtmd-cli.so", "-m", "/m/model.gguf", "--mmproj", "/m/mmproj-model.gguf",
            "-jf", "/cache/schema.json",
            "--image", "/cache/card.jpg",
            "-sys", "SYS",
            "-p", "READ THE CARD", "-n", "512", "--temp", "0", "--repeat-penalty", "1.1",
            "-t", "4", "-tb", "4"
        ) + fastCores + listOf("--no-warmup", "-ngl", "0", "--no-mmproj-offload", "--no-op-offload")
        assertEquals(expected, args)
    }

    @Test
    fun `cliArgs leaves out -sys when there is no system prompt and uses the grammar file when given`() {
        val args = LlamaLaunch.cliArgs(cli(grammarArgs = listOf("--grammar-file", "/cache/g.gbnf")))
        assertFalse(args.contains("-sys"))
        assertEquals(listOf("--grammar-file", "/cache/g.gbnf"), args.subList(5, 7))
        assertEquals(listOf("--image", "/cache/card.jpg", "-p"), args.subList(7, 10))
    }

    @Test
    fun `cliArgs with no grammar sends none`() {
        val args = LlamaLaunch.cliArgs(cli())
        assertFalse(args.contains("-jf"))
        assertFalse(args.contains("--grammar-file"))
    }

    @Test
    fun `cliArgs offloads the language model to the GPU when asked`() {
        val args = LlamaLaunch.cliArgs(cli(useGpu = true))
        assertEquals(listOf("-ngl", "99", "--no-mmproj-offload"), args.takeLast(3))
        assertFalse(args.contains("--no-op-offload"))
    }

    @Test
    fun `cliArgs caps the context at 4096 for MiniCPM, LFM2 and Qwen but not for other models`() {
        for (family in listOf(miniCpm, lfm2, qwen)) {
            val args = LlamaLaunch.cliArgs(cli(family = family))
            assertEquals(1, args.count { it == "-c" })
            assertEquals("4096", args[args.indexOf("-c") + 1])
        }
        assertFalse(LlamaLaunch.cliArgs(cli(family = none)).contains("-c"))
    }

    @Test
    fun `cliArgs puts the context cap before the offload flags`() {
        val args = LlamaLaunch.cliArgs(cli(family = qwen))
        assertEquals(
            listOf("--no-warmup", "-c", "4096", "-ngl", "0", "--no-mmproj-offload", "--no-op-offload"),
            args.takeLast(7)
        )
    }

    // --- serverArgs -------------------------------------------------------------------------

    @Test
    fun `serverArgs for a model with no special settings`() {
        val expected = listOf(
            "/lib/libllama-server.so", "-m", "/m/model.gguf", "--mmproj", "/m/mmproj-model.gguf",
            "--port", "8123", "--host", "127.0.0.1", "-t", "4", "-tb", "4"
        ) + fastCores + listOf("-ngl", "0", "--no-mmproj-offload", "--no-op-offload")
        assertEquals(expected, LlamaLaunch.serverArgs(server()))
    }

    @Test
    fun `serverArgs only listens on the loopback interface`() {
        val args = LlamaLaunch.serverArgs(server())
        assertEquals("127.0.0.1", args[args.indexOf("--host") + 1])
    }

    @Test
    fun `serverArgs for Qwen uses one slot, no warm-up and a 512 MiB prompt cache`() {
        val args = LlamaLaunch.serverArgs(server(family = qwen))
        assertEquals(
            listOf("-c", "4096", "-np", "1", "--no-warmup", "--cache-ram", "512"),
            args.takeLast(7)
        )
    }

    @Test
    fun `serverArgs for LFM2 uses one slot and no warm-up but no prompt-cache limit`() {
        val args = LlamaLaunch.serverArgs(server(family = lfm2, jinja = true))
        assertEquals(listOf("--jinja", "-c", "4096", "-np", "1", "--no-warmup"), args.takeLast(6))
        assertFalse(args.contains("--cache-ram"))
    }

    @Test
    fun `serverArgs for MiniCPM only caps the context`() {
        val args = LlamaLaunch.serverArgs(server(family = miniCpm, jinja = true))
        assertEquals(listOf("--jinja", "-c", "4096"), args.takeLast(3))
        assertFalse(args.contains("-np"))
    }

    @Test
    fun `serverArgs asks for the chat template only when told to`() {
        assertFalse(LlamaLaunch.serverArgs(server(family = qwen, jinja = false)).contains("--jinja"))
        assertTrue(LlamaLaunch.serverArgs(server(family = qwen, jinja = true)).contains("--jinja"))
    }

    @Test
    fun `serverArgs offloads to the GPU when asked`() {
        val args = LlamaLaunch.serverArgs(server(useGpu = true))
        assertEquals(listOf("-ngl", "99", "--no-mmproj-offload"), args.takeLast(3))
    }

    // --- environment ------------------------------------------------------------------------

    @Test
    fun `environment always sets the library path and the image token cap`() {
        assertEquals(
            mapOf("LD_LIBRARY_PATH" to "/data/app/lib", "LLAMA_ARG_IMAGE_MAX_TOKENS" to "256"),
            LlamaLaunch.environment("/data/app/lib", tokenCap = 256, isQwen = false)
        )
    }

    @Test
    fun `environment pins Qwen to the four fast cores`() {
        val env = LlamaLaunch.environment("/data/app/lib", tokenCap = 1024, isQwen = true)
        assertEquals("{4},{5},{6},{7}", env["OMP_PLACES"])
        assertEquals("true", env["OMP_PROC_BIND"])
        assertEquals("1024", env["LLAMA_ARG_IMAGE_MAX_TOKENS"])
    }

    @Test
    fun `environment caps the vision edge at 768 only when asked`() {
        assertEquals(
            "768",
            LlamaLaunch.environment("/l", 256, isQwen = false, capVisionEdge = true)["MTMD_MAX_LONGEST_EDGE"]
        )
        assertFalse(LlamaLaunch.environment("/l", 256, isQwen = false).containsKey("MTMD_MAX_LONGEST_EDGE"))
    }
}
