package com.modeltesting.vlmtester

import com.modeltesting.vlmtester.ExtractionRules.str

/**
 * Reads the model's raw text into a JSON object and rejects output that was copied from our own
 * prompts rather than read from the document. Pure, so it runs in plain JVM unit tests. The guard
 * lists cover every document type.
 *
 * Moved out of MainActivity without changing behaviour; the only change is that a parse failure
 * is caught as JSONException (all that org.json can throw here) instead of any Exception.
 */
object OutputParser {
    // Values that appeared in earlier prompts and were echoed back as if read from the
    // card. Any of these in an output means the model copied the prompt, not the image.
    // (None of the current prompt files contain literal ID/name examples any more -
    // this list is kept as a permanent guard against the failure mode returning.)
    private val knownPromptLeaks = listOf(
        "RAJESH KUMAR SHARMA", "MOHAN LAL SHARMA", "AFZPK7190K", "12/03/1978",
        "ABCPK1234F", "ABCDE1234F", "482910374652", "M1234567", "DL0201234567",
        "full name in English", "FULL NAME IN ENGLISH", "English or null",
        "document number", "FATHER OR MOTHER NAME",
        // The model copied this literal anchor text as a value instead of reading the
        // parent's name printed beneath it (found 2026-09-16 on a live card). Kept here
        // as a permanent guard even though the prompt no longer quotes these labels.
        "Father's Name", "Fathers Name", "Mother's Name", "Mothers Name",
        "Permanent Account Number", "INCOME TAX DEPARTMENT",
        // Example Aadhaar number that was in an earlier draft of user_aadhaar.txt.
        "8416 1590 3267", "841615903267",
        // Quoted field-locating labels and example values from an earlier draft of
        // user_passport.txt (found by audit workflow, 2026-09-16, before any live
        // passport test - passport remains otherwise unverified against a real sample).
        "Name of Father", "Legal Guardian", "Surname", "Given Name(s)", "Nationality",
        "Date of Birth", "Date of Issue", "Date of Expiry", "Place of Issue",
        "Place of Birth", "Sex", "15-JUN-1990", "New Delhi", "Mumbai", "INDIAN",
        // Example vehicle-class codes from an earlier draft of user_dl.txt.
        "LMV, MCWG", "LMV, MCWG, HMV",
        // Confirmed live 2026-09-16: a real DL's own vehicle-class TABLE HEADER text,
        // copied into vehicle_classes instead of the data rows beneath it (MCWG/LMV).
        // Unlike other leaks this one came from the document image itself, not the
        // prompt - the prompt never mentions this phrase.
        "Class of Vehicle"
    )

    // Phrase FRAGMENTS: flagged if they appear anywhere INSIDE a value, even glued to
    // other text - unlike knownPromptLeaks above, which only fires on an exact whole-
    // value match. See findLeaks() below for why both kinds of check are needed.
    private val knownPromptLeakFragments = listOf(
        // Confirmed live 2026-09-16 on a tt_id back-side extraction: the prompt's own
        // description of skin_colour ("colour of skin") leaked into a DIFFERENT
        // field, social_assistance_number, glued to the real value ("COLOUR OF SKIN
        // BROWN"). The prompt was reworded to remove this phrase entirely; kept here
        // as a permanent guard in case it resurfaces in some other combination.
        "colour of skin",
        "colour of eyes"
    )

    /** Flags any field whose value matches text from one of our own prompt files - such
     *  a value was copied from the prompt, not read from the document. Checked before
     *  every other rule: a leaked value is otherwise indistinguishable from a correct,
     *  well-formed answer. */
    fun findLeaks(obj: org.json.JSONObject): List<String> = obj.keys().asSequence().mapNotNull { k ->
        val v = str(obj, k) ?: return@mapNotNull null
        val exactLeak = knownPromptLeaks.any { it.equals(v, ignoreCase = true) }
        // Contains-match, not exact-match: the failure mode isn't always "the
        // whole field IS the leaked string" - found 2026-09-16, the model glued
        // skin_colour's own prompt description onto a real value and filed it
        // under a DIFFERENT field ("COLOUR OF SKIN BROWN" under
        // social_assistance_number). An exact-match entry only catches that one
        // specific recombination; this catches the phrase regardless of what
        // else gets glued to it.
        val fragmentLeak = knownPromptLeakFragments.any { v.contains(it, ignoreCase = true) }
        if (exactLeak || fragmentLeak) "$k = '$v'" else null
    }.toList()

