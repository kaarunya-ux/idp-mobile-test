package com.modeltesting.vlmtester

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [PassportRules]. */
class PassportRulesTest {
    private val year = 2026

    private fun validPassport() = json(
        "document_type" to "passport",
        "passport_number" to "K1234567",
        "name" to "RAO ASHA",
        "dob" to "01/01/1990",
        "gender" to "F",
        "place_of_birth" to "PUNE",
        "issue_date" to "10/05/2019",
        "expiry_date" to "09/05/2029",
        "issuing_authority" to "PUNE",
        "nationality" to "INDIAN"
    )

    @Test
    fun `validatePassport grades a clean record HIGH`() {
        val report = PassportRules.validate(validPassport(), year)
        assertTrue(report, report.startsWith("Passport check: HIGH - 9/9 fields read"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `validatePassport flags a wrong document type`() {
        val report = PassportRules.validate(validPassport().put("document_type", "dl"), year)
        assertTrue(report.contains("document_type is not 'passport'"))
        assertTrue(report.startsWith("Passport check: LOW"))
    }

    @Test
    fun `validatePassport accepts one letter followed by seven digits and rejects other shapes`() {
        assertFalse(PassportRules.validate(validPassport(), year).contains("passport_number"))
        for (bad in listOf("12345678", "K123456", "KK123456", "k1234567", "K12345678")) {
            val report = PassportRules.validate(validPassport().put("passport_number", bad), year)
            assertTrue(bad, report.contains("passport_number '$bad' is not 1 letter + 7 digits"))
        }
    }

    @Test
    fun `validatePassport grades one missing field MEDIUM and lists it`() {
        val obj = validPassport().put("place_of_birth", org.json.JSONObject.NULL)
        val report = PassportRules.validate(obj, year)
        assertTrue(report, report.startsWith("Passport check: MEDIUM - 8/9 fields read, null: place_of_birth"))
    }

    @Test
    fun `validatePassport grades two missing fields LOW`() {
        val obj = validPassport().put("place_of_birth", org.json.JSONObject.NULL)
            .put("nationality", org.json.JSONObject.NULL)
        assertTrue(PassportRules.validate(obj, year).startsWith("Passport check: LOW - 7/9"))
    }

    @Test
    fun `validatePassport flags an unrecognised date format`() {
        val obj = validPassport().put("expiry_date", "2029.05.09")
        assertTrue(
            PassportRules.validate(obj, year).contains("expiry_date '2029.05.09' is not a recognised date format")
        )
    }

    @Test
    fun `validatePassport uses the injected year plus twenty as the upper bound for the expiry date`() {
        val obj = validPassport().put("expiry_date", "09/05/2050")
        assertTrue(PassportRules.validate(obj, 2026).contains("expiry_date year 2050 implausible"))
        assertFalse(PassportRules.validate(obj, 2031).contains("implausible"))
    }

    @Test
    fun `validatePassport uses the injected year as the upper bound for the date of birth and issue date`() {
        val obj = validPassport().put("dob", "01/01/2030").put("issue_date", "01/01/2030")
        val report = PassportRules.validate(obj, 2026)
        assertTrue(report.contains("dob year 2030 implausible"))
        assertTrue(report.contains("issue_date year 2030 implausible"))
    }

    @Test
    fun `validatePassport flags an expiry date that is not after the issue date`() {
        val obj = validPassport().put("issue_date", "23/06/2023").put("expiry_date", "27/10/2014")
        val report = PassportRules.validate(obj, year)
        assertTrue(report, report.contains("expiry_date 2014-10-27 is not after issue_date 2023-06-23"))
        assertFalse(PassportRules.validate(validPassport(), year).contains("is not after"))
    }

    // --- compact profile -----------------------------------------------------------

    private fun compactPassport() = json(
        "document_type" to "passport",
        "passport_number" to "K1234567",
        "name" to "ASHA RAO",
        "dob" to "01/01/1990",
        "gender" to "F",
        "place_of_birth" to "PUNE",
        "issue_date" to "10/05/2019",
        "expiry_date" to "09/05/2029"
    )

    @Test
    fun `validatePassport in compact mode grades the seven compact fields HIGH`() {
        val report = PassportRules.validate(compactPassport(), year, compact = true)
        assertTrue(report, report.startsWith("Passport check: HIGH - 7/7 fields read"))
        assertFalse(report.contains("  - "))
    }

    @Test
    fun `validatePassport without compact still expects the nine fields`() {
        val report = PassportRules.validate(compactPassport(), year)
        assertTrue(report, report.startsWith("Passport check: LOW - 7/9 fields read"))
    }

    @Test
    fun `validatePassport in compact mode still checks the passport number shape`() {
        val report = PassportRules.validate(compactPassport().put("passport_number", "12345678"), year, compact = true)
        assertTrue(report, report.contains("passport_number '12345678' is not 1 letter + 7 digits"))
    }
}
