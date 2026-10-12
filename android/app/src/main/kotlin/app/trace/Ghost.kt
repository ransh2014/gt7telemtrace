package app.trace

import app.trace.telemetry.Frame

/** ghost.py: a leaderboard lap's position -> time curve, for the live "VS TOP" gap. */
class Ghost(private val pos: DoubleArray, private val times: DoubleArray) {
    fun timeAt(p: Double): Double? {
        if (p < pos[0] || p > pos[pos.size - 1]) return null
        var lo = 0; var hi = pos.size - 1
        while (hi - lo > 1) { val m = (lo + hi) / 2; if (pos[m] <= p) lo = m else hi = m }
        val span = pos[hi] - pos[lo]
        return if (span <= 0) times[lo] else times[lo] + (times[hi] - times[lo]) * (p - pos[lo]) / span
    }

    /** Seconds you are ahead (-) or behind (+) the ghost at this spot. */
    fun gap(p: Double, elapsed: Double): Double? = timeAt(p)?.let { elapsed - it }

    companion object {
        fun build(df: Frame, lapMs: Int): Ghost? {
            val lapS = lapMs / 1000.0
            if (!(lapS > 1.0) || !df.has("track_position") || !df.has("speed_kmh")) return null
            val tp = df["track_position"]; val sp = df["speed_kmh"]
            val pts = (0 until df.n).filter { tp[it].isFinite() && sp[it].isFinite() }.map { tp[it] to sp[it] }.sortedBy { it.first }
            if (pts.size < 20) return null
            val pp = ArrayList<Double>(); val ss = ArrayList<Double>()
            for ((i, p) in pts.withIndex()) if (i == 0 || p.first > pts[i - 1].first) { pp.add(p.first); ss.add(p.second / 3.6) }
            if (pp.size < 20 || pp.last() - pp.first() < 100.0) return null
            val t = DoubleArray(pp.size)
            for (i in 1 until pp.size) t[i] = t[i - 1] + (pp[i] - pp[i - 1]) / maxOf((ss[i] + ss[i - 1]) / 2, 1.0)
            if (t.last() <= 0) return null
            val k = lapS / t.last()
            return Ghost(pp.toDoubleArray(), DoubleArray(t.size) { t[it] * k })
        }
    }
}
