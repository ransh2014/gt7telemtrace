package app.trace.telemetry

/** Small helpers ported from udp.py / dashboard.py. */
object TraceUtil {
    private val WIN_RESERVED = setOf("con", "prn", "aux", "nul") + (1..9).map { "com$it" } + (1..9).map { "lpt$it" }
    private const val MAX_NAME_LEN = 64

    /** udp.sanitize */
    fun sanitize(name: String?): String {
        if (name.isNullOrEmpty()) return "unknown"
        val sb = StringBuilder()
        for (c in name.lowercase().trim()) sb.append(if (c.isLetterOrDigit()) c else '_')
        var safe = sb.toString()
        while ("__" in safe) safe = safe.replace("__", "_")
        safe = safe.trim('_')
        if (safe.isEmpty()) return "unknown"
        if (safe.length > MAX_NAME_LEN) safe = safe.substring(0, MAX_NAME_LEN).trimEnd('_')
        if (safe in WIN_RESERVED) safe += "_"
        return safe.ifEmpty { "unknown" }
    }

    /** dashboard.ms_to_laptime */
    fun msToLapTime(ms: Int): String {
        if (ms <= 0) return "--:--.---"
        val s = ms / 1000.0
        val m = (s / 60).toInt()
        val sec = s % 60
        return "%d:%06.3f".format(java.util.Locale.ROOT, m, sec)
    }

    enum class Band { NONE, COLD, GOOD, WARM, HOT, BAD }

    /** dashboard.temp_color: NONE(grey) / COLD(blue) / GOOD(green) / WARM(amber) / HOT(red) */
    fun tempBand(v: Double, cold: Double = 60.0, good: Double = 80.0, hot: Double = 100.0): Band = when {
        v <= 0 -> Band.NONE; v < cold -> Band.COLD; v < good -> Band.GOOD; v < hot -> Band.WARM; else -> Band.HOT
    }

    /** dashboard.slip_color: NONE / BAD(red) / GOOD(green) / WARM(amber) */
    fun slipBand(v: Double): Band = when {
        v <= 0 -> Band.NONE; v < 0.95 -> Band.BAD; v < 1.05 -> Band.GOOD; v < 1.15 -> Band.WARM; else -> Band.BAD
    }

    /** dashboard.lap_clock_running */
    fun lapClockRunning(curLap: Int, totalLaps: Int, paused: Boolean, onTrack: Boolean, loading: Boolean?): Boolean {
        if (curLap < 1 || paused || !onTrack || loading == true) return false
        return !(totalLaps > 0 && curLap > totalLaps)
    }

    /** leaderboard.PHYSICS_ERA -- bump with each GT7 physics change (1.72 on Oct 15). */
    const val PHYSICS_ERA = "1.71"
    private const val EPOCH_STAMP = "20260820_000000"

    fun physicsEraOf(recordedAt: String?): String? {
        val s = (recordedAt ?: "").trim()
        if (s.length < 8 || !s.substring(0, 8).all { it.isDigit() }) return null
        return if (s >= EPOCH_STAMP.substring(0, minOf(s.length, EPOCH_STAMP.length))) PHYSICS_ERA else "pre-$PHYSICS_ERA"
    }

    fun isCurrentEra(physicsEra: String?, recordedAt: String?): Boolean {
        if (!physicsEra.isNullOrEmpty()) return physicsEra == PHYSICS_ERA
        val era = physicsEraOf(recordedAt)
        return era == null || era == PHYSICS_ERA
    }
}
