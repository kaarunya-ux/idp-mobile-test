package com.modeltesting.vlmtester

import java.io.File

/** Stop flag for a running monitor thread. */
class MonitorHandle {
    @Volatile
    var stop = false
}

/** Reads a whole file, or null when it cannot be read (the /proc and /sys files come and go with the process). */
internal fun readTextOrNull(path: String): String? = runCatching { File(path).readText() }.getOrNull()

/**
 * Samples one child process (the model) every 300 ms for the live stats card: seconds elapsed, resident
 * memory, CPU since the last sample and the GPU counters. Moved out of MainActivity without changing
 * what is read or how often; the file reader and the sleep are parameters so unit tests can fake them.
 */
class ProcessMonitor(
    private val clkTck: Long,
    private val readFile: (String) -> String? = ::readTextOrNull,
    private val sleepMs: (Long) -> Unit = { Thread.sleep(it) },
    private val publish: (Stats) -> Unit
) {
    data class Stats(
        val elapsedS: Double,
        val ramMb: Double?,
        val cpuPct: Double?,
        val gpuBusy: String?,
        val gpuClock: String?
    )

    fun start(pid: Long, startNanos: Long): MonitorHandle {
        val handle = MonitorHandle()
        var lastCpuTicks = -1L
        var lastWallNanos = startNanos

        Thread {
            while (!handle.stop) {
                val elapsedS = (System.nanoTime() - startNanos) / 1e9
                val ramMb = readFile("/proc/$pid/status")?.let { ProcessStats.parseVmRssMb(it) }
                val nowWallNanos = System.nanoTime()
                val stat = readFile("/proc/$pid/stat")?.lineSequence()?.firstOrNull()
                val sample = ProcessStats.cpuSample(stat, lastCpuTicks, lastWallNanos, nowWallNanos, clkTck)
                lastCpuTicks = sample.ticks
                lastWallNanos = sample.wallNanos
                val gpuBusy = readFile("/sys/kernel/gpu/gpu_busy")?.trim()
                val gpuClock = readFile("/sys/kernel/gpu/gpu_clock")?.trim()
                publish(Stats(elapsedS, ramMb, sample.percent, gpuBusy, gpuClock))
                sleepMs(SAMPLE_INTERVAL_MS)
            }
        }.start()
        return handle
    }

    private companion object {
        const val SAMPLE_INTERVAL_MS = 300L
    }
}

/** Finds the process id of a child the app started. */
object ProcessIds {
    /** java.lang.Process.pid() isn't exposed by this compileSdk's Android stub. Android's
     *  ProcessBuilder implementation still stores the child's pid in a "pid" field on the
     *  returned Process object, so pull it via reflection (the standard pre-API-26 trick). */
    fun of(process: Process): Long? = runCatching {
        val field = process.javaClass.getDeclaredField("pid")
        field.isAccessible = true
        field.getInt(process).toLong()
    }.getOrNull()

    /** Fallback if reflection ever fails on some OEM build: resolve via `pidof`. */
    fun waitFor(processName: String): Long? {
        repeat(PIDOF_TRIES) {
            val pid = runCatching {
                val p = ProcessBuilder("/system/bin/sh", "-c", "pidof $processName").start()
                val out = p.inputStream.bufferedReader().readText().trim()
                p.waitFor()
                out.split(Regex("\\s+")).firstOrNull()?.toLongOrNull()
            }.getOrNull()
            if (pid != null) return pid
            Thread.sleep(PIDOF_PAUSE_MS)
        }
        return null
    }

    private const val PIDOF_TRIES = 20
    private const val PIDOF_PAUSE_MS = 50L
}
