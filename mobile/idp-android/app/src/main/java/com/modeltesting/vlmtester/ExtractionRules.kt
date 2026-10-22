package com.modeltesting.vlmtester

/**
 * Deterministic checks on what the model returned: date normalisation, the Aadhaar
 * (Verhoeff) checksum, PAN structure and the coverage grade. Pure functions of their
 * inputs, with no Android or activity state, so they run in plain JVM unit tests
 * (app/src/test). The calendar year is a parameter, never read from the clock here.
 *
 * Moved out of MainActivity without changing behaviour; MainActivity keeps thin
 * delegates with the same names so the remaining validators are untouched.
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
        problems: MutableList<String>, notes: MutableList<String>,
        obj: org.json.JSONObject, key: String, minYear: Int, maxYear: Int
    ) {
        val raw = str(obj, key) ?: return
        val iso = toIso(raw)
        if (iso == null) { problems += "$key '$raw' is not a recognised date format"; return }
        val (y, mo, d) = iso.split("-").map { it.toInt() }
        if (d !in 1..31) problems += "$key day out of range"
        if (mo !in 1..12) problems += "$key month out of range"
        if (y !in minYear..maxYear) problems += "$key year $y implausible"
        if (problems.none { it.startsWith(key) }) notes += "$key -> $iso"
    }

    // Verhoeff checksum tables - the algorithm UIDAI uses for the 12th (check) digit of
    // every Aadhaar number. Detects essentially all single-digit misreads and adjacent
    // transpositions, so it is a much stronger signal than "is this 12 digits". Table
    // values verified against the standard test vector (base "236" -> check digit "3").
    private val verhoeffD = arrayOf(
        intArrayOf(0,1,2,3,4,5,6,7,8,9), intArrayOf(1,2,3,4,0,6,7,8,9,5),
        intArrayOf(2,3,4,0,1,7,8,9,5,6), intArrayOf(3,4,0,1,2,8,9,5,6,7),
        intArrayOf(4,0,1,2,3,9,5,6,7,8), intArrayOf(5,9,8,7,6,0,4,3,2,1),
        intArrayOf(6,5,9,8,7,1,0,4,3,2), intArrayOf(7,6,5,9,8,2,1,0,4,3),
        intArrayOf(8,7,6,5,9,3,2,1,0,4), intArrayOf(9,8,7,6,5,4,3,2,1,0)
    )
    private val verhoeffP = arrayOf(
        intArrayOf(0,1,2,3,4,5,6,7,8,9), intArrayOf(1,5,7,6,2,8,3,0,9,4),
        intArrayOf(5,8,0,3,7,9,6,1,4,2), intArrayOf(8,9,1,6,0,4,3,5,2,7),
        intArrayOf(9,4,5,3,1,2,6,8,7,0), intArrayOf(4,2,8,6,5,7,3,9,0,1),
        intArrayOf(2,7,9,3,8,0,6,4,1,5), intArrayOf(7,0,4,6,9,1,3,2,5,8)
    )
    fun verhoeffValid(number: String): Boolean {
        var c = 0
        for ((i, ch) in number.reversed().withIndex()) {
            c = verhoeffD[c][verhoeffP[i % 8][ch - '0']]
        }
        return c == 0
    }

    fun coverageReport(
        label: String, obj: org.json.JSONObject, readableFields: List<String>,
        problems: List<String>, notes: List<String>, oftenNullFields: List<String> = emptyList()
    ): String {
        val read = readableFields.count { str(obj, it) != null }
        val total = readableFields.size
        val nulls = readableFields.filter { str(obj, it) == null }
        val grade = when {
            problems.isNotEmpty() -> "LOW"
            read == total -> "HIGH"
            read >= total - 1 -> "MEDIUM"
            else -> "LOW"
        }
        val sb = StringBuilder("$label check: $grade - $read/$total fields read")
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

    fun validatePan(obj: org.json.JSONObject, currentYear: Int): String {
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

        // PAN structure: 3 series letters, entity type, surname/given-name initial, 4
        // digits, check letter.
        val pan = str(obj, "pan_number")
        if (pan != null) {
            if (!Regex("^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$").matches(pan)) {
                problems += "pan_number '$pan' is not a valid PAN structure"
            } else if (pan[3] == 'P') {
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
                val nameInitials = name?.trim()?.split(Regex("\\s+"))
                    ?.mapNotNull { it.firstOrNull()?.uppercaseChar() }.orEmpty()
                if (nameInitials.isNotEmpty() && pan[4] !in nameInitials) {
                    notes += "note: pan_number char 5 '${pan[4]}' matches none of name's " +
                        "word-initials (${nameInitials.joinToString("")}) - " +
                        "worth a manual glance, not necessarily wrong"
                }
            }
        }

        return coverageReport("PAN", obj, fields, problems, notes)
    }

    fun validateAadhaar(obj: org.json.JSONObject, currentYear: Int): String {
        // Qwen profile only: address_visible=false means the card genuinely prints no address, so
        // address/pincode are not misses - leave them out of the graded fields (shown for
        // information instead) so a front-only card can grade HIGH.
        val addressDeclaredAbsent = obj.has("address_visible") && !obj.optBoolean("address_visible", false)
        val fields = if (addressDeclaredAbsent) listOf("name", "dob", "gender", "aadhaar_number")
                     else listOf("name", "dob", "gender", "aadhaar_number", "address", "pincode")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "aadhaar") problems += "document_type is not 'aadhaar'"
        // issue_date was dropped from the schema entirely: it's printed in a corner
        // stamp too small for this vision encoder to reliably resolve, and the model
        // was fabricating a plausible-looking date rather than admitting it couldn't
        // read it (found 2026-09-16, "25/04/2017" on a card with no such date visible).
        if (obj.has("issue_date")) problems += "issue_date must not appear on an Aadhaar record"

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear)

        // The schema now accepts the number in its natural printed grouping ("1234 5678
        // 9012") as well as bare digits, because forcing bare-digits-only previously gave
        // the model no legal way to emit the number the way it naturally reads it - the
        // grammar rejected the spaced form and the value fell through into "address"
        // instead (found 2026-09-16). Normalize here rather than relying on the model to
        // strip spaces itself, the same reasoning as the date normalizer.
        val aadhaarRaw = str(obj, "aadhaar_number")
        if (aadhaarRaw != null) {
            val aadhaar = aadhaarRaw.replace(" ", "")
            if (!Regex("^[0-9]{12}$").matches(aadhaar)) {
                problems += "aadhaar_number '$aadhaarRaw' is not 12 digits"
            } else {
                notes += "aadhaar_number -> $aadhaar"
                if (!verhoeffValid(aadhaar)) {
                    problems += "aadhaar_number '$aadhaarRaw' fails the Aadhaar checksum " +
                        "(Verhoeff algorithm) - at least one digit is misread"
                }
            }
        }

        val pin = str(obj, "pincode")
        if (pin != null && !Regex("^[0-9]{6}$").matches(pin)) {
            problems += "pincode '$pin' is not 6 digits"
        }

        // Observed live, three times now on three different cards, with no address block
        // actually visible: rather than returning null as instructed, the model fabricates
        // an "address" by recombining content it already generated for OTHER fields on the
        // same record - or content belonging to a DIFFERENT field entirely. Card 1: address
        // was a verbatim duplicate of aadhaar_number. Card 2: address embedded name
        // ("Basant Raj") and dob ("01/01/2000") verbatim, plus invented filler text, with
        // pincode a plausible-looking but unconnected invented number. Card 3 (same
        // "Basant Raj" card, re-tested 2026-09-17): a DIFFERENT fabrication this time -
        // "Nawabzat R expanse / Enrolment No.: 4049/30507/00690" - no longer duplicating
        // this record's own fields, but instead referencing "Enrolment No.", which is a
        // real but DIFFERENT Aadhaar field (the enrollment ID, distinct from the Aadhaar
        // number) that has no business appearing inside an address. Confirmed by the user:
        // this card has no real address printed. Same underlying behaviour every time -
        // when this field has nothing real to point to, the model recombines whatever
        // concept is "nearby" rather than admitting it doesn't know. A prose "use null"
        // instruction has proven unreliable against this, so detect it instead: a genuine
        // address never legitimately contains the resident's own name/dob/aadhaar_number,
        // nor a reference to the enrollment-number field.
        val addr = str(obj, "address")
        val name = str(obj, "name")
        val dobRaw = str(obj, "dob")
        val aadhaarDigitsOnly = aadhaarRaw?.replace(" ", "")
        val fabricated = addr != null && (
            (aadhaarDigitsOnly != null && addr.replace(" ", "") == aadhaarDigitsOnly) ||
            (name != null && addr.contains(name, ignoreCase = true)) ||
            (dobRaw != null && addr.contains(dobRaw)) ||
            Regex("enrolment no|enrollment no|\\beid\\b", RegexOption.IGNORE_CASE).containsMatchIn(addr)
        )
        if (fabricated) {
            problems += "address reuses this record's own name/dob/aadhaar_number, or " +
                "references the enrollment-number field - almost certainly fabricated, " +
                "not a real address"
            // A PIN with no genuine address behind it has no legitimate source either.
            if (pin != null) problems += "pincode has no genuine address to derive from"
        } else if (pin != null && aadhaarDigitsOnly != null && aadhaarDigitsOnly.startsWith(pin)) {
            problems += "pincode '$pin' looks derived from aadhaar_number, not a real PIN"
        } else if (addr != null && pin != null) {
            // Found live 2026-09-17: address ended "...Andhra Pradesh - 516309" while the
            // separate pincode field said "759831" - two different numbers for what should
            // be the same PIN. A real address's own trailing PIN and the standalone
            // pincode field must agree; take the LAST 6-digit run in the address as its
            // embedded PIN, since that's where a genuine Indian address prints one.
            val addrPin = Regex("\\b[0-9]{6}\\b").findAll(addr).lastOrNull()?.value
            if (addrPin != null && addrPin != pin) {
                problems += "address ends in PIN '$addrPin' but the separate pincode field " +
                    "says '$pin' - these should be the same PIN and are not"
            }
        }

        // Qwen profile: the address_visible flag must agree with what was returned. The grammar
        // already forces these when it can; the app re-checks them so a scan that breaks the
        // contract is flagged instead of trusted. A genuine block-wide copy is also expected to
        // contain its own PIN, and an address with a PIN in it but a null pincode field is wrong.
        if (obj.has("address_visible")) {
            if (obj.optBoolean("address_visible", false)) {
                if (addr == null || addr.length < 15) {
                    problems += "address_visible is true but the address is missing or too short"
                } else {
                    val embeddedPin = Regex("\\b[0-9]{6}\\b").findAll(addr).lastOrNull()?.value
                    if (pin != null && !addr.contains(pin)) {
                        problems += "address does not contain the pincode '$pin'"
                    } else if (pin == null && embeddedPin != null) {
                        problems += "address contains the PIN '$embeddedPin' but the pincode field is null"
                    } else if (pin == null) {
                        notes += "address read, but no 6-digit PIN was found in it - check the address is complete"
                    }
                }
            } else if (addr != null || pin != null) {
                problems += "address_visible is false but an address or pincode was returned"
            }
        }

        return coverageReport(
            "Aadhaar", obj, fields, problems, notes,
            oftenNullFields = if (addressDeclaredAbsent) listOf("address", "pincode") else emptyList()
        )
    }
}
