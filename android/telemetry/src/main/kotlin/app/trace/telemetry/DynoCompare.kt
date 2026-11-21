package app.trace.telemetry

import java.math.BigDecimal
import java.math.RoundingMode
import kotlin.math.max
import kotlin.math.min

/** dyno_compare.py: car-vs-car power overlay and final-drive advice, built on Dyno.analyse(). */
object DynoCompare {
    class Peak(val wkg: Double, val rpm: Double)
    class Overlay(val ok: Boolean, val msg: String, val rpm: DoubleArray, val a: DoubleArray, val b: DoubleArray, val peak: Map<String, Peak>, val text: String)

    /** Both cars' power curves resampled onto shared rpm points. */
    fun overlay(a: Dyno.Result, b: Dyno.Result, nameA: String = "A", nameB: String = "B", n: Int = 60): Overlay {
        fun fail(m: String) = Overlay(false, m, DoubleArray(0), DoubleArray(0), DoubleArray(0), emptyMap(), m)
        if (!(a.ok && b.ok)) return fail("Both cars need a dyno result (a full lap with engine data).")
        val lo = max(a.coverLo, b.coverLo)
        val hi = min(a.revLim, b.revLim)
        if (hi <= lo) return fail("The two cars' rev ranges don't overlap enough to compare.")
        val rpm = Np.linspace(lo, hi, n)
        val pa = Np.nanToNum(DoubleArray(n) { a.pAt(rpm[it]) })
        val pb = Np.nanToNum(DoubleArray(n) { b.pAt(rpm[it]) })
        val stronger = if (a.peakWkg >= b.peakWkg) nameA else nameB
        val diff = Math.abs(a.peakWkg - b.peakWkg) / max(min(a.peakWkg, b.peakWkg), 1e-6) * 100
        var area = 0.0
        for (i in 1 until n) area += ((pa[i] - pb[i]) + (pa[i - 1] - pb[i - 1])) / 2 * (rpm[i] - rpm[i - 1])
        val text = "$stronger makes ${fixed(diff, 0)}% more peak power per kg. " +
            "Across the shared rev range ${if (area >= 0) nameA else nameB} pulls harder on average."
        return Overlay(true, "", rpm, pa, pb, linkedMapOf(nameA to Peak(a.peakWkg, a.peakRpm), nameB to Peak(b.peakWkg, b.peakRpm)), text)
    }

    class Advice(val ok: Boolean, val action: String?, val text: String, val topGearKmh: Double = 0.0, val lapMaxKmh: Double = 0.0, val limiterShare: Double = 0.0)

    /** Python's round(x, nd): correctly rounded on the exact binary value, half to even. */
    private fun pyRound(x: Double, nd: Int) = BigDecimal(x).setScale(nd, RoundingMode.HALF_EVEN).toDouble()

    /** Suggest a final-drive change from where the lap's top speed lands against the limiter. */
    fun finalDriveAdvice(res: Dyno.Result, speedKmh: DoubleArray, topGearFrac: Double = 0.02): Advice {
        if (!res.ok || res.gears.isEmpty()) return Advice(false, null, "Needs a dyno result.")
        var top = res.gears[0]
        for (g in res.gears) if (g.g > top.g) top = g
        val v = speedKmh.filter { it.isFinite() }.toDoubleArray()
        if (v.size < 20) return Advice(false, null, "Needs the lap's speed trace.")
        val vmax = Np.percentile(v, 99.5)
        val gearTop = top.topKmh
        val share = v.count { it >= 0.985 * gearTop }.toDouble() / v.size
        if (share >= topGearFrac) {
            val pct = max(1.0, pyRound(share / 0.05 * 1.0, 1))
            return Advice(true, "longer", "You hit the limiter in top gear for ${fixed(share * 100, 1)}% of the lap (top ${fixed(gearTop, 0)} km/h). " +
                "Lengthen the final drive about ${fixed(min(pct, 5.0), 0)}% (lower the final ratio number).", gearTop, vmax, share)
        }
        if (vmax < 0.93 * gearTop) {
            val gap = (gearTop - vmax) / gearTop * 100
            val pct = min(Math.rint(gap * 0.7), 8.0)
            return Advice(true, "shorter", "Top gear has ${fixed(gap, 0)}% to spare (lap top speed ${fixed(vmax, 0)} km/h of ${fixed(gearTop, 0)}). " +
                "Shorten the final drive about ${fixed(pct, 0)}% for stronger acceleration.", gearTop, vmax, share)
        }
        return Advice(true, "keep", "Top-gear length is about right for this lap.", gearTop, vmax, share)
    }
}
