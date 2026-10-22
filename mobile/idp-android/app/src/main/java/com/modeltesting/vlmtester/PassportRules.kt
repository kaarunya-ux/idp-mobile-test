package com.modeltesting.vlmtester

import com.modeltesting.vlmtester.ExtractionRules.checkDateShape
import com.modeltesting.vlmtester.ExtractionRules.coverageReport
import com.modeltesting.vlmtester.ExtractionRules.str

/** Checks on the personal-particulars page of an Indian passport (pure; see ExtractionRules). */
object PassportRules {
    fun validate(obj: org.json.JSONObject, currentYear: Int): String {
        // father_name dropped: modern Indian passports don't print it on this page
        // (removed from the printed booklet after a 2018 government notification), and
        // asking for a field that isn't there is exactly the pattern that made the model
        // fabricate a PAN gender and an Aadhaar issue date earlier this session.
        val fields = listOf(
            "name", "dob", "gender", "place_of_birth",
            "passport_number", "issue_date", "expiry_date", "issuing_authority", "nationality"
        )
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "passport") problems += "document_type is not 'passport'"

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear)
        checkDateShape(problems, notes, obj, "issue_date", 1990, currentYear)
        checkDateShape(problems, notes, obj, "expiry_date", 1990, currentYear + 20)

        val pp = str(obj, "passport_number")
        if (pp != null && !Regex("^[A-Z][0-9]{7}$").matches(pp)) {
            problems += "passport_number '$pp' is not 1 letter + 7 digits"
        }

        // Unverified against a real sample - no passport image exists in this workspace.
        // Structural checks here are best-effort; only the field set was confirmed.
        return coverageReport("Passport", obj, fields, problems, notes)
    }
}
