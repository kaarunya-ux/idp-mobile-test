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

    // --- compact profile -----------------------------------------------------------

    private fun compactDl() = json(
        "document_type" to "dl",
        "license_number" to "MH12 20190012345",
        "name" to "ASHA RAO",
        "dob" to "01/01/1990",
        "issue_date" to "10/05/2019",
        "validity_nt" to "09/05/2039"
    )

    @Test
    fun `validateDl in compact mode grades the five compact fields HIGH`() {
        val report = DlRules.validate(compactDl(), year, compact = true)
        assertTrue(report, report.startsWith("DL check: HIGH - 5/5 fields read"))
        assertFalse(report.contains("often blank"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `validateDl without compact still expects the full field list`() {
        val report = DlRules.validate(compactDl(), year)
        assertTrue(report, report.startsWith("DL check: LOW - 5/8 fields read"))
    }

    @Test
    fun `validateDl in compact mode still flags a bad date`() {
        val report = DlRules.validate(compactDl().put("dob", "32/01/1990"), year, compact = true)
        assertTrue(report, report.contains("dob day out of range"))
        assertTrue(report, report.startsWith("DL check: LOW"))
    }

    // --- licence number label, issue year ------------------------------------------

    @Test
    fun `validateDl flags a licence number that starts with the DL No label`() {
        val labelled = listOf(
            "DL NO KL21 2019002",
            "DL NO MH03 200800 ",
            "dl no MH03 20080022135",
            "DL.NO KL21 2019002"
        )
        for (bad in labelled) {
            val report = DlRules.validate(validDl().put("license_number", bad), year)
            assertTrue(report, report.contains("starts with the label 'DL No'"))
            assertTrue(report, report.startsWith("DL check: LOW"))
        }
    }

    @Test
    fun `validateDl accepts real licence numbers including the Delhi format with a dash`() {
        for (good in listOf("DL-0420110149646", "KL21 20190023531", "MH02 20120037319", "OD15 19800170087")) {
            val report = DlRules.validate(validDl().put("license_number", good), year)
            assertFalse(report, report.contains("starts with the label"))
        }
    }

    @Test
    fun `validateDl accepts an issue date in 1980 and flags one before 1950`() {
        val ok = DlRules.validate(validDl().put("issue_date", "03-09-1980"), year)
        assertFalse(ok, ok.contains("issue_date year"))
        val bad = DlRules.validate(validDl().put("issue_date", "03-09-1949"), year)
        assertTrue(bad, bad.contains("issue_date year 1949 implausible"))
    }

    @Test
    fun `validateDl still flags a validity date before 1990`() {
        val report = DlRules.validate(validDl().put("validity_nt", "01-01-1985"), year)
        assertTrue(report, report.contains("validity_nt year 1985 implausible"))
    }
}
