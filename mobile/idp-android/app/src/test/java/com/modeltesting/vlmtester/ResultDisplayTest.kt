package com.modeltesting.vlmtester

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

class ResultDisplayTest {
    private val report =
        "Passport check: HIGH - 7/7 fields read\n  dob -> 2000-07-01\n  expiry_date -> 2014-10-27\n" +
            "  - expiry is before issue"

    @Test
    fun `shows labelled lines and the verdict without the derived date lines`() {
        // The JVM test copy of org.json does not keep key order (Android's does), so the order is not asserted.
        val answer = """{"passport_number": "X6337086", "place_of_birth": "JODHPUR"}"""
        val out = ResultDisplay.format(34, "passport", 30.14, answer, report)
        assertTrue(out, out.startsWith("Run 34 - passport - 30.1 s\n\n"))
        assertTrue(out, out.contains("Passport number: X6337086"))
        assertTrue(out, out.contains("Place of birth: JODHPUR"))
        assertTrue(out, out.endsWith("Passport check: HIGH - 7/7 fields read\n- expiry is before issue"))
        assertFalse(out, out.contains("->"))
    }

    @Test
    fun `shows a dash for null and empty values and joins address lines`() {
        val answer = """{"name": null, "address": "Line one\nLine two", "pincode": ""}"""
        val out = ResultDisplay.format(1, "aadhaar", 5.0, answer, "")
        assertTrue(out.contains("Name: -"))
        assertTrue(out.contains("Address: Line one, Line two"))
        assertTrue(out.contains("Pincode: -"))
    }

    @Test
    fun `keeps the raw text when the answer is not a JSON object`() {
        val out = ResultDisplay.format(2, "dl", 5.0, "no json here", "DL check: LOW")
        assertTrue(out.contains("no json here"))
        assertFalse(out.contains("{"))
    }
}
