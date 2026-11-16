package com.modeltesting.vlmtester

import org.junit.Assert.assertFalse
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [TtRules] (Trinidad and Tobago checks, moved out of MainActivity). */
class TtRulesTest {
    private val year = 2026

    private fun passport() = json(
        "document_type" to "tt_passport", "type" to "P", "country_code" to "TTO", "passport_number" to "TA123456",
        "surname" to "HERBERT", "given_names" to "LYNETTE", "nationality" to "TRINIDAD AND TOBAGO", "gender" to "F",
        "dob" to "01/02/1990", "place_of_birth" to "PORT OF SPAIN", "issue_date" to "01/02/2020",
        "issuing_authority" to "PORT OF SPAIN", "expiry_date" to "01/02/2030"
    )

    private fun permit() = json(
        "document_type" to "tt_dl", "permit_number" to "12345678", "name" to "LYNETTE HERBERT", "dob" to "01/02/1990",
        "gender" to "F", "address" to "5 ELM ROAD", "issue_date" to "01/02/2020", "expiry_date" to "01/02/2030",
        "payment_date" to "01/02/2020", "transaction_code" to "TR1", "class" to "2"
    )

    private fun front() = json("document_type" to "tt_id_front", "name" to "Lynette Herbert")

    private fun back() = json(
        "document_type" to "tt_id_back", "name" to "Lynette Herbert", "dob" to "01/02/1990",
        "issue_date" to "01/02/2020", "expiry_date" to "01/02/2030", "registration_number" to "199002010001",
        "citizenship_status" to "CITIZEN", "place_of_birth" to "PORT OF SPAIN", "gender" to "F",
        "eye_colour" to "BROWN", "height_cm" to "165", "skin_colour" to "MEDIUM"
    )

    @Test
    fun `passport grades a clean record HIGH with a note about the number format`() {
        val report = TtRules.validatePassport(passport(), year)
        assertTrue(report, report.startsWith("TT Passport check: HIGH - 12/12 fields read"))
        assertTrue(report, report.contains("passport_number format unverified"))
    }

    @Test
    fun `passport flags the wrong type, identical names, a bad country code and an expiry too far ahead`() {
        val obj = passport().put("document_type", "passport").put("given_names", "herbert")
            .put("country_code", "tt").put("expiry_date", "01/02/2047")
        val report = TtRules.validatePassport(obj, year)
        assertTrue(report, report.contains("document_type is not 'tt_passport'"))
        assertTrue(report, report.contains("surname and given_names are identical"))
        assertTrue(report, report.contains("country_code 'tt' is not a 3-letter code"))
        assertTrue(report, report.contains("expiry_date year 2047 implausible"))
        val limit = TtRules.validatePassport(passport().put("expiry_date", "01/02/2046"), year)
        assertFalse(limit, limit.contains("implausible"))
    }

    @Test
    fun `permit grades a clean record HIGH`() {
        val report = TtRules.validateDl(permit(), year)
        assertTrue(report, report.startsWith("TT Driving Permit check: HIGH - 10/10 fields read"))
    }

    @Test
    fun `permit flags identical dates and an address that reuses the record's own values`() {
        val same = TtRules.validateDl(permit().put("expiry_date", "01/02/2020"), year)
        assertTrue(same, same.contains("issue_date and expiry_date are identical ('01/02/2020')"))
        for (address in listOf("lynette herbert street", "born 01/02/1990", "no 12-34 5678")) {
            val report = TtRules.validateDl(permit().put("address", address), year)
            assertTrue(address, report.contains("almost certainly fabricated"))
        }
        val wrongType = TtRules.validateDl(permit().put("document_type", "x"), year)
        assertTrue(wrongType, wrongType.contains("document_type is not 'tt_dl'"))
        val badDay = TtRules.validateDl(permit().put("payment_date", "31/02/2020"), year)
        assertTrue(badDay, badDay.contains("does not exist"))
    }

    @Test
    fun `id card grades a clean pair and lists the often-blank fields`() {
        val report = TtRules.validateId(front(), back(), year)
        assertTrue(report, report.startsWith("TT National ID check: HIGH - 11/11 fields read"))
        assertTrue(report, report.contains("often blank on this document, not graded"))
        assertFalse(report, report.contains("note: blood_group"))
    }

    @Test
    fun `id card rejects the wrong side types`() {
        val noFront = TtRules.validateId(back(), back(), year)
        assertTrue(noFront, noFront.contains("front image's document_type is not 'tt_id_front'"))
        val noBack = TtRules.validateId(front(), front(), year)
        assertTrue(noBack, noBack.contains("back image's document_type is not 'tt_id_back'"))
    }

    @Test
    fun `id card compares the names as word sets`() {
        val swapped = TtRules.validateId(front(), back().put("name", "Herbert Lynette"), year)
        assertTrue(swapped, swapped.contains("use the same words in a different order"))
        assertFalse(swapped, swapped.contains("does not match"))
        val different = TtRules.validateId(front(), back().put("name", "Herbert Linette"), year)
        val expected = "front name 'Lynette Herbert' does not match back name 'Herbert Linette'"
        assertTrue(different, different.contains(expected))
    }

    @Test
    fun `id card flags duplicated dates and notes an often-blank field that has a value`() {
        val report = TtRules.validateId(front(), back().put("expiry_date", "01/02/2020").put("blood_group", "B+"), year)
        assertTrue(report, report.contains("issue_date and expiry_date are identical"))
        assertTrue(report, report.contains("note: blood_group = 'B+'"))
    }
}
