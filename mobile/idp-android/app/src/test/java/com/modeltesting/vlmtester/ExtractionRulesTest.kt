package com.modeltesting.vlmtester

import org.json.JSONObject
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for the deterministic checks in [ExtractionRules]. No device needed.
 *
 * The Aadhaar-shaped numbers below are not real: they were generated with an independent
 * Verhoeff implementation (and checked against the published vector 236 -> check digit 3).
 */
class ExtractionRulesTest {

    private val year = 2026

    // --- str -----------------------------------------------------------------------

    @Test
    fun `str returns null when the key is absent or JSON null`() {
        val obj = json("present" to "x", "empty" to null)
        assertEquals("x", ExtractionRules.str(obj, "present"))
        assertNull(ExtractionRules.str(obj, "empty"))
        assertNull(ExtractionRules.str(obj, "missing"))
    }

    // --- toIso ---------------------------------------------------------------------

    @Test
    fun `toIso converts the day-first and ISO shapes`() {
        assertEquals("1990-08-15", ExtractionRules.toIso("15/08/1990"))
        assertEquals("1990-08-15", ExtractionRules.toIso("15-08-1990"))
        assertEquals("1990-08-15", ExtractionRules.toIso("1990-08-15"))
    }

    @Test
    fun `toIso converts three-letter month names in either case`() {
        assertEquals("1990-08-15", ExtractionRules.toIso("15-Aug-1990"))
        assertEquals("1990-08-15", ExtractionRules.toIso("15-aug-1990"))
        assertEquals("1990-12-01", ExtractionRules.toIso("01-DEC-1990"))
    }

    @Test
    fun `toIso returns null when the shape is not recognised`() {
        assertNull(ExtractionRules.toIso(null))
        assertNull(ExtractionRules.toIso("15-Xyz-1990"))
        assertNull(ExtractionRules.toIso("15.08.1990"))
        assertNull(ExtractionRules.toIso("5/8/1990"))
        assertNull(ExtractionRules.toIso(""))
    }

    // --- verhoeffValid -------------------------------------------------------------

    @Test
    fun `verhoeff accepts the published test vector`() {
        assertTrue(ExtractionRules.verhoeffValid("2363"))
        assertFalse(ExtractionRules.verhoeffValid("2364"))
    }

    @Test
    fun `verhoeff accepts numbers with a correct check digit`() {
        for (number in listOf("234567890124", "499374610552", "278461930529")) {
            assertTrue(number, ExtractionRules.verhoeffValid(number))
        }
    }

    @Test
    fun `verhoeff rejects a single misread digit`() {
        assertFalse(ExtractionRules.verhoeffValid("234567890125"))
        assertFalse(ExtractionRules.verhoeffValid("234567890134"))
    }

    @Test
    fun `verhoeff rejects an adjacent transposition`() {
        assertFalse(ExtractionRules.verhoeffValid("234567890142"))
        assertFalse(ExtractionRules.verhoeffValid("324567890124"))
    }

    // --- checkDateShape ------------------------------------------------------------

    private fun checkDate(raw: String?, min: Int = 1900, max: Int = year): Pair<List<String>, List<String>> {
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()
        ExtractionRules.checkDateShape(problems, notes, json("dob" to raw), "dob", min, max)
        return problems to notes
    }

    @Test
    fun `checkDateShape notes the ISO date when it is plausible`() {
        val (problems, notes) = checkDate("15/08/1990")
        assertTrue(problems.isEmpty())
        assertEquals(listOf("dob -> 1990-08-15"), notes)
    }

    @Test
    fun `checkDateShape does nothing when the field is null`() {
        val (problems, notes) = checkDate(null)
        assertTrue(problems.isEmpty())
        assertTrue(notes.isEmpty())
    }

    @Test
    fun `checkDateShape flags an unrecognised format`() {
        val (problems, _) = checkDate("1990/08/15")
        assertEquals(listOf("dob '1990/08/15' is not a recognised date format"), problems)
    }

    @Test
    fun `checkDateShape flags a day or month out of range`() {
        assertTrue(checkDate("32/08/1990").first.contains("dob day out of range"))
        assertTrue(checkDate("15/13/1990").first.contains("dob month out of range"))
    }

    @Test
    fun `checkDateShape flags a year outside the allowed range`() {
        assertTrue(checkDate("15/08/1850").first.contains("dob year 1850 implausible"))
        assertTrue(checkDate("15/08/2031").first.contains("dob year 2031 implausible"))
        assertTrue(checkDate("15/08/2031", max = 2031).first.isEmpty())
    }

    // --- coverageReport ------------------------------------------------------------

    private fun coverage(obj: JSONObject, problems: List<String> = emptyList()) =
        ExtractionRules.coverageReport("X", obj, listOf("a", "b", "c"), problems, emptyList())

    @Test
    fun `coverageReport grades HIGH when every field was read`() {
        assertEquals("X check: HIGH - 3/3 fields read", coverage(json("a" to "1", "b" to "2", "c" to "3")))
    }

    @Test
    fun `coverageReport grades MEDIUM with one missing field and lists it`() {
        val report = coverage(json("a" to "1", "b" to "2", "c" to null))
        assertEquals("X check: MEDIUM - 2/3 fields read, null: c", report)
    }

    @Test
    fun `coverageReport grades LOW with two missing fields`() {
        assertTrue(coverage(json("a" to "1")).startsWith("X check: LOW - 1/3"))
    }

    @Test
    fun `coverageReport grades LOW when there is any problem even if all fields were read`() {
        val report = coverage(json("a" to "1", "b" to "2", "c" to "3"), problems = listOf("bad"))
        assertTrue(report.startsWith("X check: LOW - 3/3"))
        assertTrue(report.contains("  - bad"))
    }

    // --- withDocumentType ----------------------------------------------------------------

    @Test
    fun `withDocumentType adds the document type and keeps the other fields`() {
        val result = JSONObject(ExtractionRules.withDocumentType("""{"name":"ASHA RAO","dob":"01/01/1990"}""", "pan"))
        assertEquals("pan", result.getString("document_type"))
        assertEquals("ASHA RAO", result.getString("name"))
        assertEquals("01/01/1990", result.getString("dob"))
    }

    @Test
    fun `withDocumentType keeps a document type the model already gave`() {
        val result = JSONObject(ExtractionRules.withDocumentType("""{"document_type":"aadhaar"}""", "pan"))
        assertEquals("aadhaar", result.getString("document_type"))
    }

    @Test
    fun `withDocumentType returns the text unchanged when there is no JSON object`() {
        assertEquals("no json here", ExtractionRules.withDocumentType("no json here", "pan"))
        assertEquals("} backwards {", ExtractionRules.withDocumentType("} backwards {", "pan"))
        assertEquals("{not json}", ExtractionRules.withDocumentType("{not json}", "pan"))
    }

    @Test
    fun `withDocumentType reads the object out of surrounding text`() {
        val result = JSONObject(ExtractionRules.withDocumentType("""Answer: {"name":"A"} done""", "pan"))
        assertEquals("pan", result.getString("document_type"))
        assertEquals("A", result.getString("name"))
    }
}
