package com.modeltesting.vlmtester

/**
 * Shared building blocks for the per-document checks (PanRules, AadhaarRules, DlRules,
 * PassportRules): date normalisation, the Aadhaar (Verhoeff) checksum and the coverage
 * grade. Pure functions of their inputs, with no Android or activity state, so they run
 * in plain JVM unit tests (app/src/test). The calendar year is a parameter, never read
 * from the clock here.
 *
 * Moved out of MainActivity without changing behaviour; MainActivity keeps thin
 * delegates with the same names.
 */
object ExtractionRules {
    fun str(obj: org.json.JSONObject, k: String): String? =
        if (!obj.has(k) || obj.isNull(k)) null else obj.optString(k, null)

    /** Converts a date the model copied verbatim off a document into ISO form.
     *  Handles DD/MM/YYYY, DD-MM-YYYY (numeric, hyphens - the usual Indian DL format,
     *  missed until a live DL test on 2026-09-16 falsely flagged 3 correct dates as
     *  "not recognised"), DD-MMM-YYYY (3-letter English month, either case), and
     *  YYYY-MM-DD (found live on a Trinidad & Tobago National ID, 2026-09-16 - all
     *  three of its dates were falsely flagged "not recognised" because this shape
     *  had never come up on an Indian document; T&T apparently prints dates in ISO
     *  order rather than DD/MM/YYYY). The model has never once performed date-format
     *  conversion itself in testing, so every prompt asks it to copy the date exactly
     *  as printed and this always runs afterwards instead - deterministic, and free.
     *  Returns null if the shape isn't recognised. */
    fun toIso(raw: String?): String? {
        if (raw == null) return null
        Regex("^([12][0-9]{3})-([01][0-9])-([0-3][0-9])$").find(raw)?.let { m ->
            val (y, mo, d) = m.destructured
            return "$y-$mo-$d"
        }
        Regex("^([0-3][0-9])[/-]([01][0-9])[/-]([12][0-9]{3})$").find(raw)?.let { m ->
            val (d, mo, y) = m.destructured
            return "$y-$mo-$d"
        }
        val months = mapOf(
            "JAN" to "01", "FEB" to "02", "MAR" to "03", "APR" to "04",
            "MAY" to "05", "JUN" to "06", "JUL" to "07", "AUG" to "08",
            "SEP" to "09", "OCT" to "10", "NOV" to "11", "DEC" to "12"
        )
        Regex("^([0-3][0-9])-([A-Za-z]{3})-([12][0-9]{3})$").find(raw)?.let { m ->
            val (d, mon, y) = m.destructured
            val mo = months[mon.uppercase()] ?: return null
            return "$y-$mo-$d"
        }
        return null
    }

    fun checkDateShape(
        problems: MutableList<String>,
        notes: MutableList<String>,
        obj: org.json.JSONObject,
        key: String,
        minYear: Int,
        maxYear: Int
    ) {
        val raw = str(obj, key) ?: return
        val iso = toIso(raw)
        if (iso == null) {
            problems += "$key '$raw' is not a recognised date format"
            return
        }
        val (y, mo, d) = iso.split("-").map { it.toInt() }
        if (d !in 1..31) problems += "$key day out of range"
        if (mo !in 1..12) problems += "$key month out of range"
        if (d in 1..31 && mo in 1..12 && d > daysInMonth(y, mo)) {
            problems += "$key day $d does not exist in month $mo of $y"
        }
        if (y !in minYear..maxYear) problems += "$key year $y implausible"
        if (problems.none { it.startsWith(key) }) notes += "$key -> $iso"
    }

    /** Days in [month] (1 to 12) of [year], leap years included. Found 2026-10-23: a PAN date of
     *  birth read as 31/09/2001 (the card says 31/01/2001) graded HIGH because only 1..31 was checked. */
    private fun daysInMonth(year: Int, month: Int): Int = when (month) {
        2 -> if (year % 4 == 0 && (year % 100 != 0 || year % 400 == 0)) 29 else 28
        4, 6, 9, 11 -> 30
        else -> 31
    }

