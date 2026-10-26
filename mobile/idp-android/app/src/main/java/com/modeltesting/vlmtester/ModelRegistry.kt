package com.modeltesting.vlmtester

/**
 * The model registry (`assets/models.yaml`, Python Engineering Standard ML-01): every model the
 * app can run, with name, version, source, SHA-256, runtime and device. The reader accepts only
 * the small, fixed shape that file uses and rejects anything else with the line number, so a
 * typo cannot silently turn into a missing field. Pure, so it runs in plain JVM unit tests.
 *
 * The run log records which registry model produced a result (ML-04). `null` in the file means
 * "not known yet" and is reported as such, never filled in.
 */
object ModelRegistry {
    /** One registered model. [file] is the language-model file the app looks for in its models folder. */
    data class Entry(
        val name: String,
        val version: String?,
        val source: String?,
        val sha256: String?,
        val runtime: String,
        val device: String,
        val file: String?,
        val mmproj: String?
    )

    private val keys = setOf("name", "version", "source", "sha256", "runtime", "device", "file", "mmproj")
    private const val ENTRY_PREFIX = "  - "
    private const val FIELD_PREFIX = "    "
    private const val SHA_SHORT_LENGTH = 12

    /** Reads the registry text. Throws IllegalArgumentException, naming the line, on anything unexpected. */
    fun parse(text: String): List<Entry> {
        val rows = mutableListOf<MutableMap<String, String?>>()
        var sawHeader = false
        text.lines().forEachIndexed { index, line ->
            val lineNumber = index + 1
            when {
                line.isBlank() || line.trimStart().startsWith("#") -> Unit
                line == "models:" -> sawHeader = true
                !sawHeader -> fail(lineNumber, "expected 'models:' first")
                line.startsWith(ENTRY_PREFIX) -> {
                    val row = mutableMapOf<String, String?>()
                    rows += row
                    addField(row, line.removePrefix(ENTRY_PREFIX), lineNumber)
                }
                line.startsWith(FIELD_PREFIX) && rows.isNotEmpty() ->
                    addField(rows.last(), line.removePrefix(FIELD_PREFIX), lineNumber)
                else -> fail(lineNumber, "unexpected line '$line'")
            }
        }
        require(sawHeader) { "model registry has no 'models:' list" }
        return rows.mapIndexed { index, row -> toEntry(row, index + 1) }
    }

    /** The registered model whose language-model file is [fileName], or null. */
    fun find(entries: List<Entry>, fileName: String): Entry? = entries.firstOrNull { it.file == fileName }

    /** The run-log header fields that name the registry model: `registryModel=name@version`, runtime, checksum. */
    fun provenance(entry: Entry?): String {
        if (entry == null) return " | registryModel=unregistered"
        return " | registryModel=${entry.name}@${entry.version ?: "unversioned"} | runtime=${entry.runtime}" +
            (entry.sha256?.let { " | sha256=${it.take(SHA_SHORT_LENGTH)}" } ?: "")
    }

    private fun addField(row: MutableMap<String, String?>, text: String, lineNumber: Int) {
        val key = text.substringBefore(':').trim()
        if (!text.contains(':') || key !in keys) fail(lineNumber, "unknown or malformed field '$text'")
        if (row.containsKey(key)) fail(lineNumber, "field '$key' appears twice in one entry")
        row[key] = scalar(text.substringAfter(':').trim())
    }

    private fun scalar(value: String): String? = when {
        value == "null" -> null
        value.length >= 2 && value.startsWith("\"") && value.endsWith("\"") -> value.substring(1, value.length - 1)
        else -> value
    }

    private fun toEntry(row: Map<String, String?>, position: Int): Entry {
        fun required(key: String): String = requireNotNull(row[key]) { "registry entry $position is missing '$key'" }
        return Entry(
            name = required("name"),
            version = row["version"],
            source = row["source"],
            sha256 = row["sha256"],
            runtime = required("runtime"),
            device = required("device"),
            file = row["file"],
            mmproj = row["mmproj"]
        )
    }

    private fun fail(lineNumber: Int, message: String): Nothing =
        throw IllegalArgumentException("model registry line $lineNumber: $message")
}
