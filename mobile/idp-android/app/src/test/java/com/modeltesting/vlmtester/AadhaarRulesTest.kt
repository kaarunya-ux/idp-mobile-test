package com.modeltesting.vlmtester

import org.json.JSONObject
import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/**
 * JVM unit tests for [AadhaarRules]. The numbers are not real: they were generated with an
 * independent Verhoeff implementation.
 */
class AadhaarRulesTest {
    private val year = 2026

    private fun validAadhaar() = json(
        "document_type" to "aadhaar",
        "name" to "ASHA RAO",
        "dob" to "01/01/1990",
        "gender" to "F",
        "aadhaar_number" to "2345 6789 0124",
        "address" to "12 MG Road, Pune, Maharashtra 411001",
        "pincode" to "411001"
    )

    // --- validateAadhaar -----------------------------------------------------------

    @Test
    fun `validateAadhaar grades a clean record HIGH and normalises the number`() {
        val report = AadhaarRules.validate(validAadhaar(), year)
        assertTrue(report, report.startsWith("Aadhaar check: HIGH - 6/6 fields read"))
        assertTrue(report.contains("aadhaar_number -> 234567890124"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `validateAadhaar flags a number that fails the checksum`() {
        val obj = validAadhaar().put("aadhaar_number", "2345 6789 0125")
        val report = AadhaarRules.validate(obj, year)
        assertTrue(report.startsWith("Aadhaar check: LOW"))
        assertTrue(report.contains("fails the Aadhaar checksum"))
    }

    @Test
    fun `validateAadhaar flags a number that is not 12 digits`() {
        val obj = validAadhaar().put("aadhaar_number", "1234 5678")
        assertTrue(AadhaarRules.validate(obj, year).contains("'1234 5678' is not 12 digits"))
    }

    @Test
    fun `validateAadhaar flags a pincode that is not 6 digits`() {
        val obj = validAadhaar().put("pincode", "4110")
        assertTrue(AadhaarRules.validate(obj, year).contains("pincode '4110' is not 6 digits"))
    }

    @Test
    fun `validateAadhaar flags a pincode taken from the Aadhaar number`() {
        val obj = validAadhaar().put("pincode", "234567").put("address", "12 MG Road, Pune")
        assertTrue(
            AadhaarRules.validate(obj, year)
                .contains("pincode '234567' looks derived from aadhaar_number")
        )
    }

    @Test
    fun `validateAadhaar flags an address that reuses the resident's own name`() {
        val obj = validAadhaar().put("address", "ASHA RAO, 12 MG Road, Pune 411001")
        val report = AadhaarRules.validate(obj, year)
        assertTrue(report.contains("address reuses this record's own name/dob/aadhaar_number"))
        assertTrue(report.contains("pincode has no genuine address to derive from"))
    }

    @Test
    fun `validateAadhaar flags an address that references the enrolment number`() {
        val obj = validAadhaar().put("address", "Enrolment No.: 4049/30507/00690")
        assertTrue(
            AadhaarRules.validate(obj, year).contains("references the enrollment-number field")
        )
    }

    @Test
    fun `validateAadhaar flags an address PIN that disagrees with the pincode field`() {
        val obj = validAadhaar().put("address", "Kadapa, Andhra Pradesh - 516309").put("pincode", "759831")
        assertTrue(
            AadhaarRules.validate(obj, year)
                .contains("address ends in PIN '516309' but the separate pincode field says '759831'")
        )
    }

    @Test
    fun `validateAadhaar flags an issue_date field that must not exist`() {
        val obj = validAadhaar().put("issue_date", "25/04/2017")
        assertTrue(
            AadhaarRules.validate(obj, year).contains("issue_date must not appear on an Aadhaar record")
        )
    }

    @Test
    fun `validateAadhaar does not grade address or pincode when the card prints none`() {
        val obj = validAadhaar().put("address_visible", false).put("address", JSONObject.NULL)
            .put("pincode", JSONObject.NULL)
        val report = AadhaarRules.validate(obj, year)
        assertTrue(report, report.startsWith("Aadhaar check: HIGH - 4/4 fields read"))
        assertTrue(report.contains("often blank on this document, not graded: address=null, pincode=null"))
    }

    @Test
    fun `validateAadhaar flags an address returned although none is visible`() {
        val obj = validAadhaar().put("address_visible", false)
        assertTrue(
            AadhaarRules.validate(obj, year)
                .contains("address_visible is false but an address or pincode was returned")
        )
    }

    @Test
    fun `validateAadhaar flags a visible address that is missing or too short`() {
        val obj = validAadhaar().put("address_visible", true).put("address", "Pune").put("pincode", JSONObject.NULL)
        assertTrue(
            AadhaarRules.validate(obj, year)
                .contains("address_visible is true but the address is missing or too short")
        )
    }

    @Test
    fun `validateAadhaar uses the injected year as the upper bound for the date of birth`() {
        val obj = validAadhaar().put("dob", "01/01/2030")
        assertTrue(AadhaarRules.validate(obj, 2026).contains("dob year 2030 implausible"))
        assertFalse(AadhaarRules.validate(obj, 2031).contains("implausible"))
    }

    @Test
    fun `validateAadhaar flags a visible address that does not contain the pincode`() {
        val obj = validAadhaar().put("address_visible", true)
            .put("address", "12 MG Road, Pune, Maharashtra 411001 India").put("pincode", "560001")
        assertTrue(
            AadhaarRules.validate(obj, year).contains("address does not contain the pincode '560001'")
        )
    }

    @Test
    fun `validateAadhaar flags a PIN inside the address when the pincode field is null`() {
        val obj = validAadhaar().put("address_visible", true)
            .put("address", "12 MG Road, Pune, Maharashtra 411001").put("pincode", JSONObject.NULL)
        assertTrue(
            AadhaarRules.validate(obj, year)
                .contains("address contains the PIN '411001' but the pincode field is null")
        )
    }

    @Test
    fun `validateAadhaar only notes a visible address with no PIN anywhere`() {
        val obj = validAadhaar().put("address_visible", true)
            .put("address", "12 MG Road, Opposite Town Hall, Pune").put("pincode", JSONObject.NULL)
        val report = AadhaarRules.validate(obj, year)
        assertTrue(report.contains("address read, but no 6-digit PIN was found in it"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `validateAadhaar flags an address that is the Aadhaar number itself`() {
        val obj = validAadhaar().put("address", "2345 6789 0124").put("pincode", JSONObject.NULL)
        assertTrue(AadhaarRules.validate(obj, year).contains("address reuses this record's own"))
    }

    @Test
    fun `validateAadhaar flags an address that contains the date of birth`() {
        val obj = validAadhaar().put("address", "Born 01/01/1990, Pune").put("pincode", JSONObject.NULL)
        assertTrue(AadhaarRules.validate(obj, year).contains("address reuses this record's own"))
    }

    @Test
    fun `validateAadhaar flags an address that mentions an EID`() {
        val obj = validAadhaar().put("address", "EID 1234/56789 Pune").put("pincode", JSONObject.NULL)
        assertTrue(AadhaarRules.validate(obj, year).contains("references the enrollment-number field"))
    }

    // --- compact profile -----------------------------------------------------------

    private fun compactAadhaar() = json(
        "document_type" to "aadhaar",
        "name" to "ASHA RAO",
        "dob" to "01/01/1990",
        "gender" to "F",
        "aadhaar_number" to "2345 6789 0124"
    )

    @Test
    fun `validateAadhaar in compact mode grades the four compact fields HIGH`() {
        val report = AadhaarRules.validate(compactAadhaar(), year, compact = true)
        assertTrue(report, report.startsWith("Aadhaar check: HIGH - 4/4 fields read"))
        assertTrue(report.contains("aadhaar_number -> 234567890124"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `validateAadhaar without compact still expects the address and pincode`() {
        val report = AadhaarRules.validate(compactAadhaar(), year)
        assertTrue(report, report.startsWith("Aadhaar check: LOW - 4/6 fields read"))
    }

    @Test
    fun `validateAadhaar in compact mode still checks the Verhoeff checksum`() {
        val bad = compactAadhaar().put("aadhaar_number", "2345 6789 0125")
        val report = AadhaarRules.validate(bad, year, compact = true)
        assertTrue(report, report.contains("fails the Aadhaar checksum"))
        assertTrue(report, report.startsWith("Aadhaar check: LOW"))
    }
}
