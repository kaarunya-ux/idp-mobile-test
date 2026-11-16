package com.modeltesting.vlmtester

import java.util.Date
import java.util.TimeZone
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Assert.assertFalse
import org.junit.Before
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder

/** JVM unit tests for [RunLog]. */
class RunLogTest {
    @get:Rule
    val folder = TemporaryFolder()

    private val originalZone: TimeZone = TimeZone.getDefault()

    @Before
    fun useUtc() {
        TimeZone.setDefault(TimeZone.getTimeZone("UTC"))
    }

    @After
    fun restoreZone() {
        TimeZone.setDefault(originalZone)
    }

    @Test
    fun `appendRun writes the header with run number, document type and timestamp, then the body`() {
        val file = folder.newFile("run_log.txt")
        RunLog(file).appendRun(7, "aadhaar", Date(0L), "{\"name\":\"A\"}\nAadhaar check: HIGH")
        assertEquals(
            "=== Run 7 | aadhaar | 1970-01-01 00:00:00 ===\n{\"name\":\"A\"}\nAadhaar check: HIGH\n\n",
            file.readText()
        )
    }

    @Test
    fun `appendRun adds to the file and keeps earlier runs`() {
        val file = folder.newFile("run_log.txt")
        val log = RunLog(file)
        log.appendRun(1, "pan_full", Date(0L), "first")
        log.appendRun(2, "pan_full", Date(86_400_000L), "second")
        assertEquals(
            "=== Run 1 | pan_full | 1970-01-01 00:00:00 ===\nfirst\n\n" +
                "=== Run 2 | pan_full | 1970-01-02 00:00:00 ===\nsecond\n\n",
            file.readText()
        )
    }

    @Test
    fun `appendRun creates the file when it does not exist yet`() {
        val file = java.io.File(folder.root, "new_log.txt")
        assertFalse(file.exists())
        RunLog(file).appendRun(1, "dl", Date(0L), "body")
        assertEquals("=== Run 1 | dl | 1970-01-01 00:00:00 ===\nbody\n\n", file.readText())
    }

    @Test
    fun `appendStatus writes the prefix, the date and the summary`() {
        val file = folder.newFile("run_log.txt")
        val date = Date(0L)
        RunLog(file).appendStatus("Server warm-up", date, "server up 1.0s, total 2.0s")
        assertEquals("=== Server warm-up | $date ===\nserver up 1.0s, total 2.0s\n\n", file.readText())
    }
}
