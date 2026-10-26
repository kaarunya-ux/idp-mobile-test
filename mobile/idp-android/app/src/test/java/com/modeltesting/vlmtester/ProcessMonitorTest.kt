package com.modeltesting.vlmtester

import java.util.concurrent.CopyOnWriteArrayList
import java.util.concurrent.CountDownLatch
import java.util.concurrent.TimeUnit
import java.util.concurrent.atomic.AtomicInteger
import org.junit.Assert.assertEquals
import org.junit.Assert.assertNotNull
import org.junit.Assert.assertNull
import org.junit.Assert.assertTrue
import org.junit.Test

/** JVM unit tests for [ProcessMonitor], [ProcessIds] and [RunCounter] (moved out of MainActivity). */
class ProcessMonitorTest {
    private val files = mapOf(
        "/proc/42/status" to "Name:\tx\nVmRSS:\t   204800 kB\n",
        "/sys/kernel/gpu/gpu_busy" to " 7 \n",
        "/sys/kernel/gpu/gpu_clock" to "221000\n"
    )

    @Test
    fun `publishes elapsed time, memory, GPU counters and a missing CPU value until two samples exist`() {
        val seen = CopyOnWriteArrayList<ProcessMonitor.Stats>()
        val two = CountDownLatch(2)
        val monitor = ProcessMonitor(100L, readFile = { files[it] }, sleepMs = { Thread.sleep(1) }) {
            seen += it
            two.countDown()
        }
        val handle = monitor.start(42L, System.nanoTime())
        assertTrue(two.await(5, TimeUnit.SECONDS))
        handle.stop = true
        val first = seen.first()
        assertEquals(200.0, first.ramMb!!, 0.001)
        assertEquals("7", first.gpuBusy)
        assertEquals("221000", first.gpuClock)
        assertNull(first.cpuPct)
        assertTrue(first.elapsedS >= 0.0)
    }

    @Test
    fun `reports CPU use from the second sample on`() {
        val seen = CopyOnWriteArrayList<ProcessMonitor.Stats>()
        val three = CountDownLatch(3)
        val reads = AtomicInteger()
        val fakeFiles: (String) -> String? = { path ->
            if (path == "/proc/42/stat") {
                "42 (x) S " + List(10) { "0" }.joinToString(" ") + " ${reads.incrementAndGet() * 100} 0"
            } else {
                null
            }
        }
        val monitor = ProcessMonitor(100L, readFile = fakeFiles, sleepMs = { Thread.sleep(2) }) {
            seen += it
            three.countDown()
        }
        val handle = monitor.start(42L, System.nanoTime())
        assertTrue(three.await(5, TimeUnit.SECONDS))
        handle.stop = true
        assertNull(seen[0].cpuPct)
        assertTrue(seen[1].cpuPct!! > 0.0)
        assertTrue(seen[2].cpuPct!! > 0.0)
        assertNull(seen[1].ramMb)
    }

    @Test
    fun `stops sampling once the handle is stopped and keeps going when files are unreadable`() {
        val count = AtomicInteger()
        val started = CountDownLatch(1)
        val monitor = ProcessMonitor(100L, readFile = { null }, sleepMs = { Thread.sleep(1) }) {
            assertNull(it.ramMb)
            assertNull(it.gpuBusy)
            count.incrementAndGet()
            started.countDown()
        }
        val handle = monitor.start(1L, System.nanoTime())
        assertTrue(started.await(5, TimeUnit.SECONDS))
        handle.stop = true
        Thread.sleep(50)
        val after = count.get()
        Thread.sleep(50)
        assertEquals(after, count.get())
    }

    @Test
    fun `reads a missing file as null`() {
        assertNull(readTextOrNull("/definitely/not/here"))
        val f = java.io.File.createTempFile("pmon", ".txt")
        f.writeText("abc")
        f.deleteOnExit()
        assertEquals("abc", readTextOrNull(f.path))
    }

    @Test
    fun `finds a process id by reflection or gives up with null`() {
        val process = ProcessBuilder("true").start()
        val pid = ProcessIds.of(process)
        process.waitFor()
        assertTrue(pid == null || pid > 0)
        assertNotNull(ProcessMonitor.Stats(0.0, null, null, null, null))
    }

    @Test
    fun `counts runs from the stored value and stores the new one`() {
        var stored = 4
        val counter = RunCounter(read = { stored }, write = { stored = it })
        assertEquals(5, counter.next())
        assertEquals(6, counter.next())
        assertEquals(6, stored)
    }
}
