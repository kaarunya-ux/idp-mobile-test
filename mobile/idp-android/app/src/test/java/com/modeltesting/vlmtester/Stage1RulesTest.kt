package com.modeltesting.vlmtester

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [Stage1Rules]. */
class Stage1RulesTest {
    private val year = 2026

    private fun validPan() = json(
        "pan_number" to "BXYPK5678Q",
        "name" to "RAMESH KUMAR",
        "father_name" to "SURESH KUMAR",
        "date_of_birth" to "15/08/1990",
        "signature_present" to true
    )

    private fun validAadhaar() = json(
        "aadhaar_number" to "2345 6789 0124",
        "vid" to "1234567890123456",
        "name" to "ASHA RAO",
        "date_of_birth" to "01/01/1990",
        "gender" to "F",
        "address" to "12 MG Road, Pune 411001"
    )

    // --- PAN -----------------------------------------------------------------------------

    @Test
    fun `stage1 PAN grades a clean record HIGH`() {
        val report = Stage1Rules.validatePan(validPan(), year)
        assertTrue(report, report.startsWith("PAN (stage1) check: HIGH - 5/5 fields read"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `stage1 PAN flags a key outside the schema`() {
        val report = Stage1Rules.validatePan(validPan().put("extra", "x"), year)
        assertTrue(report.startsWith("PAN (stage1) check: LOW"))
        assertTrue(report.contains("unexpected key(s) not in the stage1 schema: extra"))
    }

    @Test
    fun `stage1 PAN flags an invalid PAN structure`() {
        val report = Stage1Rules.validatePan(validPan().put("pan_number", "ABCDE1234F"), year)
        assertTrue(report.contains("pan_number 'ABCDE1234F' is not a valid PAN structure"))
    }

    @Test
    fun `stage1 PAN uses the injected year as the upper bound for the date of birth`() {
        val obj = validPan().put("date_of_birth", "01/01/2030")
        assertTrue(Stage1Rules.validatePan(obj, 2026).contains("date_of_birth year 2030 implausible"))
        assertFalse(Stage1Rules.validatePan(obj, 2031).contains("implausible"))
    }

    // --- Aadhaar -------------------------------------------------------------------------

    @Test
    fun `stage1 Aadhaar grades a clean record HIGH`() {
        val report = Stage1Rules.validateAadhaar(validAadhaar(), year)
        assertTrue(report, report.startsWith("Aadhaar (stage1) check: HIGH - 6/6 fields read"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `stage1 Aadhaar flags a key outside the schema`() {
        val report = Stage1Rules.validateAadhaar(validAadhaar().put("pincode", "411001"), year)
        assertTrue(report.contains("unexpected key(s) not in the stage1 schema: pincode"))
    }

    @Test
    fun `stage1 Aadhaar flags a number that fails the checksum or is not 12 digits`() {
        val badChecksum = Stage1Rules.validateAadhaar(validAadhaar().put("aadhaar_number", "2345 6789 0125"), year)
        assertTrue(badChecksum.contains("fails the Aadhaar checksum"))
        val tooShort = Stage1Rules.validateAadhaar(validAadhaar().put("aadhaar_number", "1234 5678"), year)
        assertTrue(tooShort.contains("'1234 5678' is not 12 digits"))
    }

    @Test
    fun `stage1 Aadhaar accepts a VID with spaces and rejects one that is not 16 digits`() {
        val spaced = Stage1Rules.validateAadhaar(validAadhaar().put("vid", "1234 5678 9012 3456"), year)
        assertFalse(spaced.contains("vid"))
        val short = Stage1Rules.validateAadhaar(validAadhaar().put("vid", "1234"), year)
        assertTrue(short.contains("vid '1234' is not 16 digits"))
    }
}
