package com.modeltesting.vlmtester

import java.util.Locale
import org.junit.After
import org.junit.Assert.assertEquals
import org.junit.Before
import org.junit.Test

/** JVM unit tests for [ByteFormat]. */
class ByteFormatTest {
    private val originalLocale: Locale = Locale.getDefault()

    @Before
    fun useUsLocale() {
        Locale.setDefault(Locale.US)
    }

    @After
    fun restoreLocale() {
        Locale.setDefault(originalLocale)
    }

    @Test
    fun `formats a gigabyte or more with two decimals`() {
        assertEquals("1.00 GB", ByteFormat.format(1_000_000_000L))
        assertEquals("2.35 GB", ByteFormat.format(2_350_000_000L))
        assertEquals("7.60 GB", ByteFormat.format(7_600_000_000L))
    }

    @Test
    fun `formats less than a gigabyte in whole megabytes`() {
        assertEquals("529 MB", ByteFormat.format(529_101_536L))
        assertEquals("728 MB", ByteFormat.format(727_954_528L))
        assertEquals("0 MB", ByteFormat.format(0L))
    }

    @Test
    fun `rounds up to 1000 MB just under a gigabyte`() {
        // Existing behaviour: the unit switches at exactly 1 GB, so 999.9 MB prints as 1000 MB.
        assertEquals("1000 MB", ByteFormat.format(999_999_999L))
    }
}
