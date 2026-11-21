package app.trace.telemetry

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.min

/**
 * coach.py: the corner-by-corner driving coach. Every corner of a lap is matched to the same corner on a reference
 * lap (Corners.matchCorners) and the biggest fixable difference at each one becomes a single plain-English tip.
 * Same thresholds, same rounding and same wording as the desktop.
 */
object Coach {
    const val BRAKE_MIN_M = 8.0
    const val THROTTLE_MIN_M = 8.0
    const val SPEED_MIN_KMH = 3.0
    const val TIME_MIN_S = 0.02
    const val LEAD_S = 3.5
    const val MIN_LEAD_M = 120.0
    const val MAX_LEAD_M = 400.0

    class Tip(
        val corner: Int, val apex: Double, val brakePos: Double?, val kind: String, val text: String, val cue: String,
        val timeS: Double, val estS: Double,
    )

    private class Cand(val est: Double, val kind: String, val text: String, val cue: String)

    private fun speedAt(d: Frame, pos: Double): Double =
        max(20.0, Np.interp(pos, d["track_position"], d["speed_kmh"])) / 3.6

    private fun candidates(c: Corners.Corner, delta: Corners.CornerDelta, d: Frame): List<Cand> {
        val n = c.number
        val out = ArrayList<Cand>()
        val b = delta.brakeM
        if (b != null) {
            if (b < -BRAKE_MIN_M) {
                val m = abs(b)
                val v = if (c.brakePos != null) speedAt(d, c.brakePos) else 50.0
                out.add(Cand(m / v * 0.5, "brake_early", "Brake ${fixed(m, 0)} m later at T$n.", "Turn $n. Brake later"))
            } else if (b > BRAKE_MIN_M && delta.minSpeedKmh < -SPEED_MIN_KMH) {
                out.add(Cand(0.1 + b / 80.0, "brake_late",
                    "You brake ${fixed(b, 0)} m later than the reference at T$n but carry less " +
                        "speed (${fixed(abs(delta.minSpeedKmh), 0)} km/h slower at the apex). Brake a touch earlier.",
                    "Turn $n. Brake earlier"))
            }
        }
        if (delta.minSpeedKmh < -SPEED_MIN_KMH && !(b != null && b > BRAKE_MIN_M)) {
            val s = abs(delta.minSpeedKmh)
            out.add(Cand(s / 50.0, "speed", "Carry ${fixed(s, 0)} km/h more through the middle of T$n.", "Turn $n. More minimum speed"))
        }
        val t = delta.throttleM
        if (t != null && t > THROTTLE_MIN_M) {
            val v = if (c.throttlePos != null) speedAt(d, c.throttlePos) else c.minSpeed / 3.6
            val sec = t / max(v, 5.0)
            out.add(Cand(sec, "throttle", "You're lifting ${fixed(sec, 1)} s early at T$n: back on the throttle ${fixed(t, 0)} m sooner.",
                "Turn $n. Throttle sooner"))
        }
        return out
    }

    /** The most useful tips for df against ref, biggest time loss first. Empty when the laps can't be read or nothing costs time. */
    fun analyse(df: Frame, ref: Frame, maxTips: Int = 3): List<Tip> {
        val m = Corners.matchCorners(df, ref)
        val d = Corners.prepare(df)
        if (d == null || m.deltas.isEmpty()) return emptyList()
        val byNum = m.a.associateBy { it.number }
        val tips = ArrayList<Tip>()
        for (delta in m.deltas) {
            val c = byNum[delta.number] ?: continue
            val ts = delta.timeS
            if (ts == null || ts < TIME_MIN_S) continue
            val cands = candidates(c, delta, d)
            if (cands.isEmpty()) continue
            var best = cands[0]
            for (x in cands) if (x.est > best.est) best = x   // first of equals wins, like Python's max()
            tips.add(Tip(c.number, c.apex, c.brakePos, best.kind, best.text, best.cue, ts, best.est))
        }
        return tips.sortedByDescending { it.timeS }.take(maxTips)
    }

    fun describe(tips: List<Tip>): String {
        if (tips.isEmpty()) return "Coach: no corner is costing you time against the reference."
        return "Coach:\n" + tips.joinToString("\n") { "  T${it.corner} (+${fixed(it.timeS, 2)}s): ${it.text}" }
    }
}

/** coach.LiveCoach: speaks a short cue shortly before each coached corner on the next lap. Pure: feed it the position each frame. */
class LiveCoach(tips: List<Coach.Tip> = emptyList()) {
    var tips: List<Coach.Tip> = tips.toList(); private set
    private val said = HashSet<Int>()
    private var lastPos: Double? = null

    fun setTips(t: List<Coach.Tip>) { tips = t.toList(); said.clear(); lastPos = null }

    /** The cue to speak now, or null. Each corner is announced once per lap; a drop in position (new lap) re-arms them. */
    fun update(trackPosition: Double, speedKmh: Double): String? {
        val lp = lastPos
        if (lp != null && trackPosition < lp - 200.0) said.clear()
        lastPos = trackPosition
        val lead = min(Coach.MAX_LEAD_M, max(Coach.MIN_LEAD_M, speedKmh / 3.6 * Coach.LEAD_S))
        for (t in tips) {
            if (t.corner in said) continue
            val at = t.brakePos ?: t.apex
            if (at - lead <= trackPosition && trackPosition < at) { said.add(t.corner); return t.cue }
        }
        return null
    }
}
