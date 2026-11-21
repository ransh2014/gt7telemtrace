package app.trace.telemetry

import kotlin.math.abs
import kotlin.math.max
import kotlin.math.sign
import kotlin.math.tan

/**
 * setup_advisor.py: finds understeer, oversteer, wheelspin, front lock-ups and bottoming-out in one lap and suggests
 * setup changes, plus an A/B check. Balance needs the front wheels' steering angle and the wheelbase; without them it
 * says so rather than guessing. Every suggestion is a direction and size to try, not a guaranteed fix.
 *
 * Balance reads the mean of the two front wheels' steering angle: saved laps carry `wheel_steering_angle_l` / `_r`,
 * and a frame may also carry one combined `wheel_steering_angle` column (see wheelAngle; same rule as the desktop).
 */
object SetupAdvisor {
    const val MIN_SPEED_KMH = 40.0
    const val MIN_DEMAND = 0.01
    const val UNDER_RATIO = 0.75
    const val OVER_RATIO = 1.25
    const val SEVERE = 0.18
    const val MILD = 0.07
    const val SPIN = 1.12
    const val LOCK = 0.80
    const val BOTTOM_MM = 5.0
    const val MIN_ROWS = 120

    class Finding(val key: String, val phase: String, val share: Double, val severity: String, val text: String, val fixes: List<String>)
    class Balance(val under: Double, val over: Double, val n: Int)
    class Diagnosis(val ok: Boolean, val msg: String, val balance: Map<String, Balance>, val findings: List<Finding>)

    private fun col(df: Frame, name: String, def: Double = 0.0): DoubleArray =
        if (df.has(name)) df[name].let { a -> DoubleArray(a.size) { if (a[it].isNaN()) def else a[it] } } else DoubleArray(df.n) { def }

    /** Mean front road-wheel angle (rad) per row, or null if the lap has no such data. */
    private fun wheelAngle(df: Frame): DoubleArray? {
        if (!df.has("wheel_steering_angle")) {
            if (!df.has("wheel_steering_angle_l") || !df.has("wheel_steering_angle_r")) return null
            val l = df["wheel_steering_angle_l"]; val r = df["wheel_steering_angle_r"]
            val m = DoubleArray(l.size) { (l[it] + r[it]) / 2.0 }
            return if (m.isEmpty() || m.count { it.isNaN() }.toDouble() / m.size > 0.5) null else Np.nanToNum(m)
        }
        val a = df["wheel_steering_angle"]
        if (a.isEmpty() || a.count { it.isNaN() }.toDouble() / a.size > 0.5) return null
        return Np.nanToNum(a)
    }

    private fun wheelbase(df: Frame): Double? {
        if (!df.has("wheel_base")) return null
        val w = df["wheel_base"].filter { it.isFinite() && it > 1.0 }.toDoubleArray()
        return if (w.isNotEmpty()) Np.median(w) else null
    }

    private fun sev(share: Double) = if (share >= SEVERE) "strong" else "mild"

    private fun mean(mask: BooleanArray, pred: (Int) -> Boolean): Double {
        var n = 0; var c = 0
        for (i in mask.indices) if (mask[i]) { n++; if (pred(i)) c++ }
        return if (n == 0) Double.NaN else c.toDouble() / n
    }

