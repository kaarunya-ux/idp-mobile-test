package com.modeltesting.vlmtester

/**
 * Prompt files as the Python Engineering Standard asks for them (LLM-02, IDP-03 Ch. 4.4): one
 * versioned file per prompt, named `<name>.v<n>.md`, with front matter giving the model,
 * temperature and schema name. The text after the front matter is the prompt, byte for byte; the
 * front matter is never sent to the model. The file's base name (`user_pan_full.v1`) is the
 * prompt version recorded on every run, in the same form as the standard's `field-read.v2`.
 *
 * A prompt change is a new version file, not an edit: add `<name>.v2.md`, point the code at it
 * here or in ExtractionPlan, and re-run the evaluation before it ships (LLM-02, ML-12).
 */
object PromptFile {
    /** The shared system prompt. */
    const val SYSTEM = "prompts/system.v1.md"

    /** The instruction prompt for [name], for example `pan_full` or `aadhaar_qwen`. */
    fun userAsset(name: String): String = "prompts/user_$name.v1.md"

    /** What the front matter says about a prompt, and the prompt text itself. */
    data class Parsed(val model: String, val temperature: String, val schema: String, val body: String)

    /**
     * Splits a prompt file into its front matter and body. Fails loudly if the front matter is
     * missing or incomplete.
     */
    fun parse(text: String): Parsed {
        require(text.startsWith(FENCE)) { "prompt file has no front matter (it must start with ---)" }
        val closing = text.indexOf("\n$FENCE", startIndex = FENCE.length - 1)
        require(closing >= 0) { "prompt file front matter is not closed with ---" }
        val header = if (closing < FENCE.length) "" else text.substring(FENCE.length, closing)
        val fields = header.lines().filter { it.isNotBlank() }.associate {
            val key = it.substringBefore(':').trim()
            key to it.substringAfter(':', "").trim()
        }
        fun required(key: String): String = requireNotNull(fields[key]) { "prompt front matter is missing '$key'" }
        return Parsed(
            model = required("model"),
            temperature = required("temperature"),
            schema = required("schema"),
            body = text.substring(closing + FENCE.length + 1)
        )
    }

    /** Just the prompt text. */
    fun body(text: String): String = parse(text).body

    /** `prompts/user_pan_full.v1.md` becomes `user_pan_full.v1`. */
    fun versionOf(assetPath: String): String = assetPath.substringAfterLast('/').removeSuffix(".md")

    /** The run-log header fields that name the prompt versions used. */
    fun provenance(promptAsset: String, systemPromptAsset: String?): String =
        " | promptVersion=${versionOf(promptAsset)}" +
            (systemPromptAsset?.let { " | systemPromptVersion=${versionOf(it)}" } ?: "")

    private const val FENCE = "---\n"
}