    // Verhoeff checksum tables - the algorithm UIDAI uses for the 12th (check) digit of
    // every Aadhaar number. Detects essentially all single-digit misreads and adjacent
    // transpositions, so it is a much stronger signal than "is this 12 digits". Table
    // values verified against the standard test vector (base "236" -> check digit "3").
    private val verhoeffD = arrayOf(
        intArrayOf(0, 1, 2, 3, 4, 5, 6, 7, 8, 9), intArrayOf(1, 2, 3, 4, 0, 6, 7, 8, 9, 5),
        intArrayOf(2, 3, 4, 0, 1, 7, 8, 9, 5, 6), intArrayOf(3, 4, 0, 1, 2, 8, 9, 5, 6, 7),
        intArrayOf(4, 0, 1, 2, 3, 9, 5, 6, 7, 8), intArrayOf(5, 9, 8, 7, 6, 0, 4, 3, 2, 1),
        intArrayOf(6, 5, 9, 8, 7, 1, 0, 4, 3, 2), intArrayOf(7, 6, 5, 9, 8, 2, 1, 0, 4, 3),
        intArrayOf(8, 7, 6, 5, 9, 3, 2, 1, 0, 4), intArrayOf(9, 8, 7, 6, 5, 4, 3, 2, 1, 0)
    )
    private val verhoeffP = arrayOf(
        intArrayOf(0, 1, 2, 3, 4, 5, 6, 7, 8, 9),
        intArrayOf(1, 5, 7, 6, 2, 8, 3, 0, 9, 4),
        intArrayOf(5, 8, 0, 3, 7, 9, 6, 1, 4, 2),
        intArrayOf(8, 9, 1, 6, 0, 4, 3, 5, 2, 7),
        intArrayOf(9, 4, 5, 3, 1, 2, 6, 8, 7, 0),
        intArrayOf(4, 2, 8, 6, 5, 7, 3, 9, 0, 1),
        intArrayOf(2, 7, 9, 3, 8, 0, 6, 4, 1, 5),
        intArrayOf(7, 0, 4, 6, 9, 1, 3, 2, 5, 8)
    )
    fun verhoeffValid(number: String): Boolean {
        var c = 0
        for ((i, ch) in number.reversed().withIndex()) {
            c = verhoeffD[c][verhoeffP[i % 8][ch - '0']]
        }
        return c == 0
    }

    fun coverageReport(
        label: String,
        obj: org.json.JSONObject,
        readableFields: List<String>,
        problems: List<String>,
        notes: List<String>,
        oftenNullFields: List<String> = emptyList()
    ): String {
        val read = readableFields.count { str(obj, it) != null }
        val total = readableFields.size
        val nulls = readableFields.filter { str(obj, it) == null }
        val sb = StringBuilder("$label check: ${grade(read, total, problems)} - $read/$total fields read")
        if (nulls.isNotEmpty()) sb.append(", null: ${nulls.joinToString(", ")}")
        // Fields the document itself often doesn't print at all (confirmed by the
        // user for tt_id's social_assistance_number/blood_group/national_insurance_
        // number) are shown for information only - a null here is normal, not a
        // miss, so they're excluded from the read/total ratio that drives grade.
        if (oftenNullFields.isNotEmpty()) {
            sb.append("\n  often blank on this document, not graded: ")
            sb.append(oftenNullFields.joinToString(", ") { "$it=${str(obj, it) ?: "null"}" })
        }
        if (notes.isNotEmpty()) sb.append("\n").append(notes.joinToString("\n") { "  $it" })
        if (problems.isNotEmpty()) sb.append("\n").append(problems.joinToString("\n") { "  - $it" })
        return sb.toString()
    }

    private fun grade(read: Int, total: Int, problems: List<String>): String = when {
        problems.isNotEmpty() -> "LOW"
        read == total -> "HIGH"
        read >= total - 1 -> "MEDIUM"
        else -> "LOW"
    }

    /** The LFM2 grammar has no document_type key (the BUL2 prompt does not ask for it) but
     *  validatePan() requires document_type == "pan". It is added only for the check; the
     *  output shown to the user is the model's own, unmodified text. The Qwen PAN and
     *  Aadhaar grammars are the same shape (no document_type), so they use this too. */
    fun withDocumentType(text: String, type: String): String {
        val start = text.indexOf('{')
        val end = text.lastIndexOf('}')
        if (start < 0 || end <= start) return text
        return try {
            val obj = org.json.JSONObject(text.substring(start, end + 1))
            if (!obj.has("document_type")) obj.put("document_type", type)
            obj.toString()
        } catch (_: org.json.JSONException) {
            text
        }
    }
}
