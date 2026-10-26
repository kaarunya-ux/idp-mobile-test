package com.modeltesting.vlmtester

/** Human-readable sizes for the storage and RAM lines (decimal units, current locale). */
object ByteFormat {
    fun format(bytes: Long): String {
        val gb = bytes / 1e9
        return if (gb >= 1.0) "%.2f GB".format(gb) else "%.0f MB".format(bytes / 1e6)
    }
}
