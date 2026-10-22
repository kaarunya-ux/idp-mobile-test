package com.modeltesting.vlmtester

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [PanRules]. */
class PanRulesTest {
    private val year = 2026

    private fun validPan() = json(
        "document_type" to "pan",
        "name" to "RAMESH KUMAR",
        "parent_name" to "SURESH KUMAR",
        "parent_relation" to "father",
        "dob" to "15/08/1990",
        "pan_number" to "ABCPK1234F"
    )

    // --- validatePan ---------------------------------------------------------------

    @Test
    fun `validatePan grades a clean record HIGH`() {
        val report = PanRules.validate(validPan(), year)
        assertTrue(report, report.startsWith("PAN check: HIGH - 5/5 fields read"))
        assertFalse(report.contains("  - "))
        assertFalse(report.contains("note:"))
    }

    @Test
    fun `validatePan flags a PAN with an invalid entity letter`() {
        val obj = validPan().put("pan_number", "ABCDE1234F")
        val report = PanRules.validate(obj, year)
        assertTrue(report.startsWith("PAN check: LOW"))
        assertTrue(report.contains("pan_number 'ABCDE1234F' is not a valid PAN structure"))
    }

    @Test
    fun `validatePan flags identical name and parent name`() {
        val obj = validPan().put("parent_name", "ramesh kumar")
        assertTrue(PanRules.validate(obj, year).contains("name and parent_name are identical"))
    }

    @Test
    fun `validatePan flags a wrong document type and fields a PAN must not have`() {
        val obj = validPan().put("document_type", "aadhaar").put("gender", "M")
        val report = PanRules.validate(obj, year)
        assertTrue(report.contains("document_type is not 'pan'"))
        assertTrue(report.contains("gender must not appear on a PAN record"))
    }

    @Test
    fun `validatePan only notes, and does not penalise, a fifth character that matches no name initial`() {
        val obj = validPan().put("pan_number", "ABCPZ1234F")
        val report = PanRules.validate(obj, year)
        assertTrue(report.startsWith("PAN check: HIGH"))
        assertTrue(report.contains("pan_number char 5 'Z' matches none of name's word-initials (RK)"))
    }

    @Test
    fun `validatePan skips the initial check for non-individual PANs`() {
        val obj = validPan().put("pan_number", "ABCCZ1234F")
        assertFalse(PanRules.validate(obj, year).contains("note:"))
    }

    @Test
    fun `validatePan reports a missing PAN number as a null field`() {
        val report = PanRules.validate(validPan().put("pan_number", JSONObject.NULL), year)
        assertTrue(report.startsWith("PAN check: MEDIUM - 4/5 fields read, null: pan_number"))
    }

    @Test
    fun `validatePan uses the injected year as the upper bound for the date of birth`() {
        val obj = validPan().put("dob", "01/01/2030")
        assertTrue(PanRules.validate(obj, 2026).contains("dob year 2030 implausible"))
        assertFalse(PanRules.validate(obj, 2031).contains("implausible"))
    }

    // --- validateCrop --------------------------------------------------------------------

    @Test
    fun `validateCrop recognises a PAN number, a date and a name-shaped value`() {
        assertEquals("PAN crop check: OK (PAN number)", PanRules.validateCrop("pan_crop", "BXYPK5678Q"))
        assertEquals("PAN crop check: OK (date of birth)", PanRules.validateCrop("pan_crop", "15/08/1990"))
        assertTrue(
            PanRules.validateCrop("pan_crop", "RAMESH KUMAR").startsWith("PAN crop check: OK (name-shaped value")
        )
    }

    @Test
    fun `validateCrop trims the value before checking`() {
        assertEquals("PAN crop check: OK (PAN number)", PanRules.validateCrop("pan_crop", "  BXYPK5678Q\n"))
    }

    @Test
    fun `validateCrop fails a value that fits none of the shapes`() {
        assertEquals(
            "PAN crop check: FAILED - \"ramesh 123\" does not match PAN number, date, or name format",
            PanRules.validateCrop("pan_crop", "ramesh 123")
        )
    }

    @Test
    fun `validateCrop does not accept lowercase letters or digits inside a name`() {
        for (value in listOf("ramesh kumar", "RAMESH 2 KUMAR", "Ramesh")) {
            val report = PanRules.validateCrop("pan_crop", value)
            assertTrue(value, report.startsWith("PAN crop check: FAILED - \"$value\""))
        }
    }

    @Test
    fun `validateCrop reports an unknown crop document type`() {
        assertEquals("Unknown crop document type: dl_crop", PanRules.validateCrop("dl_crop", "anything"))
    }
}
