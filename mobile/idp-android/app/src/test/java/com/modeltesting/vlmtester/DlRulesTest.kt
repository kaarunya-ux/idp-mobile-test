package com.modeltesting.vlmtester

import org.json.JSONObject
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [DlRules]. */
class DlRulesTest {
    private val year = 2026

    // --- validateDl ----------------------------------------------------------------

    private fun validDl() = json(
        "document_type" to "dl",
        "license_number" to "MH12 20190012345",
        "name" to "ASHA RAO",
        "dob" to "01/01/1990",
        "address" to "12 MG Road, Pune",
        "issue_date" to "10/05/2019",
        "validity_nt" to "09/05/2039",
        "validity_tr" to null,
        "blood_group" to null,
        "organ_donor" to "N",
        "vehicle_classes" to "LMV MCWG"
    )

    @Test
    fun `validateDl grades a clean record HIGH and does not grade the often-blank fields`() {
        val report = DlRules.validate(validDl(), year)
        assertTrue(report, report.startsWith("DL check: HIGH - 8/8 fields read"))
        assertTrue(report.contains("often blank on this document, not graded: validity_tr=null, blood_group=null"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `validateDl grades a missing licence number MEDIUM`() {
        val report = DlRules.validate(validDl().put("license_number", JSONObject.NULL), year)
        assertTrue(report, report.startsWith("DL check: MEDIUM - 7/8 fields read, null: license_number"))
    }

    @Test
    fun `validateDl flags a wrong document type`() {
        val report = DlRules.validate(validDl().put("document_type", "pan"), year)
        assertTrue(report.contains("document_type is not 'dl'"))
    }

    @Test
    fun `validateDl flags an issue date copied into validity_nt`() {
        val obj = validDl().put("validity_nt", "10/05/2019")
        val report = DlRules.validate(obj, year)
        assertTrue(report.startsWith("DL check: LOW"))
        assertTrue(report.contains("issue_date and validity_nt are identical ('10/05/2019')"))
    }

    @Test
    fun `validateDl only notes identical NT and TR validity dates`() {
        val obj = validDl().put("validity_tr", "09/05/2039")
        val report = DlRules.validate(obj, year)
        assertTrue(report, report.startsWith("DL check: HIGH"))
        assertTrue(report.contains("note: validity_nt and validity_tr are identical ('09/05/2039')"))
    }

    @Test
    fun `validateDl notes a blood group for manual verification`() {
        val report = DlRules.validate(validDl().put("blood_group", "O+"), year)
        assertTrue(report.startsWith("DL check: HIGH"))
        assertTrue(report.contains("note: blood_group = 'O+' - this kind of field is known to sometimes be fabricated"))
    }

    @Test
    fun `validateDl flags an address that reuses the holder's name`() {
        val obj = validDl().put("address", "ASHA RAO, 12 MG Road, Pune")
        assertTrue(
            DlRules.validate(obj, year).contains("address reuses this record's own name/dob/license_number")
        )
    }

    @Test
    fun `validateDl flags an address that reuses the date of birth`() {
        val obj = validDl().put("address", "Pune 01/01/1990")
        assertTrue(DlRules.validate(obj, year).contains("address reuses this record's own"))
    }

    @Test
    fun `validateDl flags an address that reuses the licence number ignoring spaces and hyphens`() {
        val obj = validDl().put("address", "Pune MH-12-20190012345")
        assertTrue(DlRules.validate(obj, year).contains("address reuses this record's own"))
    }

    @Test
    fun `validateDl flags an address that reuses a hyphenated licence number written without hyphens`() {
        val obj = validDl().put("license_number", "MH-12 2019-0012345").put("address", "Pune MH1220190012345")
        assertTrue(DlRules.validate(obj, year).contains("address reuses this record's own"))
    }

    @Test
    fun `validateDl uses the injected year plus twenty as the upper bound for validity dates`() {
        val obj = validDl().put("validity_nt", "09/05/2050")
        assertTrue(DlRules.validate(obj, 2026).contains("validity_nt year 2050 implausible"))
        assertFalse(DlRules.validate(obj, 2031).contains("implausible"))
    }

    @Test
    fun `validateDl does not compare dates that are missing`() {
        for (key in listOf("issue_date", "validity_nt")) {
            val report = DlRules.validate(validDl().put(key, JSONObject.NULL), year)
            assertFalse(key, report.contains("are identical"))
        }
        val bothMissing = validDl().put("issue_date", JSONObject.NULL).put("validity_nt", JSONObject.NULL)
        assertFalse(DlRules.validate(bothMissing, year).contains("are identical"))
    }

    @Test
    fun `validateDl does not compare NT and TR when TR is missing or different`() {
        assertFalse(DlRules.validate(validDl(), year).contains("validity_nt and validity_tr are identical"))
        val different = validDl().put("validity_tr", "09/05/2040")
        assertFalse(DlRules.validate(different, year).contains("validity_nt and validity_tr are identical"))
    }
}
