package com.modeltesting.vlmtester

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [OutputParser]. */
class OutputParserTest {
    // --- findLeaks ---------------------------------------------------------------------

    @Test
    fun `findLeaks returns nothing for ordinary values and ignores null fields`() {
        val obj = json("name" to "ASHA RAO", "pan_number" to "BXYPK5678Q", "dob" to null)
        assertTrue(OutputParser.findLeaks(obj).isEmpty())
    }

    @Test
    fun `findLeaks flags a value that is a known prompt example, ignoring case`() {
        assertEquals(listOf("pan_number = 'ABCPK1234F'"), OutputParser.findLeaks(json("pan_number" to "ABCPK1234F")))
        assertEquals(listOf("pan_number = 'abcpk1234f'"), OutputParser.findLeaks(json("pan_number" to "abcpk1234f")))
        assertEquals(listOf("name = 'Surname'"), OutputParser.findLeaks(json("name" to "Surname")))
    }

    @Test
    fun `findLeaks flags a prompt label copied as a value`() {
        assertEquals(
            listOf("parent_name = 'Father's Name'"),
            OutputParser.findLeaks(json("parent_name" to "Father's Name"))
        )
    }

    @Test
    fun `findLeaks flags a leaked phrase glued to a real value`() {
        val leaks = OutputParser.findLeaks(json("social_assistance_number" to "COLOUR OF SKIN BROWN"))
        assertEquals(listOf("social_assistance_number = 'COLOUR OF SKIN BROWN'"), leaks)
    }

    @Test
    fun `findLeaks does not flag a value that merely contains an example as a longer word`() {
        // Exact examples must match the whole value; only the fragment list matches inside a value.
        assertTrue(OutputParser.findLeaks(json("name" to "Mumbai Kumar")).isEmpty())
    }

    @Test
    fun `findLeaks reports every leaking field`() {
        val leaks = OutputParser.findLeaks(json("a" to "ABCPK1234F", "b" to "Mumbai", "c" to "fine")).sorted()
        assertEquals(listOf("a = 'ABCPK1234F'", "b = 'Mumbai'"), leaks)
    }

    // --- parseSide ---------------------------------------------------------------------

    @Test
    fun `parseSide returns the object for clean JSON`() {
        val (obj, error) = OutputParser.parseSide("PAN", """{"name":"ASHA RAO"}""")
        assertNull(error)
        assertEquals("ASHA RAO", obj?.getString("name"))
    }

    @Test
    fun `parseSide reads the object out of surrounding text`() {
        val (obj, error) = OutputParser.parseSide("PAN", """Here you go: {"name":"ASHA RAO"} hope that helps""")
        assertNull(error)
        assertEquals("ASHA RAO", obj?.getString("name"))
    }

    @Test
    fun `parseSide fails when there is no JSON object`() {
        for (text in listOf("", "no json at all", "} backwards {", "{")) {
            val (obj, error) = OutputParser.parseSide("PAN", text)
            assertNull(text, obj)
            assertEquals(text, "PAN check: FAILED - no JSON object found", error)
        }
    }

    @Test
    fun `parseSide fails on malformed JSON`() {
        val (obj, error) = OutputParser.parseSide("AADHAAR", "{ not json }")
        assertNull(obj)
        assertNotNull(error)
        assertTrue(error, error!!.startsWith("AADHAAR check: FAILED - malformed JSON ("))
    }

    @Test
    fun `parseSide rejects output copied from the prompt`() {
        val (obj, error) = OutputParser.parseSide("PAN", """{"pan_number":"ABCPK1234F"}""")
        assertNull(obj)
        assertEquals(
            "PAN check: FABRICATED - value(s) copied from the prompt, not read from the document:\n" +
                "  - pan_number = 'ABCPK1234F'\n" +
                "Do not trust any field in this output.",
            error
        )
    }

    // --- extractAnswer -----------------------------------------------------------------

    @Test
    fun `extractAnswer drops the startup log and the template preview before the answer`() {
        val raw = "0.00.100.000 I load: model loaded\nHello\nHi there\nHow are you?\n" +
            "0.01.200.000 I main: ready\n{\"name\":\"A\"}\n"
        assertEquals("{\"name\":\"A\"}", OutputParser.extractAnswer(raw))
    }

    @Test
    fun `extractAnswer treats WARN and the normal-use-cases line as log lines`() {
        assertEquals("answer", OutputParser.extractAnswer("noise\nWARN: something odd\nanswer"))
        assertEquals("answer", OutputParser.extractAnswer("noise\nFor normal use cases, use --foo\nanswer"))
    }

    @Test
    fun `extractAnswer keeps only what follows the last log line`() {
        val raw = "0.00.100.000 I first\nstale text\n0.02.000.000 W second\nthe answer"
        assertEquals("the answer", OutputParser.extractAnswer(raw))
    }

    @Test
    fun `extractAnswer returns the whole trimmed text when there are no log lines`() {
        assertEquals("just an answer", OutputParser.extractAnswer("  just an answer \n"))
    }

    @Test
    fun `extractAnswer falls back to the trimmed raw text when nothing follows the log`() {
        assertEquals("0.00.100.000 I only a log line", OutputParser.extractAnswer("0.00.100.000 I only a log line\n"))
    }

    // --- cliDiag -----------------------------------------------------------------------

    @Test
    fun `cliDiag says so when there is no output or no timing block`() {
        assertEquals("perf: no output", OutputParser.cliDiag(null))
        assertEquals("perf: no output", OutputParser.cliDiag(""))
        assertEquals("perf: not reported by binary", OutputParser.cliDiag("just an answer\nand more"))
    }

    @Test
    fun `cliDiag summarises the timing lines`() {
        val raw = "answer\n" +
            "llama_perf_context_print: prompt eval time =   12345.67 ms /   300 tokens (41.15 ms per token)\n" +
            "llama_perf_context_print:        eval time =    2000.00 ms /    24 runs   (83.33 ms per token)\n"
        // Existing behaviour: the "eval time" lookup takes the first line containing those words,
        // which is the "prompt eval time" line, so both figures show the prompt values.
        assertEquals(
            "perf: prompt eval time=12345.67ms/300 eval time=12345.67ms/300",
            OutputParser.cliDiag(raw)
        )
    }

    @Test
    fun `cliDiag marks a figure it cannot read`() {
        val raw = "llama_perf_context_print: prompt eval time = unknown\n"
        assertEquals("perf: prompt eval time=? eval time=?", OutputParser.cliDiag(raw))
    }
}
