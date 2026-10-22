package com.modeltesting.vlmtester

import com.modeltesting.vlmtester.ExtractionRules.checkDateShape
import com.modeltesting.vlmtester.ExtractionRules.coverageReport
import com.modeltesting.vlmtester.ExtractionRules.str
import com.modeltesting.vlmtester.ExtractionRules.verhoeffValid

/** Checks on an Aadhaar card record (pure; see ExtractionRules). */
object AadhaarRules {
    fun validate(obj: org.json.JSONObject, currentYear: Int): String {
        // Qwen profile only: address_visible=false means the card genuinely prints no address, so
        // address/pincode are not misses - leave them out of the graded fields (shown for
        // information instead) so a front-only card can grade HIGH.
        val addressDeclaredAbsent = obj.has("address_visible") && !obj.optBoolean("address_visible", false)
        val fields = if (addressDeclaredAbsent) {
            listOf("name", "dob", "gender", "aadhaar_number")
        } else {
            listOf("name", "dob", "gender", "aadhaar_number", "address", "pincode")
        }
        val problems = mutableListOf<String>()
        val notes = mutableListOf<String>()

        if (str(obj, "document_type") != "aadhaar") problems += "document_type is not 'aadhaar'"
        // issue_date was dropped from the schema entirely: it's printed in a corner
        // stamp too small for this vision encoder to reliably resolve, and the model
        // was fabricating a plausible-looking date rather than admitting it couldn't
        // read it (found 2026-09-16, "25/04/2017" on a card with no such date visible).
        if (obj.has("issue_date")) problems += "issue_date must not appear on an Aadhaar record"

        checkDateShape(problems, notes, obj, "dob", 1900, currentYear)

        val aadhaarRaw = str(obj, "aadhaar_number")
        checkAadhaarNumber(aadhaarRaw, problems, notes)

        val pin = str(obj, "pincode")
        if (pin != null && !Regex("^[0-9]{6}$").matches(pin)) {
            problems += "pincode '$pin' is not 6 digits"
        }

        checkAddressConsistency(obj, aadhaarRaw, pin, problems)
        checkAddressVisibility(obj, pin, problems, notes)

        return coverageReport(
            "Aadhaar",
            obj,
            fields,
            problems,
            notes,
            oftenNullFields = if (addressDeclaredAbsent) listOf("address", "pincode") else emptyList()
        )
    }

    // The schema now accepts the number in its natural printed grouping ("1234 5678
    // 9012") as well as bare digits, because forcing bare-digits-only previously gave
    // the model no legal way to emit the number the way it naturally reads it - the
    // grammar rejected the spaced form and the value fell through into "address"
    // instead (found 2026-09-16). Normalize here rather than relying on the model to
    // strip spaces itself, the same reasoning as the date normalizer.
    private fun checkAadhaarNumber(aadhaarRaw: String?, problems: MutableList<String>, notes: MutableList<String>) {
        if (aadhaarRaw == null) return
        val aadhaar = aadhaarRaw.replace(" ", "")
        if (!Regex("^[0-9]{12}$").matches(aadhaar)) {
            problems += "aadhaar_number '$aadhaarRaw' is not 12 digits"
        } else {
            notes += "aadhaar_number -> $aadhaar"
            if (!verhoeffValid(aadhaar)) {
                problems += "aadhaar_number '$aadhaarRaw' fails the Aadhaar checksum " +
                    "(Verhoeff algorithm) - at least one digit is misread"
            }
        }
    }

