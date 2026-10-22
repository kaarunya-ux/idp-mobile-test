package com.modeltesting.vlmtester

import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale

/**
 * Appends one plain-text block per completed run (or per status event such as a server warm-up)
 * to the run log: run number, document type, timestamp, then the exact response and validation
 * report shown on screen. The file holds extracted document fields, so the caller keeps it in
 * private storage. The run counter lives in the activity because it needs SharedPreferences.
 */
class RunLog(private val file: File) {
    fun appendRun(runNumber: Int, docType: String, date: Date, body: String) {
        val timestamp = SimpleDateFormat("yyyy-MM-dd HH:mm:ss", Locale.US).format(date)
        file.appendText("=== Run $runNumber | $docType | $timestamp ===\n$body\n\n")
    }

    fun appendStatus(statusPrefix: String, date: Date, summary: String) {
        file.appendText("=== $statusPrefix | $date ===\n$summary\n\n")
    }
}
