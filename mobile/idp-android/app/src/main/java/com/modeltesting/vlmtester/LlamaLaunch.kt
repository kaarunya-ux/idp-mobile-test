package com.modeltesting.vlmtester

/**
 * The command line and environment for the two ways the app runs llama.cpp: a one-shot
 * `llama-mtmd-cli` process per scan, and a persistent `llama-server` on the loopback interface.
 * Pure (lists and maps in, lists and maps out), so the exact flags can be unit-tested; the
 * process itself is started by MainActivity. Every flag and its measurement note was moved here
 * unchanged. The caller decides the model family from the file name (see ModelProfiles).
 *
 * Several settings are hard-coded for the Exynos 1380 phone the numbers were measured on (the
 * four fast cores are CPUs 4 to 7); revisit them before running on a different chipset.
 */
object LlamaLaunch {
    /** Which model family is loaded, as decided from the file name. */
    data class Family(val isMiniCpm: Boolean, val isLfm2: Boolean, val isQwen: Boolean)

    /** One `llama-mtmd-cli` run. [systemPrompt] is null when no `-sys` argument is wanted. */
    data class Cli(
        val execPath: String,
        val modelPath: String,
        val mmprojPath: String,
        val imagePath: String,
        val prompt: String,
        val systemPrompt: String?,
        val maxTokens: Int,
        val threads: Int,
        val useGpu: Boolean,
        val family: Family,
        val grammarArgs: List<String>
    )

    /** One persistent `llama-server`. [jinja] asks for the model's own chat template. */
    data class Server(
        val execPath: String,
        val modelPath: String,
        val mmprojPath: String,
        val port: Int,
        val threads: Int,
        val useGpu: Boolean,
        val family: Family,
        val jinja: Boolean
    )

    fun cliArgs(cli: Cli): List<String> {
        val args = mutableListOf(cli.execPath, "-m", cli.modelPath, "--mmproj", cli.mmprojPath)
        args += cli.grammarArgs
        args += listOf("--image", cli.imagePath)
        cli.systemPrompt?.let { args += listOf("-sys", it) }
        args += listOf(
            "-p",
            cli.prompt,
            "-n",
            cli.maxTokens.toString(),
            "--temp",
            "0",
            // llama.cpp defaults this to 1.0 (disabled) - confirmed in common/common.h.
            // Applies regardless of temp=0/greedy decoding: it discourages exact-text
            // repetition loops, a distinct failure mode from sampling randomness.
            "--repeat-penalty",
            "1.1",
            "-t",
            cli.threads.toString(),
            "-tb",
            cli.threads.toString()
        )
        args += FAST_CORES_AND_FLASH_ATTENTION
        // llama.cpp runs an extra warm-up forward pass on every model load by
        // default. Measured on-device 2026-09-29 with MiniCPM-V 4.6 / pan_full:
        // 58.6s with it vs 54.5s without (-4.1s), and byte-identical output under
        // --temp 0. Only costs anything on the CLI path, which reloads the model
        // every run - a warm server pays it once at startup and is unaffected.
        args += "--no-warmup"
        // MiniCPM-V only: with no -c, llama.cpp uses the model's full 262144-token context
        // and reserves a ~3 GB KV cache up front (1.9s just to allocate). Real usage is
        // ~400 tokens, so cap it. Measured on-device 2026-09-30, MiniCPM-V 4.6 / pan_full,
        // same card, byte-identical output: 44.3s -> 38.1s (crop), 48.2s -> 42.5s avg
        // over 4 camera-frame simulations. (-fit off was also tried: no gain, not used.)
        if (cli.family.isMiniCpm) args += CONTEXT_4096
        // LFM2-VL: the GGUF's default context is 128,000 tokens, so the same unneeded KV
        // allocation applies. Real prompts are 190-260 tokens; every LFM2 measurement in
        // LFM2_VL_3B_LATENCY_FINDINGS.md ran with -c 4096.
        if (cli.family.isLfm2) args += CONTEXT_4096
        // Qwen3-VL: same unneeded KV allocation, and on the 4B it also swapped.
        if (cli.family.isQwen) args += CONTEXT_4096
        args += offloadArgs(cli.useGpu)
        return args
    }

