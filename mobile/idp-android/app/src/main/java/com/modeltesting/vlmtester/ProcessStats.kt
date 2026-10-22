package com.modeltesting.vlmtester

/** Reads memory and CPU figures out of the text of /proc/<pid>/status and /proc/<pid>/stat. Pure. */
object ProcessStats {
    /** CPU use since the previous sample, plus the figures the next sample needs. */
    data class CpuSample(val percent: Double?, val ticks: Long, val wallNanos: Long)

    /** Resident memory in MB from the text of /proc/<pid>/status, or null if it is not there. */
    fun parseVmRssMb(status: String): Double? {
        val line = status.lines().firstOrNull { it.startsWith("VmRSS:") } ?: return null
        val kb = line.substringAfter("VmRSS:").trim().split(Regex("\\s+")).firstOrNull()?.toLongOrNull()
        return kb?.let { it / 1024.0 }
    }

    /**
     * Total CPU time in clock ticks (user plus system) from one line of /proc/<pid>/stat, or null.
     * Fields after the comm field "(name)" are space-separated; utime is the 14th and stime the
     * 15th field counting from 1.
     */
    fun parseCpuTicks(stat: String): Long? {
        val fields = stat.substringAfter(") ").split(" ")
        val utime = fields.getOrNull(UTIME_INDEX)?.toLongOrNull() ?: return null
        val stime = fields.getOrNull(STIME_INDEX)?.toLongOrNull() ?: return null
        return utime + stime
    }

    /**
     * CPU percent (of one core) between the previous sample and this one. [lastTicks] is negative
     * for the first sample, which can only record its figures. A sample that cannot be read keeps
     * the previous ticks and records the current time.
     */
    fun cpuSample(stat: String?, lastTicks: Long, lastWallNanos: Long, nowWallNanos: Long, clkTck: Long): CpuSample {
        val totalTicks = stat?.let { parseCpuTicks(it) } ?: return CpuSample(null, lastTicks, nowWallNanos)
        if (lastTicks < 0) return CpuSample(null, totalTicks, nowWallNanos)

        val deltaTicks = totalTicks - lastTicks
        val deltaWallSeconds = (nowWallNanos - lastWallNanos) / 1e9
        if (deltaWallSeconds <= 0) return CpuSample(null, totalTicks, nowWallNanos)

        return CpuSample((deltaTicks / clkTck.toDouble()) / deltaWallSeconds * 100.0, totalTicks, nowWallNanos)
    }

    private const val UTIME_INDEX = 11
    private const val STIME_INDEX = 12
}