    // Observed live, three times now on three different cards, with no address block
    // actually visible: rather than returning null as instructed, the model fabricates
    // an "address" by recombining content it already generated for OTHER fields on the
    // same record - or content belonging to a DIFFERENT field entirely. Card 1: address
    // was a verbatim duplicate of aadhaar_number. Card 2: address embedded name
    // ("Basant Raj") and dob ("01/01/2000") verbatim, plus invented filler text, with
    // pincode a plausible-looking but unconnected invented number. Card 3 (same
    // "Basant Raj" card, re-tested 2026-09-17): a DIFFERENT fabrication this time -
    // "Nawabzat R expanse / Enrolment No.: 4049/30507/00690" - no longer duplicating
    // this record's own fields, but instead referencing "Enrolment No.", which is a
    // real but DIFFERENT Aadhaar field (the enrollment ID, distinct from the Aadhaar
    // number) that has no business appearing inside an address. Confirmed by the user:
    // this card has no real address printed. Same underlying behaviour every time -
    // when this field has nothing real to point to, the model recombines whatever
    // concept is "nearby" rather than admitting it doesn't know. A prose "use null"
    // instruction has proven unreliable against this, so detect it instead: a genuine
    // address never legitimately contains the resident's own name/dob/aadhaar_number,
    // nor a reference to the enrollment-number field.
    private fun checkAddressConsistency(
        obj: org.json.JSONObject,
        aadhaarRaw: String?,
        pin: String?,
        problems: MutableList<String>
    ) {
        val addr = str(obj, "address")
        val aadhaarDigitsOnly = aadhaarRaw?.replace(" ", "")
        if (isFabricatedAddress(addr, str(obj, "name"), str(obj, "dob"), aadhaarDigitsOnly)) {
            problems += "address reuses this record's own name/dob/aadhaar_number, or " +
                "references the enrollment-number field - almost certainly fabricated, " +
                "not a real address"
            // A PIN with no genuine address behind it has no legitimate source either.
            if (pin != null) problems += "pincode has no genuine address to derive from"
        } else if (pin != null && aadhaarDigitsOnly != null && aadhaarDigitsOnly.startsWith(pin)) {
            problems += "pincode '$pin' looks derived from aadhaar_number, not a real PIN"
        } else if (addr != null && pin != null) {
            // Found live 2026-09-17: address ended "...Andhra Pradesh - 516309" while the
            // separate pincode field said "759831" - two different numbers for what should
            // be the same PIN. A real address's own trailing PIN and the standalone
            // pincode field must agree; take the LAST 6-digit run in the address as its
            // embedded PIN, since that's where a genuine Indian address prints one.
            val addrPin = Regex("\\b[0-9]{6}\\b").findAll(addr).lastOrNull()?.value
            if (addrPin != null && addrPin != pin) {
                problems += "address ends in PIN '$addrPin' but the separate pincode field " +
                    "says '$pin' - these should be the same PIN and are not"
            }
        }
    }

    private fun isFabricatedAddress(
        addr: String?,
        name: String?,
        dobRaw: String?,
        aadhaarDigitsOnly: String?
    ): Boolean = addr != null &&
        (
            (aadhaarDigitsOnly != null && addr.replace(" ", "") == aadhaarDigitsOnly) ||
                (name != null && addr.contains(name, ignoreCase = true)) ||
                (dobRaw != null && addr.contains(dobRaw)) ||
                Regex("enrolment no|enrollment no|\\beid\\b", RegexOption.IGNORE_CASE).containsMatchIn(addr)
            )

    // Qwen profile: the address_visible flag must agree with what was returned. The grammar
    // already forces these when it can; the app re-checks them so a scan that breaks the
    // contract is flagged instead of trusted. A genuine block-wide copy is also expected to
    // contain its own PIN, and an address with a PIN in it but a null pincode field is wrong.
    private fun checkAddressVisibility(
        obj: org.json.JSONObject,
        pin: String?,
        problems: MutableList<String>,
        notes: MutableList<String>
    ) {
        if (!obj.has("address_visible")) return
        val addr = str(obj, "address")
        if (obj.optBoolean("address_visible", false)) {
            checkVisibleAddress(addr, pin, problems, notes)
        } else if (addr != null || pin != null) {
            problems += "address_visible is false but an address or pincode was returned"
        }
    }

    private fun checkVisibleAddress(
        addr: String?,
        pin: String?,
        problems: MutableList<String>,
        notes: MutableList<String>
    ) {
        if (addr == null || addr.length < 15) {
            problems += "address_visible is true but the address is missing or too short"
            return
        }
        val embeddedPin = Regex("\\b[0-9]{6}\\b").findAll(addr).lastOrNull()?.value
        if (pin != null && !addr.contains(pin)) {
            problems += "address does not contain the pincode '$pin'"
        } else if (pin == null && embeddedPin != null) {
            problems += "address contains the PIN '$embeddedPin' but the pincode field is null"
        } else if (pin == null) {
            notes += "address read, but no 6-digit PIN was found in it - check the address is complete"
        }
    }
}
