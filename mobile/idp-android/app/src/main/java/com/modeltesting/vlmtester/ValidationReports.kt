package com.modeltesting.vlmtester

/**
 * Picks the output check for a document type and returns its report text (pure). Moved out of
 * MainActivity without changing behaviour; the calendar year is a parameter so tests can fix it.
 */
object ValidationReports {
    // A hardcoded upper year bound goes stale the moment the calendar passes it - the
    // dob check's "2025" cap was already in the past by the time this was tested live
    // (found 2026-09-16), flagging every genuinely correct current-year extraction as
    // "implausible". Compute it from the device clock instead so it never goes stale.
    fun currentYear(): Int = java.time.Year.now().value

    fun forCrop(docType: String, text: String): String = PanRules.validateCrop(docType, text)

    fun forDocument(docType: String, text: String, compact: Boolean = false, year: Int = currentYear()): String {
        val (obj, err) = OutputParser.parseSide(docType.uppercase(), text)
        if (err != null) return err

        return checkFor(docType, obj!!, compact, year)
    }

    private fun checkFor(docType: String, obj: org.json.JSONObject, compact: Boolean, year: Int): String =
        when (docType) {
            "pan", "pan_full" -> PanRules.validate(obj, year)
            "aadhaar" -> AadhaarRules.validate(obj, year, compact)
            "passport" -> PassportRules.validate(obj, year, compact)
            "dl" -> DlRules.validate(obj, year, compact)
            "tt_passport" -> TtRules.validatePassport(obj, year)
            "tt_dl" -> TtRules.validateDl(obj, year)
            "pan_stage1" -> Stage1Rules.validatePan(obj, year)
            "aadhaar_stage1" -> Stage1Rules.validateAadhaar(obj, year)
            else -> "Unknown document type: $docType"
        }

    /** Same shape as [forDocument], but for a document whose data is split across two separate
     *  images (front + back) - currently only tt_id. Parses each side independently (own
     *  leak-check each), then hands both objects to a type-specific check that merges and
     *  cross-checks them. */
    fun forTwoSided(docType: String, frontText: String, backText: String, year: Int = currentYear()): String {
        val (frontObj, frontErr) = OutputParser.parseSide("${docType.uppercase()} FRONT", frontText)
        if (frontErr != null) return frontErr
        val (backObj, backErr) = OutputParser.parseSide("${docType.uppercase()} BACK", backText)
        if (backErr != null) return backErr

        return when (docType) {
            "tt_id" -> TtRules.validateId(frontObj!!, backObj!!, year)
            else -> "Unknown two-sided document type: $docType"
        }
    }
}