    /** Parses one side's raw model output into a JSONObject, checking for malformed
     *  JSON and prompt/leak fabrication. Returns (object, null) on success or
     *  (null, errorReport) on failure - shared by the single-image validator below and
     *  the two-sided (front+back) validator further down. */
    fun parseSide(label: String, text: String): Pair<org.json.JSONObject?, String?> {
        val start = text.indexOf('{')
        val end = text.lastIndexOf('}')
        if (start < 0 || end <= start) return null to "$label check: FAILED - no JSON object found"

        val obj = try {
            org.json.JSONObject(text.substring(start, end + 1))
        } catch (e: org.json.JSONException) {
            return null to "$label check: FAILED - malformed JSON (${e.message})"
        }

        val leaked = findLeaks(obj)
        if (leaked.isNotEmpty()) {
            return null to (
                "$label check: FABRICATED - value(s) copied from the prompt, " +
                    "not read from the document:\n" +
                    leaked.joinToString("\n") { "  - $it" } +
                    "\nDo not trust any field in this output."
                )
        }
        return obj to null
    }

    /**
     * Strips llama.cpp's startup log preamble, keeping just the generated answer.
     * llama-mtmd-cli's startup log includes an unrelated example conversation
     * ("Hello" / "Hi there" / "How are you?") dumped as a chat-template preview,
     * followed later by the real timestamped log lines, then the actual answer.
     * Filtering line-by-line let that example conversation leak through (its lines
     * have no timestamp prefix). Anchoring on the LAST log/warning line instead
     * skips both the log preamble and the earlier template dump in one step.
     */
    fun extractAnswer(raw: String): String {
        val logLinePattern = Regex("^\\d+\\.\\d+\\.\\d+\\.\\d+\\s+[IWE]\\s")
        val lines = raw.lines()
        val lastLogIndex = lines.indexOfLast {
            logLinePattern.containsMatchIn(it) ||
                it.trimStart().startsWith("WARN:") ||
                it.trim().startsWith("For normal use cases")
        }
        val answerLines = if (lastLogIndex >= 0) lines.drop(lastLogIndex + 1) else lines
        val text = answerLines.joinToString("\n").trim()
        return text.ifEmpty { raw.trim() }
    }

    /**
     * Pulls llama.cpp's own per-stage timing summary out of the CLI's merged stdout+stderr so the
     * run log records where the wall-clock actually went, instead of one opaque total. The binary
     * already prints these ("llama_perf_context_print: prompt eval time = ... / N tokens" and the
     * matching "eval time" line) on every run; extractAnswer() strips them as preamble. Prompt eval
     * is where vision encoding lands, which is the split needed to tell "too many vision tiles"
     * apart from "too many generated tokens" - the two call for opposite fixes. Returns a short
     * one-line summary, or a marker if the binary didn't emit the block.
     */
    fun cliDiag(raw: String?): String {
        if (raw.isNullOrEmpty()) return "perf: no output"
        val lines = raw.lineSequence().filter { it.contains("llama_perf_context_print") }.toList()
        if (lines.isEmpty()) return "perf: not reported by binary"

        fun grab(label: String): String {
            val line = lines.firstOrNull { it.contains(label) } ?: return "$label=?"
            val ms = Regex("= *([0-9.]+) ms").find(line)?.groupValues?.get(1) ?: return "$label=?"
            val count = Regex("/ *([0-9]+) (tokens|runs)").find(line)?.groupValues?.get(1) ?: return "$label=?"
            return "$label=${ms}ms/$count"
        }
        return "perf: ${grab("prompt eval time")} ${grab("eval time")}"
    }
}
