package com.modeltesting.vlmtester

import org.junit.Assert.assertEquals
import org.junit.Assert.assertNull
import org.junit.Test

/** JVM unit tests for [ProcessStats]. The /proc lines below are shaped like real Linux output. */
class ProcessStatsTest {
    // utime = 500 and stime = 200 clock ticks
    private val stat = "1234 (llama-server) S 1 1 1 0 -1 4194560 100 0 0 0 500 200 0 0 20 0 8 0 12345 1000000 5000"

    // --- parseVmRssMb ---------------------------------------------------------------------

    @Test
    fun `parseVmRssMb reads the resident size in megabytes`() {
        val status = "Name:\tllama-server\nVmPeak:\t 2097152 kB\nVmRSS:\t 1048576 kB\nThreads:\t8\n"
        assertEquals(1024.0, ProcessStats.parseVmRssMb(status)!!, 0.0)
        assertEquals(0.5, ProcessStats.parseVmRssMb("VmRSS: 512 kB")!!, 0.0)
    }

    @Test
    fun `parseVmRssMb returns null when the line is missing or not a number`() {
        assertNull(ProcessStats.parseVmRssMb("Name:\tllama-server\nThreads:\t8\n"))
        assertNull(ProcessStats.parseVmRssMb("VmRSS:\tlots kB"))
        assertNull(ProcessStats.parseVmRssMb(""))
    }

    // --- parseCpuTicks --------------------------------------------------------------------

    @Test
    fun `parseCpuTicks adds user and system time`() {
        assertEquals(700L, ProcessStats.parseCpuTicks(stat))
    }

    @Test
    fun `parseCpuTicks returns null when the line is too short or not numeric`() {
        assertNull(ProcessStats.parseCpuTicks("1234 (llama-server) S 1 1 1"))
        assertNull(ProcessStats.parseCpuTicks("1234 (llama-server) S 1 1 1 0 -1 4194560 100 0 0 0 x 200"))
        assertNull(ProcessStats.parseCpuTicks(""))
    }

    // --- cpuSample ------------------------------------------------------------------------

    @Test
    fun `cpuSample only records the figures on the first sample`() {
        val sample = ProcessStats.cpuSample(
            stat,
            lastTicks = -1L,
            lastWallNanos = 0L,
            nowWallNanos = 1_000L,
            clkTck = 100L
        )
        assertEquals(ProcessStats.CpuSample(null, 700L, 1_000L), sample)
    }

    @Test
    fun `cpuSample reports percent of one core between two samples`() {
        // 200 ticks at 100 ticks per second is 2 CPU-seconds in 1 wall-second: 200 percent.
        val sample = ProcessStats.cpuSample(
            stat,
            lastTicks = 500L,
            lastWallNanos = 1_000_000_000L,
            nowWallNanos = 2_000_000_000L,
            clkTck = 100L
        )
        assertEquals(200.0, sample.percent!!, 1e-9)
        assertEquals(700L, sample.ticks)
        assertEquals(2_000_000_000L, sample.wallNanos)
    }

    @Test
    fun `cpuSample reports no percent when no wall time has passed`() {
        val sample = ProcessStats.cpuSample(
            stat,
            lastTicks = 500L,
            lastWallNanos = 5L,
            nowWallNanos = 5L,
            clkTck = 100L
        )
        assertEquals(ProcessStats.CpuSample(null, 700L, 5L), sample)
    }

    @Test
    fun `cpuSample keeps the previous ticks when the line cannot be read`() {
        assertEquals(
            ProcessStats.CpuSample(null, 500L, 9L),
            ProcessStats.cpuSample(null, lastTicks = 500L, lastWallNanos = 1L, nowWallNanos = 9L, clkTck = 100L)
        )
        assertEquals(
            ProcessStats.CpuSample(null, 500L, 9L),
            ProcessStats.cpuSample("garbage", lastTicks = 500L, lastWallNanos = 1L, nowWallNanos = 9L, clkTck = 100L)
        )
    }
}
