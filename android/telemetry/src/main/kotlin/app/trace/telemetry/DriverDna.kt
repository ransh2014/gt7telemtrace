package app.trace.telemetry

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/** trait -> score; precision is null until 3+ laps are given. */
typealias DnaTraits = Map<String, Double?>

/**
 * driver_dna.py: five 0-100 style traits from a lap, tracked over time and comparable with a friend through a short
 * `DNA1:name:v,v,v,v,v` code. Describes how a lap was driven, not whether it was fast.
 */
object DriverDna {
    val TRAITS = listOf("brake_aggression", "trail_braking", "smoothness", "throttle_modulation", "precision")
    val LABELS = mapOf(
        "brake_aggression" to "Brake aggression", "trail_braking" to "Trail braking", "smoothness" to "Smoothness",
        "throttle_modulation" to "Throttle modulation", "precision" to "Precision",
    )
    const val MAX_HISTORY = 200

    private fun clip(x: Double) = max(0.0, min(100.0, x))

    private fun runs(mask: BooleanArray): List<IntArray> {
        val out = ArrayList<IntArray>(); var start = -1
        for (i in mask.indices) {
            if (mask[i] && start < 0) start = i
            else if (!mask[i] && start >= 0) { out.add(intArrayOf(start, i)); start = -1 }
        }
        if (start >= 0) out.add(intArrayOf(start, mask.size))
        return out
    }

    /** np.gradient(f, x) for non-uniform x, first-order edges. */
    internal fun gradient(f: DoubleArray, x: DoubleArray): DoubleArray {
        val n = f.size
        val g = DoubleArray(n)
        if (n < 2) return g
        g[0] = (f[1] - f[0]) / (x[1] - x[0])
        g[n - 1] = (f[n - 1] - f[n - 2]) / (x[n - 1] - x[n - 2])
        for (i in 1 until n - 1) {
            val hs = x[i] - x[i - 1]; val hd = x[i + 1] - x[i]
            g[i] = (hs * hs * f[i + 1] + (hd * hd - hs * hs) * f[i] - hd * hd * f[i - 1]) / (hs * hd * (hd + hs))
        }
        return g
    }

    private fun std0(a: DoubleArray): Double = Np.std(a, 0)

    /** Trait scores for one lap (null if it can't be read). otherLaps are more frames from the same session; 3+ laps in total fill precision. */
    fun profile(df: Frame, otherLaps: List<Frame?> = emptyList()): DnaTraits? {
        val d = Corners.prepare(df) ?: return null
        val t = d["t"]; val brk = d["brake"]; val thr = d["throttle"]; val st = d["steering"]
        val n = t.size
        val dt = DoubleArray(n) { i -> max(if (i == 0) t[0] - (t[0] - 0.05) else t[i] - t[i - 1], 1e-3) }

        val on = BooleanArray(n) { brk[it] > 0.1 }
        val brakeAggr: Double
        if (on.any { it }) {
            val rs = runs(on)
            val peak = rs.map { r -> var m = Double.NEGATIVE_INFINITY; for (q in r[0] until r[1]) if (brk[q] > m) m = brk[q]; m }.average()
            val x = DoubleArray(n) { t[it] + it * 1e-9 }
            val rise = gradient(brk, x)
            val pos = rise.filter { it > 0 }.toDoubleArray()
            val rate = if (pos.isNotEmpty()) Np.percentile(pos, 90.0) else 0.0
            brakeAggr = clip(100 * (0.6 * peak + 0.4 * min(1.0, rate / 6.0)))
        } else brakeAggr = 0.0

        val corn = Corners.detectCorners(df)
        val trail = corn.filter { it.brakePos != null }
        var trailScore = clip(100 * (if (trail.isNotEmpty()) trail.map { if (it.trail) 1.0 else 0.0 }.average() else 0.0))
        if (on.any { it }) {
            var both = 0; var nOn = 0
            for (i in 0 until n) { if (on[i]) nOn++; if (on[i] && abs(st[i]) > 0.15) both++ }
            trailScore = clip(0.5 * trailScore + 50 * min(1.0, both.toDouble() / max(nOn, 1) * 2.5))
        }

        val jerk = DoubleArray(n) { i -> (if (i == 0) 0.0 else abs(st[i] - st[i - 1])) / dt[i] }
        val smooth = clip(100 * (1.0 - min(1.0, Np.percentile(jerk, 95.0) / 6.0)))

        val spd = d["speed_kmh"]
        var part = 0; var fullOrOff = 0
        for (i in 0 until n) {
            if (spd[i] > 30 && brk[i] < 0.05) {
                if (thr[i] > 0.1 && thr[i] < 0.9) part++
                if (thr[i] >= 0.9 || thr[i] <= 0.1) fullOrOff++
            }
        }
        val modulation = clip(100 * min(1.0, part.toDouble() / max(part + fullOrOff, 1) * 3.0))

        var precision: Double? = null
        val laps = listOf<Frame?>(df) + otherLaps
        val usable = laps.filterNotNull()
        if (usable.size >= 3) {
            val pts = LinkedHashMap<Int, ArrayList<Double>>()
            for (lap in usable) for (c in Corners.detectCorners(lap)) {
                val bp = c.brakePos ?: continue
                pts.getOrPut(Math.rint(c.apex / 60.0).toInt()) { ArrayList() }.add(bp)
            }
            val sds = pts.values.filter { it.size >= 3 }.map { std0(it.toDoubleArray()) }
            if (sds.isNotEmpty()) precision = clip(100 * (1.0 - min(1.0, sds.average() / 25.0)))
        }
        return linkedMapOf("brake_aggression" to brakeAggr, "trail_braking" to trailScore, "smoothness" to smooth,
            "throttle_modulation" to modulation, "precision" to precision)
    }

