package app.trace.telemetry

import org.json.JSONArray
import org.json.JSONObject
import java.io.File

/**
 * planner.py: where the next tenth is, which corners to drill and a target for each. The best lap is the reference;
 * every other lap is scored against it with Corners, and the corners that cost the most and vary the most are the
 * ones worth practising.
 */
object Planner {
    const val MIN_LAPS = 3
    const val MAX_HISTORY = 100

    class Drill(val corner: Int, val apex: Double, val meanLossS: Double, val bestGainS: Double, val spreadS: Double, val target: String)
    class Plan(val ok: Boolean, val msg: String, val bestLap: Int?, val bestTimeS: Double?, val drills: List<Drill>, val totalGainS: Double, val text: String)

    /** laps[i] is the frame of the lap that took timesS[i] seconds (null laps and non-positive times are skipped). */
    fun plan(laps: List<Frame?>, timesS: List<Double>, n: Int = 3): Plan {
        val pairs = ArrayList<Pair<Frame, Double>>()
        for ((f, t) in laps.zip(timesS)) if (f != null && t > 0) pairs.add(f to t)   // NaN > 0 is false, like Python
        if (pairs.size < MIN_LAPS) {
            val m = "Needs at least $MIN_LAPS clean laps."
            return Plan(false, m, null, null, emptyList(), 0.0, m)
        }
        var bestI = 0
        for (i in pairs.indices) if (pairs[i].second < pairs[bestI].second) bestI = i
        val (ref, bestT) = pairs[bestI]
        val scored = ArrayList<Pair<Int, Map<Int, Corners.CornerDelta>>>()
        for ((i, p) in pairs.withIndex()) {
            if (i == bestI) continue
            scored.add(i to Corners.matchCorners(p.first, ref).deltas.associateBy { it.refNumber })
        }
        val spreads = LinkedHashMap<Int, Corners.CornerSpread>()
        for (s in Corners.cornerSpreads(scored, 2)) spreads[s.refNumber] = s
        val refCorners = Corners.detectCorners(ref).associateBy { it.number }
        val drills = ArrayList<Drill>()
        for ((num, s) in spreads) {
            val c = refCorners[num] ?: continue
            if (s.meanTimeS <= 0.01) continue
            val gain = s.meanTimeS
            var target = if (c.brakePos != null) "Brake at ${fixed(c.brakePos, 0)} m, " else ""
            target += "hit ${fixed(c.minSpeed, 0)} km/h at the apex"
            if (c.throttlePos != null) target += ", back on the throttle by ${fixed(c.throttlePos, 0)} m"
            drills.add(Drill(num, c.apex, s.meanTimeS, gain, s.stdTimeS, "$target."))
        }
        val top = drills.sortedByDescending { it.bestGainS + 0.5 * it.spreadS }.take(n)
        var total = 0.0
        for (d in top) total += d.bestGainS
        val text = if (top.isNotEmpty()) {
            "Next tenth is in ${top.size} corner(s), worth about ${fixed(total, 2)}s per lap:\n" +
                top.joinToString("\n") { "  T${it.corner}: costs ${fixed(it.meanLossS, 2)}s on average (±${fixed(it.spreadS, 2)}). ${it.target}" }
        } else "Your laps are consistent against your best; no single corner stands out. Push your best lap."
        return Plan(true, "", bestI, bestT, top, total, text)
    }

    // ---- progress across sessions -------------------------------------------------------------------------------
    class Session(val key: String, val date: String, val bestS: Double, val medianS: Double)

    fun loadHistory(file: File): List<Session> = try {
        val a = JSONArray(file.readText())
        (0 until a.length()).mapNotNull { i ->
            val o = a.optJSONObject(i) ?: return@mapNotNull null
            if (!o.has("key") || !o.has("best_s")) null
            else Session(o.getString("key"), o.optString("date", ""), o.getDouble("best_s"), o.optDouble("median_s", 0.0))
        }
    } catch (_: Exception) { emptyList() }

    /** key is the car + track the session was on; one entry per (key, date). */
    fun recordSession(key: String, date: String, bestS: Double, medianS: Double, file: File): Boolean = try {
        val hist = (loadHistory(file).filter { !(it.key == key && it.date == date) } + Session(key, date, bestS, medianS))
            .sortedWith(compareBy<Session>({ it.key }, { it.date })).takeLast(MAX_HISTORY)
        val arr = JSONArray()
        for (e in hist) arr.put(JSONObject().put("key", e.key).put("date", e.date).put("best_s", e.bestS).put("median_s", e.medianS))
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(arr.toString(1))
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
        true
    } catch (_: Exception) { false }

    fun progress(history: List<Session>, key: String): String {
        val h = history.filter { it.key == key }
        if (h.size < 2) return "First session on this car and track: nothing to compare with yet."
        val a = h[h.size - 2]; val b = h[h.size - 1]
        val d = b.bestS - a.bestS
        val m = b.medianS - a.medianS
        val word = if (d < 0) "faster" else "slower"
        return "Best lap ${fixed(Math.abs(d), 2)}s $word than last session (${a.date}); typical lap ${signed(m, 2)}s. ${h.size} sessions logged."
    }
}
