package app.trace.telemetry

import kotlin.math.abs
import kotlin.math.hypot
import kotlin.math.max

/** validate.py: flags laps with shortcuts, teleports or physically impossible data. clean / suspect / invalid. */
object Validate {
    const val MAX_ACCEL_MS2 = 60.0
    const val MAX_JUMP_M = 60.0
    const val MAX_LAT_G = 6.5
    const val MAX_SPEED_KMH = 650.0
    const val MIN_ROWS = 60
    const val LENGTH_TOL = 0.04
    private val SURFACE_OFF = setOf(2.0, 3.0, 4.0, 5.0)   // legacy numeric surface codes that are not asphalt
    const val OFF_LETTERS = "DGSs"   // recorded surface_type is one letter per wheel: D dirt, G grass, S sand, s snow (T tarmac, C kerb)
    const val OFF_WHEELS = 2         // a sample is off the tarmac when this many wheels are on those

    class Issue(val code: String, val level: String, val text: String)
    class Result(val verdict: String, val issues: List<Issue>)

    /** One surface_type sample: per-wheel letters ("TTGG"), or a legacy numeric code. */
    internal fun sampleOff(v: String): Boolean {
        val s = v.trim()
        val num = s.toDoubleOrNull()
        return if (num != null) num in SURFACE_OFF else s.count { it in OFF_LETTERS } >= OFF_WHEELS
    }

    private fun allFinite(a: DoubleArray): Boolean { for (x in a) if (!x.isFinite()) return false; return true }

    /** df is null or short -> invalid; refLengthM / lapTimeS are optional, like the desktop. */
    fun check(df: Frame?, refLengthM: Double? = null, lapTimeS: Double? = null): Result {
        val issues = ArrayList<Issue>()
        if (df == null || df.n < MIN_ROWS) return Result("invalid", listOf(Issue("short", "invalid", "Too few samples to be a lap.")))
        fun col(name: String): DoubleArray? = if (df.has(name)) df[name] else null
        var pos = col("track_position"); var spd = col("speed_kmh"); val t = col("t")
        if (pos == null || spd == null) return Result("invalid", listOf(Issue("columns", "invalid", "Missing position or speed.")))
        if (!allFinite(pos) || !allFinite(spd)) {
            issues.add(Issue("nan", "invalid", "The lap has NaN/infinite values."))
            pos = Np.nanToNum(pos); spd = Np.nanToNum(spd)
        }
        val smax = Np.max(spd)
        if (smax > MAX_SPEED_KMH) issues.add(Issue("speed", "invalid", "Speed reaches ${fixed(smax, 0)} km/h."))

        if (t != null && allFinite(t)) {
            val dt = Np.diffN(t)
            if (dt.any { it < -1e-6 }) issues.add(Issue("time_back", "invalid", "Time runs backwards."))
            val ok = BooleanArray(dt.size) { dt[it] > 1e-3 }
            if (ok.count { it } > 10) {
                var nBad = 0
                for (i in dt.indices) if (ok[i]) { val acc = abs(spd[i + 1] / 3.6 - spd[i] / 3.6) / dt[i]; if (acc > MAX_ACCEL_MS2) nBad++ }
                if (nBad >= 3) issues.add(Issue("accel", "invalid", "$nBad samples accelerate faster than any car can."))
            }
            val span = t[t.size - 1] - t[0]
            if (lapTimeS != null && lapTimeS != 0.0 && abs(span - lapTimeS) > max(1.5, 0.02 * lapTimeS))
                issues.add(Issue("time_mismatch", "suspect", "Samples span ${fixed(span, 1)}s but the lap time is ${fixed(lapTimeS, 1)}s."))
        }

        if (df.has("world_x") && df.has("world_z")) {
            val x = df["world_x"]; val z = df["world_z"]
            var big = 0
            for (i in 0 until x.size - 1) if (hypot(x[i + 1] - x[i], z[i + 1] - z[i]) > MAX_JUMP_M) big++
            if (big > 0) issues.add(Issue("teleport", if (big > 2) "invalid" else "suspect",
                "The car jumps >${fixed(MAX_JUMP_M, 0)} m between samples $big time(s)."))
        }

        val length = Np.max(pos) - Np.min(pos)
        if (refLengthM != null && refLengthM > 100) {
            val off = abs(length - refLengthM) / refLengthM
            if (off > LENGTH_TOL) issues.add(Issue("length", "suspect",
                "Lap is ${fixed(length, 0)} m against ${fixed(refLengthM, 0)} m: possible shortcut or wrong layout."))
        }

        val surfStr = df.str("surface_type")
        if (surfStr.isNotEmpty()) {
            var off = 0
            for (s in surfStr) if (sampleOff(s)) off++
            val share = off.toDouble() / surfStr.size
            if (share > 0.05) issues.add(Issue("off_track", "suspect", "${fixed(share * 100, 0)}% of the lap is off the tarmac."))
        }
        if (df.has("ang_y")) {
            val ay = df["ang_y"]
            var lat = Double.NEGATIVE_INFINITY
            for (i in ay.indices) { val v = abs(ay[i] * spd[i] / 3.6 / 9.81); if (v > lat) lat = v }
            if (lat > MAX_LAT_G) issues.add(Issue("lat_g", "suspect", "Peak lateral load ${fixed(lat, 1)} g is unrealistic."))
        }
        if (t != null && t.size > 20 && allFinite(t)) {
            val dt = DoubleArray(t.size - 1) { max(t[it + 1] - t[it], 1e-3) }
            val brk = if (df.has("brake")) df["brake"] else null
            var hits = 0
            for (i in dt.indices) {
                val decel = -(spd[i + 1] / 3.6 - spd[i] / 3.6) / dt[i]
                if (decel > 35.0 && (brk == null || brk[i + 1] < 0.2)) hits++
            }
            if (hits >= 2) issues.add(Issue("wall", "suspect", "$hits sudden stops with no braking: likely wall contact."))
        }

        val verdict = if (issues.any { it.level == "invalid" }) "invalid" else if (issues.isNotEmpty()) "suspect" else "clean"
        return Result(verdict, issues)
    }

    fun describe(res: Result): String =
        if (res.verdict == "clean") "Lap looks clean." else "Lap is ${res.verdict}: " + res.issues.joinToString(" ") { it.text }

    /** Only clean laps go to the leaderboard. */
    fun submissionOk(res: Result) = res.verdict == "clean"
}
