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

    private fun json(vararg pairs: Pair<String, Any?>): JSONObject {
        val obj = JSONObject()
        for ((key, value) in pairs) obj.put(key, value ?: JSONObject.NULL)
        return obj
    }

    private fun validPan() = json(
        "document_type" to "pan", "name" to "RAMESH KUMAR", "parent_name" to "SURESH KUMAR",
        "parent_relation" to "father", "dob" to "15/08/1990", "pan_number" to "ABCPK1234F"
    )

    private fun validAadhaar() = json(
        "document_type" to "aadhaar", "name" to "ASHA RAO", "dob" to "01/01/1990",
        "gender" to "F", "aadhaar_number" to "2345 6789 0124",
        "address" to "12 MG Road, Pune, Maharashtra 411001", "pincode" to "411001"
    )

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

    // --- validatePan ---------------------------------------------------------------

    @Test
    fun `validatePan grades a clean record HIGH`() {
        val report = ExtractionRules.validatePan(validPan(), year)
        assertTrue(report, report.startsWith("PAN check: HIGH - 5/5 fields read"))
        assertFalse(report.contains("  - "))
        assertFalse(report.contains("note:"))
    }

    @Test
    fun `validatePan flags a PAN with an invalid entity letter`() {
        val obj = validPan().put("pan_number", "ABCDE1234F")
        val report = ExtractionRules.validatePan(obj, year)
        assertTrue(report.startsWith("PAN check: LOW"))
        assertTrue(report.contains("pan_number 'ABCDE1234F' is not a valid PAN structure"))
    }

    @Test
    fun `validatePan flags identical name and parent name`() {
        val obj = validPan().put("parent_name", "ramesh kumar")
        assertTrue(ExtractionRules.validatePan(obj, year).contains("name and parent_name are identical"))
    }

    @Test
    fun `validatePan flags a wrong document type and fields a PAN must not have`() {
        val obj = validPan().put("document_type", "aadhaar").put("gender", "M")
        val report = ExtractionRules.validatePan(obj, year)
        assertTrue(report.contains("document_type is not 'pan'"))
        assertTrue(report.contains("gender must not appear on a PAN record"))
    }

    @Test
    fun `validatePan only notes, and does not penalise, a fifth character that matches no name initial`() {
        val obj = validPan().put("pan_number", "ABCPZ1234F")
        val report = ExtractionRules.validatePan(obj, year)
        assertTrue(report.startsWith("PAN check: HIGH"))
        assertTrue(report.contains("pan_number char 5 'Z' matches none of name's word-initials (RK)"))
    }

    @Test
    fun `validatePan skips the initial check for non-individual PANs`() {
        val obj = validPan().put("pan_number", "ABCCZ1234F")
        assertFalse(ExtractionRules.validatePan(obj, year).contains("note:"))
    }

    @Test
    fun `validatePan reports a missing PAN number as a null field`() {
        val report = ExtractionRules.validatePan(validPan().put("pan_number", JSONObject.NULL), year)
        assertTrue(report.startsWith("PAN check: MEDIUM - 4/5 fields read, null: pan_number"))
    }

    @Test
    fun `validatePan uses the injected year as the upper bound for the date of birth`() {
        val obj = validPan().put("dob", "01/01/2030")
        assertTrue(ExtractionRules.validatePan(obj, 2026).contains("dob year 2030 implausible"))
        assertFalse(ExtractionRules.validatePan(obj, 2031).contains("implausible"))
    }

    // --- validateAadhaar -----------------------------------------------------------

    @Test
    fun `validateAadhaar grades a clean record HIGH and normalises the number`() {
        val report = ExtractionRules.validateAadhaar(validAadhaar(), year)
        assertTrue(report, report.startsWith("Aadhaar check: HIGH - 6/6 fields read"))
        assertTrue(report.contains("aadhaar_number -> 234567890124"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `validateAadhaar flags a number that fails the checksum`() {
        val obj = validAadhaar().put("aadhaar_number", "2345 6789 0125")
        val report = ExtractionRules.validateAadhaar(obj, year)
        assertTrue(report.startsWith("Aadhaar check: LOW"))
        assertTrue(report.contains("fails the Aadhaar checksum"))
    }

    @Test
    fun `validateAadhaar flags a number that is not 12 digits`() {
        val obj = validAadhaar().put("aadhaar_number", "1234 5678")
        assertTrue(ExtractionRules.validateAadhaar(obj, year).contains("'1234 5678' is not 12 digits"))
    }

    @Test
    fun `validateAadhaar flags a pincode that is not 6 digits`() {
        val obj = validAadhaar().put("pincode", "4110")
        assertTrue(ExtractionRules.validateAadhaar(obj, year).contains("pincode '4110' is not 6 digits"))
    }

    @Test
    fun `validateAadhaar flags a pincode taken from the Aadhaar number`() {
        val obj = validAadhaar().put("pincode", "234567").put("address", "12 MG Road, Pune")
        assertTrue(
            ExtractionRules.validateAadhaar(obj, year)
                .contains("pincode '234567' looks derived from aadhaar_number")
        )
    }

    @Test
    fun `validateAadhaar flags an address that reuses the resident's own name`() {
        val obj = validAadhaar().put("address", "ASHA RAO, 12 MG Road, Pune 411001")
        val report = ExtractionRules.validateAadhaar(obj, year)
        assertTrue(report.contains("address reuses this record's own name/dob/aadhaar_number"))
        assertTrue(report.contains("pincode has no genuine address to derive from"))
    }

    @Test
    fun `validateAadhaar flags an address that references the enrolment number`() {
        val obj = validAadhaar().put("address", "Enrolment No.: 4049/30507/00690")
        assertTrue(
            ExtractionRules.validateAadhaar(obj, year).contains("references the enrollment-number field")
        )
    }

    @Test
    fun `validateAadhaar flags an address PIN that disagrees with the pincode field`() {
        val obj = validAadhaar().put("address", "Kadapa, Andhra Pradesh - 516309").put("pincode", "759831")
        assertTrue(
            ExtractionRules.validateAadhaar(obj, year)
                .contains("address ends in PIN '516309' but the separate pincode field says '759831'")
        )
    }

    @Test
    fun `validateAadhaar flags an issue_date field that must not exist`() {
        val obj = validAadhaar().put("issue_date", "25/04/2017")
        assertTrue(
            ExtractionRules.validateAadhaar(obj, year).contains("issue_date must not appear on an Aadhaar record")
        )
    }

    @Test
    fun `validateAadhaar does not grade address or pincode when the card prints none`() {
        val obj = validAadhaar().put("address_visible", false).put("address", JSONObject.NULL)
            .put("pincode", JSONObject.NULL)
        val report = ExtractionRules.validateAadhaar(obj, year)
        assertTrue(report, report.startsWith("Aadhaar check: HIGH - 4/4 fields read"))
        assertTrue(report.contains("often blank on this document, not graded: address=null, pincode=null"))
    }

    @Test
    fun `validateAadhaar flags an address returned although none is visible`() {
        val obj = validAadhaar().put("address_visible", false)
        assertTrue(
            ExtractionRules.validateAadhaar(obj, year)
                .contains("address_visible is false but an address or pincode was returned")
        )
    }

    @Test
    fun `validateAadhaar flags a visible address that is missing or too short`() {
        val obj = validAadhaar().put("address_visible", true).put("address", "Pune").put("pincode", JSONObject.NULL)
        assertTrue(
            ExtractionRules.validateAadhaar(obj, year)
                .contains("address_visible is true but the address is missing or too short")
        )
    }

    @Test
    fun `validateAadhaar uses the injected year as the upper bound for the date of birth`() {
        val obj = validAadhaar().put("dob", "01/01/2030")
        assertTrue(ExtractionRules.validateAadhaar(obj, 2026).contains("dob year 2030 implausible"))
        assertFalse(ExtractionRules.validateAadhaar(obj, 2031).contains("implausible"))
    }
}
