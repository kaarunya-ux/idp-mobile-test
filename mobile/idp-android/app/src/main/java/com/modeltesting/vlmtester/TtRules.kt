package com.modeltesting.vlmtester

import com.modeltesting.vlmtester.ExtractionRules.checkDateShape
import com.modeltesting.vlmtester.ExtractionRules.coverageReport
import com.modeltesting.vlmtester.ExtractionRules.str

/**
 * Checks for the Trinidad and Tobago documents (pure; see ExtractionRules). Moved out of MainActivity
 * without changing behaviour; the calendar year is now passed in like the other rule files.
 * All three are completely unverified against a real sample.
 */
object TtRules {
    fun validatePassport(obj: org.json.JSONObject, currentYear: Int): String {
        // Trinidad & Tobago passport. Field list corrected 2026-09-16 to match what is
        // actually printed on the biodata page: type, country_code, passport_number,
        // surname, given_names (split, not one combined "name"), nationality, gender,
        // dob, place_of_birth, issue_date, issuing_authority, expiry_date. Still
        // completely unverified against a real sample - no image exists in this
        // workspace, and the exact passport_number format hasn't been confirmed, so no
        // structural regex is enforced on it (schema only bounds its length).
        val fields = listOf(
            "type", "country_code", "passport_number", "surname",
            "given_names", "nationality", "gender", "dob", "place_of_birth",
            "issue_date", "issuing_authority", "expiry_date"
        )
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "tt_passport") {
            problems += "document_type is not 'tt_passport'"
        }

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear)
        checkDateShape(problems, notes, obj, "issue_date", 1990, currentYear)
        checkDateShape(problems, notes, obj, "expiry_date", 1990, currentYear + 20)

        val surname = str(obj, "surname")
        val given = str(obj, "given_names")
        if (surname != null && given != null && surname.equals(given, ignoreCase = true)) {
            problems += "surname and given_names are identical"
        }

        val cc = str(obj, "country_code")
        if (cc != null && !Regex("^[A-Z]{3}$").matches(cc)) {
            problems += "country_code '$cc' is not a 3-letter code"
        }

        notes += "passport_number format unverified for this country - no structural check applied"

        return coverageReport("TT Passport", obj, fields, problems, notes)
    }

    fun validateDl(obj: org.json.JSONObject, currentYear: Int): String {
        // Trinidad & Tobago driving permit, added 2026-09-16. Field set given directly
        // by the user - permit_number, name, dob, gender ("SEX"), address, issue_date,
        // expiry_date, payment_date, transaction_code ("TR"), class ("CLASS"). name and
        // address are printed on the card with NO label at all, confirmed by the user.
        // Completely unverified against a real sample.
        val fields = listOf(
            "permit_number", "name", "dob", "gender", "address",
            "issue_date", "expiry_date", "payment_date", "transaction_code", "class"
        )
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "tt_dl") problems += "document_type is not 'tt_dl'"

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear)
        checkDateShape(problems, notes, obj, "issue_date", 1990, currentYear)
        checkDateShape(problems, notes, obj, "expiry_date", 1990, currentYear + 20)
        checkDateShape(problems, notes, obj, "payment_date", 1990, currentYear)

        checkIssueAndExpiryDiffer(problems, obj)
        checkAddressNotReused(problems, obj)

        notes += "permit_number format unverified for this document - no structural check applied"

        return coverageReport("TT Driving Permit", obj, fields, problems, notes)
    }

    // Same duplication artifact confirmed live on the Indian DL (issue_date and
    // expiry_date coming back byte-identical) - applied here proactively since the
    // date layout is the same shape.
    private fun checkIssueAndExpiryDiffer(problems: MutableList<String>, obj: org.json.JSONObject) {
        val issueRaw = str(obj, "issue_date")
        val expiryRaw = str(obj, "expiry_date")
        if (issueRaw != null && expiryRaw != null && issueRaw == expiryRaw) {
            problems += "issue_date and expiry_date are identical ('$issueRaw') - " +
                "likely one was duplicated rather than genuinely read"
        }
    }

    // Same address-fabrication pattern confirmed live on Aadhaar and applied
    // proactively to the Indian DL - a genuine address never legitimately contains
    // the holder's own name/dob/permit_number.
    private fun checkAddressNotReused(problems: MutableList<String>, obj: org.json.JSONObject) {
        val addr = str(obj, "address")
        val name = str(obj, "name")
        val dobRaw = str(obj, "dob")
        val permit = str(obj, "permit_number")
        val byName = name != null && addr != null && addr.contains(name, ignoreCase = true)
        val byDob = dobRaw != null && addr != null && addr.contains(dobRaw)
        val squashedAddr = addr?.replace(" ", "")?.replace("-", "")
        val squashedPermit = permit?.replace(" ", "")?.replace("-", "")
        val byPermit = squashedAddr != null && squashedPermit != null && squashedAddr.contains(squashedPermit)
        val fabricated = byName || byDob || byPermit
        if (fabricated) {
            problems += "address reuses this record's own name/dob/permit_number - " +
                "almost certainly fabricated, not a real address"
        }
    }

    fun validateId(front: org.json.JSONObject, back: org.json.JSONObject, currentYear: Int): String {
        // Trinidad & Tobago National Identification Card, added 2026-09-16. The ONLY
        // two-sided document type: front carries just name, back carries everything
        // else, both confirmed by the user. name is printed (unlabelled) on both
        // sides, so cross-checking the two reads catches a misread neither side alone
        // could. social_assistance_number, blood_group and national_insurance_number
        // are commonly blank on real cards (confirmed by the user) - kept out of the
        // grading ratio via oftenNullFields so a genuinely correct read isn't dragged
        // down just because those three are null. Completely unverified against a
        // real sample.
        if (str(front, "document_type") != "tt_id_front") {
            return "TT_ID check: FAILED - front image's document_type is not 'tt_id_front'"
        }
        if (str(back, "document_type") != "tt_id_back") {
            return "TT_ID check: FAILED - back image's document_type is not 'tt_id_back'"
        }

        val merged = mergeSides(front, back)
        val coreFields = listOf(
            "name", "dob", "issue_date", "expiry_date",
            "registration_number", "citizenship_status", "place_of_birth", "gender",
            "eye_colour", "height_cm", "skin_colour"
        )
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        checkDateShape(problems, notes, merged, "dob", 1900, currentYear)
        checkDateShape(problems, notes, merged, "issue_date", 1990, currentYear)
        checkDateShape(problems, notes, merged, "expiry_date", 1990, currentYear + 20)

        checkIssueAndExpiryDiffer(problems, merged)
        checkNamesMatch(problems, notes, front, back)
        noteFabricationRisk(notes, merged)

        return coverageReport("TT National ID", merged, coreFields, problems, notes, OFTEN_NULL_ID_FIELDS)
    }

    // social_assistance_number, blood_group and national_insurance_number are commonly blank on real cards.
    private val OFTEN_NULL_ID_FIELDS = listOf("social_assistance_number", "blood_group", "national_insurance_number")

    private fun mergeSides(front: org.json.JSONObject, back: org.json.JSONObject): org.json.JSONObject {
        val backFields = listOf(
            "dob", "issue_date", "expiry_date", "registration_number",
            "citizenship_status", "place_of_birth", "gender", "eye_colour", "height_cm",
            "social_assistance_number", "skin_colour", "blood_group",
            "national_insurance_number"
        )
        val merged = org.json.JSONObject()
        merged.put("document_type", "tt_id")
        merged.put("name", front.opt("name") ?: org.json.JSONObject.NULL)
        for (k in backFields) merged.put(k, back.opt(k) ?: org.json.JSONObject.NULL)
        return merged
    }

    // name is printed on both sides of the same physical card. Corrected
    // 2026-09-16: a real card showed "Lynette Herbert" on front vs "Herbert
    // Lynette" on back - same two words, given-name/surname order swapped between
    // the sides. So compare word SETS, not literal string equality - only a real
    // wording difference (not just order) is a genuine misread signal. Same
    // reasoning as PAN's surname-initial check, which also had to stop assuming
    // one fixed word order.
    private fun checkNamesMatch(
        problems: MutableList<String>,
        notes: MutableList<String>,
        front: org.json.JSONObject,
        back: org.json.JSONObject
    ) {
        val frontName = str(front, "name")
        val backName = str(back, "name")
        if (frontName == null || backName == null) return
        val frontWords = frontName.trim().split(Regex("\\s+")).map { it.lowercase() }.toSet()
        val backWords = backName.trim().split(Regex("\\s+")).map { it.lowercase() }.toSet()
        if (frontWords != backWords) {
            problems += "front name '$frontName' does not match back name '$backName'"
        } else if (frontName != backName) {
            notes += "front name '$frontName' and back name '$backName' use the same " +
                "words in a different order - likely a display convention, not a misread"
        }
    }

    // social_assistance_number, blood_group and national_insurance_number are
    // known-high-risk for fabrication when genuinely absent (confirmed live
    // 2026-09-16: blood_group came back "B+" on a card with no blood group
    // printed anywhere). There's no internal-consistency signal to catch this the
    // way the address checks elsewhere do, so it can only be flagged
    // for manual verification, not caught automatically.
    private fun noteFabricationRisk(notes: MutableList<String>, merged: org.json.JSONObject) {
        for (k in OFTEN_NULL_ID_FIELDS) {
            val v = str(merged, k)
            if (v != null) {
                notes += "note: $k = '$v' - this field is known to sometimes be " +
                    "fabricated when genuinely absent from the card; verify against " +
                    "the physical document"
            }
        }
    }
}
