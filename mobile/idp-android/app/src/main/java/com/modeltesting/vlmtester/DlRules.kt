package com.modeltesting.vlmtester

import com.modeltesting.vlmtester.ExtractionRules.checkDateShape
import com.modeltesting.vlmtester.ExtractionRules.coverageReport
import com.modeltesting.vlmtester.ExtractionRules.str

/** Checks on an Indian driving licence record (pure; see ExtractionRules). */
object DlRules {
    /** [compact] grades only the five fields of the compact profile (see ModelProfiles.useCompactDocument). */
    fun validate(obj: org.json.JSONObject, currentYear: Int, compact: Boolean = false): String {
        // Field list corrected 2026-09-17 to match a real Indian DL: license_number,
        // name, dob, address (its PIN code is embedded in this text, not a separate
        // field), issue_date, validity_nt/validity_tr (split from a single expiry_date
        // - Indian DLs print separate validity dates for Non-Transport and Transport
        // vehicle categories; validity_tr is commonly blank), blood_group (commonly
        // blank), organ_donor (Y/N). gender, a standalone pincode field, and
        // issuing_authority were removed entirely - confirmed not present on this card.
        val fields = if (compact) {
            listOf("license_number", "name", "dob", "issue_date", "validity_nt")
        } else {
            listOf(
                "license_number",
                "name",
                "dob",
                "address",
                "issue_date",
                "validity_nt",
                "organ_donor",
                "vehicle_classes"
            )
        }
        val oftenNullFields = if (compact) emptyList() else listOf("validity_tr", "blood_group")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "dl") problems += "document_type is not 'dl'"

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear)
        // Issue dates go back well before 1990 (an Odisha licence issued 03-09-1980 is still valid),
        // so the lower bound is 1950; validity dates keep 1990.
        checkDateShape(problems, notes, obj, "issue_date", 1950, currentYear)
        checkDateShape(problems, notes, obj, "validity_nt", 1990, currentYear + 20)
        checkDateShape(problems, notes, obj, "validity_tr", 1990, currentYear + 20)

        checkDlValidityDates(obj, problems, notes)
        checkDlAddress(obj, problems)
        checkLicenseLabel(obj, problems)

        // validity_tr and blood_group are commonly blank on real cards - excluded from
        // the grading ratio via oftenNullFields. Both are also known-fabrication-risk
        // fields (same pattern confirmed live on tt_id's blood_group), so flag them for
        // manual verification whenever non-null rather than trusting them silently.
        for (k in oftenNullFields) {
            val v = str(obj, k)
            if (v != null) {
                notes += "note: $k = '$v' - this kind of field is known to sometimes be " +
                    "fabricated when genuinely absent; verify against the physical document"
            }
        }

        return coverageReport("DL", obj, fields, problems, notes, oftenNullFields)
    }

    private fun checkDlValidityDates(
        obj: org.json.JSONObject,
        problems: MutableList<String>,
        notes: MutableList<String>
    ) {
        // A DL's validity period is normally years long - issue_date and validity_nt
        // being byte-identical is a strong tell that the model duplicated one into the
        // other rather than genuinely reading a separate field (this exact pattern was
        // found live 2026-09-16, back when this field was still called expiry_date).
        val issueRaw = str(obj, "issue_date")
        val validityNtRaw = str(obj, "validity_nt")
        val validityTrRaw = str(obj, "validity_tr")
        if (issueRaw != null && validityNtRaw != null && issueRaw == validityNtRaw) {
            problems += "issue_date and validity_nt are identical ('$issueRaw') - " +
                "likely one was duplicated rather than genuinely read"
        }
        // Less clear-cut than issue==validity_nt: NT and TR could legitimately share a
        // date if both endorsements were renewed together, so this is only a note.
        if (validityNtRaw != null && validityTrRaw != null && validityNtRaw == validityTrRaw) {
            notes += "note: validity_nt and validity_tr are identical ('$validityNtRaw') - " +
                "possible, but worth a manual glance since these cover different vehicle categories"
        }
    }

    // Found live 2026-10-23: the model returned "DL NO KL21 2019002" and "DL NO MH03 200800 " - the printed
    // label copied into the value, and the number cut short. A real number never starts with the
    // words DL NO (Delhi numbers start "DL-04..." with a dash and a digit, which this does not match).
    private fun checkLicenseLabel(obj: org.json.JSONObject, problems: MutableList<String>) {
        val number = str(obj, "license_number") ?: return
        if (Regex("^(DL|LICEN[CS]E)\\.?\\s*NO\\b", RegexOption.IGNORE_CASE).containsMatchIn(number)) {
            problems += "license_number '$number' starts with the label 'DL No', not the number - " +
                "the label was copied into the value and the number may be cut short"
        }
    }

    // Same fabrication pattern confirmed live on Aadhaar - a genuine address never
    // legitimately contains the record's own name/dob/license_number.
    private fun checkDlAddress(obj: org.json.JSONObject, problems: MutableList<String>) {
        val addr = str(obj, "address")
        if (isFabricatedDlAddress(addr, str(obj, "name"), str(obj, "dob"), str(obj, "license_number"))) {
            problems += "address reuses this record's own name/dob/license_number - " +
                "almost certainly fabricated, not a real address"
        }
    }

    private fun isFabricatedDlAddress(addr: String?, name: String?, dobRaw: String?, license: String?): Boolean =
        addr != null &&
            (
                (name != null && addr.contains(name, ignoreCase = true)) ||
                    (dobRaw != null && addr.contains(dobRaw)) ||
                    (
                        license != null &&
                            addr.replace(" ", "").replace("-", "")
                                .contains(license.replace(" ", "").replace("-", ""))
                        )
                )
}