    fun diagnose(df: Frame?): Diagnosis {
        if (df == null || df.n < MIN_ROWS || !df.has("speed_kmh")) return Diagnosis(false, "Needs a full recorded lap.", emptyMap(), emptyList())
        val n = df.n
        val v = col(df, "speed_kmh").let { s -> DoubleArray(n) { s[it] / 3.6 } }
        val thr = col(df, "throttle"); val brk = col(df, "brake"); val yaw = col(df, "ang_y")
        val findings = ArrayList<Finding>()
        val balance = LinkedHashMap<String, Balance>()
        var msg = ""

        val ang = wheelAngle(df); val wb = wheelbase(df)
        if (ang == null || wb == null) {
            msg = "No steering-angle / wheelbase data in this lap, so understeer and oversteer can't be measured."
        } else {
            val expect = DoubleArray(n) { v[it] * tan(ang[it]) / wb }
            val cornering = BooleanArray(n) { v[it] * 3.6 > MIN_SPEED_KMH && abs(expect[it]) > MIN_DEMAND }
            var ratio = DoubleArray(n) { if (cornering[it]) yaw[it] * sign(expect[it]) / max(abs(expect[it]), 1e-6) else 1.0 }
            // GT7's yaw sign can differ from the wheel sign; pick the orientation that agrees on average
            if (cornering.any { it } && Np.median(ratio.filterIndexed { i, _ -> cornering[i] }.toDoubleArray()) < 0) ratio = DoubleArray(n) { -ratio[it] }
            val phases = listOf(
                "entry" to BooleanArray(n) { cornering[it] && brk[it] > 0.1 },
                "mid" to BooleanArray(n) { cornering[it] && brk[it] <= 0.1 && thr[it] <= 0.5 },
                "exit" to BooleanArray(n) { cornering[it] && brk[it] <= 0.1 && thr[it] > 0.5 },
            )
            for ((ph, m) in phases) {
                val cnt = m.count { it }
                if (cnt < 15) { balance[ph] = Balance(0.0, 0.0, cnt); continue }
                val under = mean(m) { ratio[it] < UNDER_RATIO }
                val over = mean(m) { ratio[it] > OVER_RATIO }
                balance[ph] = Balance(under, over, cnt)
                if (under >= MILD && under >= over) findings.add(underFinding(ph, under))
                else if (over >= MILD) findings.add(overFinding(ph, over))
            }
        }

        val slipFl = col(df, "tyre_slip_fl", 1.0); val slipFr = col(df, "tyre_slip_fr", 1.0)
        val slipRl = col(df, "tyre_slip_rl", 1.0); val slipRr = col(df, "tyre_slip_rr", 1.0)
        val moving = BooleanArray(n) { v[it] * 3.6 > 30 }
        val acc = BooleanArray(n) { moving[it] && thr[it] > 0.6 && brk[it] < 0.05 }
        if (acc.count { it } >= 20) {
            val share = mean(acc) { max(slipRl[it], slipRr[it]) > SPIN }
            if (share >= MILD) findings.add(Finding("wheelspin", "exit", share, sev(share),
                "The rear wheels spin on ${fixed(share * 100, 0)}% of your full-throttle driving.",
                listOf("LSD accel −5", "Traction control +1", "Rear ARB −2", "Rear spring rate −0.3")))
        }
        val braking = BooleanArray(n) { moving[it] && brk[it] > 0.3 }
        if (braking.count { it } >= 20) {
            val share = mean(braking) { kotlin.math.min(slipFl[it], slipFr[it]) < LOCK }
            if (share >= MILD) findings.add(Finding("lockup", "entry", share, sev(share),
                "The front wheels lock on ${fixed(share * 100, 0)}% of your hard braking.",
                listOf("Brake balance −1 (towards the rear)", "ABS +1", "Front brake-pad/toe: front toe 0.0")))
        }

        if (df.has("ride_height_mm")) {
            val rh = col(df, "ride_height_mm", 999.0)
            val share = if (moving.any { it }) mean(moving) { rh[it] < BOTTOM_MM } else 0.0
            if (share >= 0.03) findings.add(Finding("bottoming", "all", share, if (share >= 0.10) "strong" else "mild",
                "The car touches the ground on ${fixed(share * 100, 0)}% of the lap.",
                listOf("Ride height +5 mm (front and rear)", "Spring rate +0.5", "Damper compression +2")))
        }
        return Diagnosis(true, msg, balance, findings.sortedByDescending { it.share })
    }

    private fun underFinding(ph: String, share: Double): Finding {
        val fixes = when (ph) {
            "entry" -> listOf("Front ARB −2", "Rear ARB +1", "Front camber +0.3", "LSD decel −3")
            "mid" -> listOf("Front ARB −2", "Front camber +0.3", "Front toe −0.1 (more toe-out)", "Rear ARB +2")
            else -> listOf("LSD accel −5", "Rear ARB +1", "Front spring rate −0.3")
        }
        val where = when (ph) { "entry" -> "turning in"; "mid" -> "mid-corner"; else -> "on the power out of corners" }
        return Finding("under_$ph", ph, share, sev(share), "Understeer $where: the car turns less than you steer on ${fixed(share * 100, 0)}% of that phase.", fixes)
    }

