package com.modeltesting.vlmtester

import org.json.JSONObject

/**
 * The text the output panel shows after a scan: one "Label: value" line per extracted field and the
 * verdict, without the run diagnostics. The full text (diagnostics, raw answer, report) is still
 * saved to the run log unchanged. Pure, so it runs in JVM unit tests.
 */
object ResultDisplay {
    private const val EMPTY = "-"
    private val derivedLine = Regex("""^\s+\S+ -> """)

    fun format(runNumber: Int, docType: String, elapsedSec: Double, answer: String, report: String): String {
        val fields = parseFields(answer)
        val body = if (fields == null) answer.trim() else fields.joinToString("\n") { (k, v) -> "${label(k)}: $v" }
        val verdict = report.lines()
            .filter { it.isNotBlank() && !derivedLine.containsMatchIn(it) }
            .joinToString("\n") { it.trim() }
        return "Run $runNumber - $docType - %.1f s\n\n$body\n\n$verdict".format(elapsedSec).trim()
    }

    /** Field name and value in the order the model wrote them; null when the answer is not a JSON object. */
    private fun parseFields(answer: String): List<Pair<String, String>>? {
        val obj = OutputParser.parseSide("", answer).first ?: return null
        return obj.keys().asSequence().map { it to value(obj.opt(it)) }.toList()
    }

    private fun value(raw: Any?): String {
        val text = raw?.toString()?.trim().orEmpty()
        return if (raw == null || raw == JSONObject.NULL || text.isEmpty()) EMPTY else text.replace("\n", ", ")
    }

    private fun label(key: String): String = key.replace('_', ' ').replaceFirstChar { it.uppercase() }
}
