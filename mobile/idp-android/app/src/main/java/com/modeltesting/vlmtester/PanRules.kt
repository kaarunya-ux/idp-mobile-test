package com.modeltesting.vlmtester

import com.modeltesting.vlmtester.ExtractionRules.checkDateShape
import com.modeltesting.vlmtester.ExtractionRules.coverageReport
import com.modeltesting.vlmtester.ExtractionRules.str

/** Checks on a PAN card record (pure; see ExtractionRules). */
object PanRules {
    fun validate(obj: org.json.JSONObject, currentYear: Int): String {
        val fields = listOf("name", "parent_name", "parent_relation", "dob", "pan_number")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "pan") problems += "document_type is not 'pan'"
        for (k in listOf("gender", "address", "expiry_date", "issue_date", "confidence")) {
            if (obj.has(k)) problems += "$k must not appear on a PAN record"
        }

        val name = str(obj, "name")
        val parent = str(obj, "parent_name")
        if (name != null && parent != null && name.equals(parent, ignoreCase = true)) {
            problems += "name and parent_name are identical"
        }

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear)

        val pan = str(obj, "pan_number")
        if (pan != null) checkPanNumber(pan, name, problems, notes)

        return coverageReport("PAN", obj, fields, problems, notes)
    }

    // PAN structure: 3 series letters, entity type, surname/given-name initial, 4
    // digits, check letter.
    private fun checkPanNumber(pan: String, name: String?, problems: MutableList<String>, notes: MutableList<String>) {
        if (!Regex("^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$").matches(pan)) {
            problems += "pan_number '$pan' is not a valid PAN structure"
        } else if (pan[3] == 'P') {
            checkPanInitial(pan, name, notes)
        }
    }

    // Individual: character 5 is meant to be the initial of the holder's
    // surname, but which WORD of "name" carries that surname is not fixed -
    // confirmed on two real cards. "D MANIKANDAN" (Tamil-style initial +
    // given name) matched its LAST word (M); "KOCHERLA SRIKANTH" (surname
    // first) matched its FIRST word (K), and both pan_number/name reads were
    // independently verified character-perfect against the physical card.
    // So: check every word in name, not one fixed position, and only note a
    // possible mismatch rather than call it a "problem" - a real, correct
    // read can legitimately match none of them for a naming style not yet
    // seen, and this check must never drag a correct read down to LOW.
    private fun checkPanInitial(pan: String, name: String?, notes: MutableList<String>) {
        val nameInitials = name?.trim()?.split(Regex("\\s+"))
            ?.mapNotNull { it.firstOrNull()?.uppercaseChar() }.orEmpty()
        if (nameInitials.isNotEmpty() && pan[4] !in nameInitials) {
            notes += "note: pan_number char 5 '${pan[4]}' matches none of name's " +
                "word-initials (${nameInitials.joinToString("")}) - " +
                "worth a manual glance, not necessarily wrong"
        }
    }

    /** Crop prompts return a bare value, not JSON - no parseSide/leak-check applies (there's
     *  no prompt-shaped JSON structure to echo back). Just a direct format check per field. */
    // pan_crop's prompt doesn't tell the model (or us) in advance which of the four PAN
    // fields a given crop shows, so the field type is inferred here from the shape of
    // whatever came back rather than fixed per doc type. PAN number and date shapes are
    // unambiguous; a name-shaped value can't be told apart from a parent's name-shaped
    // value by shape alone, so that case is reported as such rather than guessed.
    fun validateCrop(docType: String, text: String): String {
        val value = text.trim()
        return when (docType) {
            "pan_crop" -> {
                val panPattern = Regex("^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$")
                val dobPattern = Regex("^\\d{2}/\\d{2}/\\d{4}$")
                val namePattern = Regex("^[A-Z][A-Z. ]*[A-Z.]$")
                when {
                    panPattern.matches(value) -> "PAN crop check: OK (PAN number)"
                    dobPattern.matches(value) -> "PAN crop check: OK (date of birth)"
                    namePattern.matches(value) ->
                        "PAN crop check: OK (name-shaped value - could be the cardholder's " +
                            "name or a parent's name; the crop alone can't tell which)"
                    else ->
                        "PAN crop check: FAILED - \"$value\" does not match PAN number, " +
                            "date, or name format"
                }
            }
            else -> "Unknown crop document type: $docType"
        }
    }
}