    fun serverArgs(server: Server): List<String> {
        val args = mutableListOf(
            server.execPath,
            "-m",
            server.modelPath,
            "--mmproj",
            server.mmprojPath,
            "--port",
            server.port.toString(),
            "--host",
            "127.0.0.1",
            "-t",
            server.threads.toString(),
            "-tb",
            server.threads.toString()
        )
        args += FAST_CORES_AND_FLASH_ATTENTION
        args += offloadArgs(server.useGpu)
        if (server.jinja) args += "--jinja"
        // Same 262144-token default-context KV waste as the CLI path - see cliArgs.
        if (server.family.isMiniCpm) args += CONTEXT_4096
        // LFM2-VL: the exact server configuration measured in
        // LFM2_VL_3B_LATENCY_FINDINGS.md section 10 (33.3s per scan warm against 35.7s cold
        // CLI, identical output): -c 4096, one slot, no warm-up decode at startup.
        if (server.family.isLfm2) args += CONTEXT_4096 + ONE_SLOT_NO_WARMUP
        // Qwen3-VL: the LFM2 server configuration above (one slot, -c 4096, no warm-up) for the
        // same reason. The first request after a start is slow (~85s) because the weights are
        // paged in lazily under --no-warmup; the app's warm-up scan absorbs that.
        // --cache-ram: llama-server keeps each finished prompt's KV state in RAM for
        // reuse, up to 8192 MiB by default - more than this phone has. Each PAN scan adds
        // roughly 40 MiB (about 260 tokens at ~150 KB/token), so a long session would
        // grow into the 3.5 GB the model already holds. 512 MiB keeps the last dozen or so.
        if (server.family.isQwen) args += CONTEXT_4096 + ONE_SLOT_NO_WARMUP + listOf("--cache-ram", "512")
        return args
    }

    /**
     * Environment for either process. [tokenCap] is llama.cpp's own upstream `--image-max-tokens`
     * equivalent: it is baked in at model-load time before any per-request preprocessing runs, so
     * there is no ordering or no-op risk the way a runtime override has. It is only read by
     * models with dynamic image resolution (Qwen); for the others it is a no-op. There is
     * deliberately no minimum-token floor: forcing crops up to a token count they don't natively
     * have measured worse on both accuracy and latency than leaving Qwen's own stock 8-token floor
     * alone.
     *
     * [isQwen] keeps the model's threads on the four performance cores only (Cortex-A78, cores
     * 4-7 on the Exynos 1380; cores 0-3 are the slow A55s). Running on the efficiency cores too
     * was tried and is slower. The -Cr/--cpu-strict flags do nothing in this build
     * (GGML_OPENMP=ON), so OpenMP is told directly. In the adb harness 2026-10-06 (4B, same two
     * variables) this changed no timing: it is a guard against a thread drifting onto an
     * efficiency core, not a speed-up.
     *
     * [capVisionEdge] caps the vision-preprocessing upscale target at 768 px instead of the
     * model's hard-coded 2048 px default (LATENCY_OPTIMIZATION.md section 2, Stage 3). Only
     * meaningful to VisionPsy's idefics3 preprocessor; the server path asks for it only for
     * models other than MiniCPM-V and LFM2.
     */
    fun environment(
        nativeLibraryDir: String,
        tokenCap: Int,
        isQwen: Boolean,
        capVisionEdge: Boolean = false
    ): Map<String, String> {
        val env = mutableMapOf(
            "LD_LIBRARY_PATH" to nativeLibraryDir,
            "LLAMA_ARG_IMAGE_MAX_TOKENS" to tokenCap.toString()
        )
        if (isQwen) {
            env["OMP_PLACES"] = "{4},{5},{6},{7}"
            env["OMP_PROC_BIND"] = "true"
        }
        if (capVisionEdge) env["MTMD_MAX_LONGEST_EDGE"] = "768"
        return env
    }

    private fun offloadArgs(useGpu: Boolean): List<String> = if (useGpu) {
        // Offload the LM to GPU; vision encoder (mmproj) stays on CPU regardless.
        listOf("-ngl", "99", "--no-mmproj-offload")
    } else {
        // -ngl 0 only keeps weights off the GPU; op_offload would still reroute
        // large prefill matmuls to Vulkan, so disable that explicitly too.
        listOf("-ngl", "0", "--no-mmproj-offload", "--no-op-offload")
    }

    // Exynos 1380-specific: cores 4-7 are the 4 fast A78 cores (0-3 are the slow A55s) -
    // confirmed via /proc/cpuinfo CPU part IDs (0xd41 vs 0xd05) on two separate M35 5G units.
    // Without this, -t/-tb only sets a thread COUNT; the scheduler can still migrate those
    // threads onto the slow cores, which is the most likely explanation for the 8-16 tok/s
    // prefill-rate swings seen across otherwise-identical requests. Not auto-detected.
    // -fa on: 'auto' (the default) was leaving flash attention off in practice; forcing it on
    // measurably helped prefill throughput in on-device testing 2026-09-24.
    private val FAST_CORES_AND_FLASH_ATTENTION = listOf(
        "-Cr",
        "4-7",
        "-Crb",
        "4-7",
        "--cpu-strict",
        "1",
        "--cpu-strict-batch",
        "1",
        "-fa",
        "on"
    )

    private val CONTEXT_4096 = listOf("-c", "4096")
    private val ONE_SLOT_NO_WARMUP = listOf("-np", "1", "--no-warmup")
}