    private fun overFinding(ph: String, share: Double): Finding {
        val fixes = when (ph) {
            "entry" -> listOf("LSD decel −5", "Brake balance +1 (towards the front)", "Rear ARB −1", "Rear toe +0.1")
            "mid" -> listOf("Rear ARB −2", "Rear camber −0.3", "Rear toe +0.1")
            else -> listOf("LSD accel −5", "Rear ARB −2", "Rear toe +0.1", "Traction control +1")
        }
        val where = when (ph) { "entry" -> "turning in"; "mid" -> "mid-corner"; else -> "on the power out of corners" }
        return Finding("over_$ph", ph, share, sev(share), "Oversteer $where: the rear steps out on ${fixed(share * 100, 0)}% of that phase.", fixes)
    }

    fun describe(res: Diagnosis): String {
        if (!res.ok) return res.msg.ifEmpty { "No diagnosis." }
        val lines = ArrayList<String>()
        if (res.msg.isNotEmpty()) lines.add(res.msg)
        if (res.findings.isEmpty()) lines.add("Setup looks balanced: nothing worth changing was found in this lap.")
        for (f in res.findings) lines.add("[${f.severity}] ${f.text}  Try: " + f.fixes.take(3).joinToString("; ") + ".")
        return lines.joinToString("\n")
    }

    // ---- A/B ------------------------------------------------------------------------------------------------------
    class Change(val key: String, val before: Double, val after: Double)
    class Ab(val verdict: String, val better: List<Change>, val worse: List<Change>, val lapDeltaS: Double?, val text: String)

    private fun score(r: Diagnosis): Map<String, Double> = r.findings.associate { it.key to it.share }

    /** Did the change help? lapsA are frames before the change, lapsB after (about three laps each). */
    fun compareAb(lapsA: List<Frame?>, lapsB: List<Frame?>, timesA: List<Double>? = null, timesB: List<Double>? = null): Ab {
        val ra = lapsA.filterNotNull().map { diagnose(it) }.filter { it.ok }
        val rb = lapsB.filterNotNull().map { diagnose(it) }.filter { it.ok }
        if (ra.size < 2 || rb.size < 2) return Ab("unclear", emptyList(), emptyList(), null, "Need at least two good laps before and two after the change.")
        val keys = (ra + rb).flatMap { score(it).keys }.toSortedSet()
        val better = ArrayList<Change>(); val worse = ArrayList<Change>()
        for (k in keys) {
            val a = Np.mean(DoubleArray(ra.size) { score(ra[it])[k] ?: 0.0 })
            val b = Np.mean(DoubleArray(rb.size) { score(rb[it])[k] ?: 0.0 })
            if (b < a - 0.03) better.add(Change(k, a, b)) else if (b > a + 0.03) worse.add(Change(k, a, b))
        }
        val ta = (timesA ?: emptyList()).filter { it != 0.0 && it > 0 }
        val tb = (timesB ?: emptyList()).filter { it != 0.0 && it > 0 }
        val d: Double? = if (ta.size >= 2 && tb.size >= 2) Np.median(tb.toDoubleArray()) - Np.median(ta.toDoubleArray()) else null
        val gain = better.size - worse.size
        var verdict = "unclear"
        if (gain > 0 && (d == null || d <= 0.05)) verdict = "better"
        else if (gain < 0 && (d == null || d >= -0.05)) verdict = "worse"
        else if (d != null && abs(d) >= 0.15) verdict = if (d < 0) "better" else "worse"
        fun names(xs: List<Change>) = xs.joinToString(", ") { it.key.replace("_", " ") }.ifEmpty { "none" }
        val lap = if (d == null) "" else " Median lap time changed by ${signed(d, 2)}s."
        return Ab(verdict, better, worse, d, "Change looks $verdict. Improved: ${names(better)}. Got worse: ${names(worse)}.$lap")
    }
}