    fun archetype(p: DnaTraits): String {
        val a = p["brake_aggression"] ?: 0.0; val tr = p["trail_braking"] ?: 0.0
        val sm = p["smoothness"] ?: 0.0; val mod = p["throttle_modulation"] ?: 0.0
        return when {
            tr >= 60 && a >= 55 -> "Late-braking trail braker"
            a >= 65 -> "Hard, late braker"
            sm >= 70 && mod >= 55 -> "Smooth momentum driver"
            sm < 40 -> "Aggressive, busy-handed driver"
            else -> "Balanced driver"
        }
    }

    /** One line per trait where the two differ by 10 points or more. */
    fun compare(a: DnaTraits, b: DnaTraits, nameA: String = "You", nameB: String = "Friend"): List<String> {
        val out = ArrayList<String>()
        for (k in TRAITS) {
            val x = a[k]; val y = b[k]
            if (x == null || y == null || abs(x - y) < 10) continue
            val (hi, lo) = if (x > y) nameA to nameB else nameB to nameA
            out.add("${LABELS[k]}: $hi ${fixed(max(x, y), 0)} vs $lo ${fixed(min(x, y), 0)}")
        }
        return if (out.isEmpty()) listOf("Your styles are very close.") else out
    }

    // ---- history over time --------------------------------------------------------------------------------------
    class Entry(val date: String, val traits: DnaTraits)

    fun loadHistory(file: File): List<Entry> = try {
        val a = JSONArray(file.readText())
        val out = ArrayList<Entry>()
        for (i in 0 until a.length()) {
            val o = a.optJSONObject(i) ?: continue
            val tr = o.optJSONObject("traits") ?: continue
            if (!o.has("date")) continue
            out.add(Entry(o.getString("date"), TRAITS.associateWith { k -> if (tr.isNull(k) || !tr.has(k)) null else tr.getDouble(k) }))
        }
        out
    } catch (_: Exception) { emptyList() }

    /** One entry per date (a second call for the same day replaces it); keeps the last MAX_HISTORY. */
    fun record(traits: DnaTraits, date: String, file: File): Boolean = try {
        val hist = (loadHistory(file).filter { it.date != date } + Entry(date, TRAITS.associateWith { traits[it] })).sortedBy { it.date }.takeLast(MAX_HISTORY)
        val arr = JSONArray()
        for (e in hist) arr.put(JSONObject().put("date", e.date).put("traits", JSONObject().also { o -> for (k in TRAITS) o.put(k, e.traits[k] ?: JSONObject.NULL) }))
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(arr.toString(1))
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
        true
    } catch (_: Exception) { false }

    /** Points per entry (least-squares slope) for one trait, or null with fewer than 3 entries. */
    fun trend(history: List<Entry>, trait: String): Double? {
        val ys = history.mapNotNull { it.traits[trait] }
        if (ys.size < 3) return null
        return Np.polyfit1(Np.arange(ys.size), ys.toDoubleArray()).first
    }

    /** A short string a friend can paste into TRACE to compare with you. */
    fun exportCode(traits: DnaTraits, name: String = ""): String {
        val vals = TRAITS.joinToString(",") { k -> traits[k]?.let { Math.rint(it).toLong().toString() } ?: "-" }
        val safeName = name.filter { it.isLetterOrDigit() || it in " _-" }.take(24)
        return "DNA1:$safeName:$vals"
    }

    fun importCode(code: String): Pair<String, DnaTraits>? {
        val parts = code.trim().split(":", limit = 3)
        if (parts.size != 3 || parts[0] != "DNA1") return null
        val vals = parts[2].split(",")
        if (vals.size != TRAITS.size) return null
        val traits = LinkedHashMap<String, Double?>()
        for ((k, v) in TRAITS.zip(vals)) {
            if (v == "-") traits[k] = null
            else {
                val n = v.trim().toIntOrNull() ?: return null
                if (n !in 0..100) return null
                traits[k] = n.toDouble()
            }
        }
        return (parts[1].ifEmpty { "Friend" }) to traits
    }
}
