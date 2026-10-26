package com.modeltesting.vlmtester

import org.junit.Assert.assertEquals
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [ValidationReports] (which check runs for which document type). */
class ValidationReportsTest {
    private val year = 2026
    private val dl =
        """{"document_type": "dl", "license_number": "KL21 20199000002", "name": "AKHIL A B", "dob": "17-06-1998", """ +
            """"issue_date": "30-12-2019", "validity_nt": "16-06-2038"}"""

    private fun assertRoute(docType: String, text: String, start: String, compact: Boolean = false) {
        val report = ValidationReports.forDocument(docType, text, compact, year)
        assertTrue("$docType: $report", report.startsWith(start))
    }

    @Test
    fun `routes each document type to its own check`() {
        assertRoute("dl", dl, "DL check: HIGH", compact = true)
        assertRoute("aadhaar", "{}", "Aadhaar check")
        assertRoute("passport", "{}", "Passport check")
        assertRoute("pan", "{}", "PAN check")
        assertRoute("pan_full", "{}", "PAN check")
        assertRoute("tt_passport", "{}", "TT Passport check")
        assertRoute("tt_dl", "{}", "TT Driving Permit check")
    }

    @Test
    fun `reports an unknown type and unreadable output`() {
        assertEquals("Unknown document type: x", ValidationReports.forDocument("x", "{}", year = year))
        val report = ValidationReports.forDocument("dl", "no json", year = year)
        assertTrue(report.contains("FAILED - no JSON object found"))
    }

    @Test
    fun `two-sided check reports an unreadable side and an unknown type`() {
        assertTrue(ValidationReports.forTwoSided("tt_id", "bad", "{}", year).contains("TT_ID FRONT check: FAILED"))
        assertTrue(ValidationReports.forTwoSided("tt_id", "{}", "bad", year).contains("TT_ID BACK check: FAILED"))
        assertEquals("Unknown two-sided document type: x", ValidationReports.forTwoSided("x", "{}", "{}", year))
        assertTrue(ValidationReports.forTwoSided("tt_id", "{}", "{}", year).startsWith("TT_ID check: FAILED"))
    }

    @Test
    fun `crop check and the calendar year come from the right place`() {
        assertEquals(PanRules.validateCrop("pan_crop", "{}"), ValidationReports.forCrop("pan_crop", "{}"))
        assertEquals(java.time.Year.now().value, ValidationReports.currentYear())
    }
}
