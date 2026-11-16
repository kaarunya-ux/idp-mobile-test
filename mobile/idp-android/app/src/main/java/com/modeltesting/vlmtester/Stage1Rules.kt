package com.modeltesting.vlmtester

import com.modeltesting.vlmtester.ExtractionRules.checkDateShape
import com.modeltesting.vlmtester.ExtractionRules.coverageReport
import com.modeltesting.vlmtester.ExtractionRules.str
import com.modeltesting.vlmtester.ExtractionRules.verhoeffValid

/** Lighter checks for the stage1 fine-tune evaluation on PAN and Aadhaar (pure; see ExtractionRules). */
object Stage1Rules {
    // --- Stage1 fine-tune eval (PAN/Aadhaar only, see MODEL_HANDOVER_stage1.md) -------
    // Deliberately a much lighter check than validatePan/validateAadhaar above: this
    // model's own schema (section 4 of the handover) has a different field list
    // entirely - no document_type, no parent_relation, no pincode, plus new fields
    // (signature_present, vid) - and grammar-constrained decoding is off for these two
    // doc types specifically, so unlike the stock checks, "did the model even follow
    // the schema" is itself part of what's being measured, not assumed. The three
    // things the handover doc asks us to log (parse failures, null-but-visible fields,
    // wrong-value fields) mostly can't be judged by code at all - null-but-visible and
    // wrong-value both require comparing against the physical document by eye. What
    // this can do automatically: report which schema keys came back null, flag any
    // key the model invented outside the schema (a direct format-discipline signal the
    // handover explicitly cares about), and keep the two objective structural checks
    // (PAN format, Aadhaar checksum) that don't depend on our app's own field names.

    private fun schemaCheck(obj: org.json.JSONObject, expected: List<String>): String? {
        val extra = obj.keys().asSequence().filterNot { it in expected }.toList()
        return if (extra.isEmpty()) {
            null
        } else {
            "unexpected key(s) not in the stage1 schema: ${extra.joinToString(", ")}"
        }
    }

    fun validatePan(obj: org.json.JSONObject, currentYear: Int): String {
        val fields = listOf("pan_number", "name", "father_name", "date_of_birth", "signature_present")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        schemaCheck(obj, fields)?.let { problems += it }
        checkDateShape(problems, notes, obj, "date_of_birth", 1900, currentYear)

        val pan = str(obj, "pan_number")
        if (pan != null && !Regex("^[A-Z]{3}[ABCFGHJLPT][A-Z][0-9]{4}[A-Z]$").matches(pan)) {
            problems += "pan_number '$pan' is not a valid PAN structure"
        }

        return coverageReport("PAN (stage1)", obj, fields, problems, notes)
    }

    fun validateAadhaar(obj: org.json.JSONObject, currentYear: Int): String {
        val fields = listOf("aadhaar_number", "vid", "name", "date_of_birth", "gender", "address")
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        schemaCheck(obj, fields)?.let { problems += it }
        checkDateShape(problems, notes, obj, "date_of_birth", 1900, currentYear)

        val aadhaarRaw = str(obj, "aadhaar_number")
        if (aadhaarRaw != null) {
            val aadhaar = aadhaarRaw.replace(" ", "")
            if (!Regex("^[0-9]{12}$").matches(aadhaar)) {
                problems += "aadhaar_number '$aadhaarRaw' is not 12 digits"
            } else if (!verhoeffValid(aadhaar)) {
                problems += "aadhaar_number '$aadhaarRaw' fails the Aadhaar checksum " +
                    "(Verhoeff algorithm) - at least one digit is misread"
            }
        }

        // VID (Virtual ID) is always 16 digits per UIDAI spec when present - unlike the
        // rest of this schema, this is an objective format fact, not a borrowed
        // assumption from a different document type.
        val vid = str(obj, "vid")
        if (vid != null && !Regex("^[0-9]{16}$").matches(vid.replace(" ", ""))) {
            problems += "vid '$vid' is not 16 digits"
        }

        return coverageReport("Aadhaar (stage1)", obj, fields, problems, notes)
    }
}
