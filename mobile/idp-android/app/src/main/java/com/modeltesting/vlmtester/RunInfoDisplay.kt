package com.modeltesting.vlmtester

/**
 * Turns the run header and the diagnostics line (the same text saved to the run log) into a short
 * "Label: value" list for the run-details card under the live stats. Pure, so it runs in JVM unit
 * tests. A value that is not in the text is left out.
 */
object RunInfoDisplay {
    private const val ONE_SHOT = "one-shot (model loaded for this scan)"
    private val pair = Regex("""(\w+)=(\S+)""")
    private val server = Regex("""server=(WARM|COLD)(\([^)]*\))?""")

    fun format(header: String, diag: String): String {
        val parts = header.trim().removePrefix("[").removeSuffix("]").split(" | ")
        val head = parts.mapNotNull { pair.matchEntire(it)?.let { m -> m.groupValues[1] to m.groupValues[2] } }.toMap()
        val d = pair.findAll(diag).associate { it.groupValues[1] to it.groupValues[2] }
        val lines = mutableListOf<Pair<String, String?>>()
        lines += "Document" to parts.getOrNull(0)
        lines += "Processor" to parts.firstOrNull { it.contains("only") || it.startsWith("GPU") }
        lines += "Mode" to (parts.firstOrNull { it.endsWith("mode") } ?: ONE_SHOT)
        lines += "Model" to head["model"]
        lines += "Model id" to listOfNotNull(head["registryModel"], head["sha256"]).joinToString(" ").ifEmpty { null }
        lines += "Threads" to head["threads"]
        lines += "Image-token limit (max)" to head["cap"]
        lines += "Profile" to head["profile"]
        lines += "Prompt" to listOfNotNull(head["promptVersion"], head["systemPromptVersion"]).joinToString(" + ")
            .ifEmpty { null }
        lines += "Image" to d["img"]?.let { it + (d["prep"]?.let { p -> ", prep ${prepSeconds(diag, p)}" } ?: "") }
        lines += "Image tokens (estimate)" to estimatedImageTokens(d["img"], head["cap"])
        lines += "Server" to server.find(diag)?.value?.removePrefix("server=")
        lines += "Tokens" to tokens(d)
        lines += "Time" to timing(d, parts.lastOrNull())
        return lines.filter { !it.second.isNullOrBlank() }.joinToString("\n") { (k, v) -> "$k: $v" }
    }

    /** About one token per 32x32 pixels, never above the limit; an estimate, the log only has the combined count. */
    private fun estimatedImageTokens(img: String?, cap: String?): String? {
        val size = img?.split("x")?.mapNotNull { it.toIntOrNull() }?.takeIf { it.size == 2 } ?: return null
        val estimate = (size[0] * size[1] / PIXELS_PER_TOKEN).coerceAtMost(cap?.toIntOrNull() ?: Int.MAX_VALUE)
        return "about $estimate"
    }

    private const val PIXELS_PER_TOKEN = 1024

    private fun prepSeconds(diag: String, kind: String): String =
        Regex("""prep=$kind (\S+)s""").find(diag)?.let { "$kind ${it.groupValues[1]} s" } ?: kind

    private fun tokens(d: Map<String, String>): String? {
        val prompt = d["prompt_tokens"] ?: return null
        val cached = d["cache_n"]?.takeIf { it != "0" }?.let { ", $it reused from cache" } ?: ""
        return "$prompt prompt, ${d["completion_tokens"] ?: "?"} answer$cached"
    }

    private fun timing(d: Map<String, String>, total: String?): String? {
        val read = d["prompt_ms"]?.toDoubleOrNull()?.div(1000)
        val write = d["predicted_ms"]?.toDoubleOrNull()?.div(1000)
        val split = if (read != null && write != null) "read %.1f s, write %.1f s, ".format(read, write) else ""
        return total?.let { split + "total " + it.replace("s", " s") }
    }
}
