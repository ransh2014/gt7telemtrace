package app.trace.telemetry

import org.json.JSONObject
import java.io.File
import kotlin.math.*

/**
 * dyno.py: the engine's power curve, the gearbox and tuning hints, all rebuilt from one lap.
 *
 * GT7 never shows the real output curve or the gear spacing, but the telemetry holds everything needed:
 * how hard the car pulls at full throttle at every rpm, in every gear. Specific wheel power is
 *
 *     P/m = v * (a + g*sin(slope) + c0 + c1*v^2)
 *
 * (acceleration, hill, rolling resistance c0, aero drag c1), taken only from full-throttle, no-wheelspin,
 * mostly-straight samples and binned by rpm. The drag terms are fitted from the lap's own coast-down, so
 * absolute numbers are an estimate (about +/-10%); the curve SHAPE, the peak rpm and the shift points are
 * much more reliable. Same maths as the PC app, same results.
 */
object Dyno {
    const val G = 9.81
    const val HP_W = 745.7
    private val REQ = listOf("t", "speed_kmh", "rpm", "gear", "throttle", "brake")
    private const val C0_DEFAULT = 0.12
    private const val C1_DEFAULT = 2.8e-4

    class Resist(val c0: Double, val c1: Double, val src: String)
    class Top(val kmh: Double, val by: String, val gear: Int)

    class Gear(
        val g: Int, val ratio: Double?, val k: Double, val src: String, val kmhPer1000: Double, val topKmh: Double,
        val share: Double, val optShift: Double?, val landRpm: Double?, val yourShift: Double?, val finalDrive: Double?,
    )

    class Hint(val sev: Int, val area: String, val finding: String, val fix: String)

    class Result {
        var ok = false
        var msg = ""
        var resist = Resist(C0_DEFAULT, C1_DEFAULT, "assumed")
        var nSamples = 0
        var revLim = 0.0
        var rpm = DoubleArray(0)
        var pWkg = DoubleArray(0)
        var q1 = DoubleArray(0)
        var q3 = DoubleArray(0)
        var tqNmkg = DoubleArray(0)
        var boost = DoubleArray(0)
        var peakRpm = 0.0
        var peakWkg = 0.0
        var peakTqRpm = 0.0
        var peakTqNmkg = 0.0
        var coverLo = 0.0
        var coverHi = 0.0
        var hpPerTonne = 0.0
        var mass: Double? = null
        var unitP = "hp/t"
        var unitT = "Nm/t"
        var pDisp = DoubleArray(0)
        var q1Disp = DoubleArray(0)
        var q3Disp = DoubleArray(0)
        var tDisp = DoubleArray(0)
        var peakPDisp = 0.0
        var peakTDisp = 0.0
        var nGears = 0
        var gears: List<Gear> = emptyList()
        /** Thrust (in g) against speed (km/h) for each gear, keyed by gear number. */
        var curves: Map<Int, Pair<DoubleArray, DoubleArray>> = emptyMap()
        var top: Top? = null
        /** Power (W/kg) at an rpm; NaN outside the rpm range the lap covered. */
        var pAt: (Double) -> Double = { Double.NaN }
        /** Same, holding the end values outside the range (for extrapolating top speed). */
        var pEdge: (Double) -> Double = { Double.NaN }
    }

    // ── small helpers (numpy look-alikes) ────────────────────────────────────
    private fun col(d: Frame, name: String, def: Double = 0.0): DoubleArray {
        val a = d.cols[name] ?: return DoubleArray(d.n) { def }
        return DoubleArray(a.size) { val x = a[it]; if (x.isFinite()) x else def }
    }

    /** Moving average of width n with edge padding (dyno._smooth). */
    private fun smooth(x: DoubleArray, n: Int): DoubleArray {
        if (n < 3 || x.size < n) return x.copyOf()
        val pad = n / 2
        val m = x.size
        return DoubleArray(m) { i ->
            var s = 0.0
            for (k in 0 until n) s += x[(i - pad + k).coerceIn(0, m - 1)]
            s / n
        }
    }

    private fun nearShift(gear: IntArray, pad: Int): BooleanArray {
        val n = gear.size
        val pre = IntArray(n + 1)
        for (i in 0 until n) pre[i + 1] = pre[i] + (if (i >= 1 && gear[i] != gear[i - 1]) 1 else 0)
        return BooleanArray(n) { i -> pre[min(n, i + pad + 1)] - pre[max(0, i - pad)] > 0 }
    }

    /** np.gradient(f, x) for uneven spacing. */
    private fun gradient(f: DoubleArray, x: DoubleArray): DoubleArray {
        val n = f.size
        val o = DoubleArray(n)
        if (n < 2) return o
        o[0] = (f[1] - f[0]) / (x[1] - x[0])
        o[n - 1] = (f[n - 1] - f[n - 2]) / (x[n - 1] - x[n - 2])
        for (i in 1 until n - 1) {
            val dx1 = x[i] - x[i - 1]; val dx2 = x[i + 1] - x[i]
            val a = -dx2 / (dx1 * (dx1 + dx2))
            val b = (dx2 - dx1) / (dx1 * dx2)
            val c = dx1 / (dx2 * (dx1 + dx2))
            o[i] = a * f[i - 1] + b * f[i] + c * f[i + 1]
        }
        return o
    }

    private fun pick(a: DoubleArray, m: BooleanArray): DoubleArray {
        var c = 0
        for (b in m) if (b) c++
        val o = DoubleArray(c)
        var j = 0
        for (i in a.indices) if (m[i]) o[j++] = a[i]
        return o
    }

    private fun count(m: BooleanArray): Int { var c = 0; for (b in m) if (b) c++; return c }
    private fun mean(m: BooleanArray): Double = if (m.isEmpty()) Double.NaN else count(m).toDouble() / m.size

    private fun pearson(x: DoubleArray, y: DoubleArray): Double {
        val mx = Np.mean(x); val my = Np.mean(y)
        var sxy = 0.0; var sxx = 0.0; var syy = 0.0
        for (i in x.indices) { val dx = x[i] - mx; val dy = y[i] - my; sxy += dx * dy; sxx += dx * dx; syy += dy * dy }
        return sxy / sqrt(sxx * syy)
    }

    private fun arangeCount(start: Double, stop: Double, step: Double): Int =
        max(0, ceil((stop - start) / step).toInt())

    // ── remembered car weights (asked once per car) ──────────────────────────
    fun loadWeight(file: File, car: String): Double? = try {
        val kg = JSONObject(file.readText()).optDouble(car.trim().lowercase(), Double.NaN)
        if (kg.isFinite() && kg in 300.0..5000.0) kg else null
    } catch (e: Exception) { null }

    fun saveWeight(file: File, car: String, kg: Double): Boolean = try {
        if (!(kg in 300.0..5000.0)) false
        else {
            val j = try { JSONObject(file.readText()) } catch (e: Exception) { JSONObject() }
            j.put(car.trim().lowercase(), kg)
            file.parentFile?.mkdirs()
            val tmp = File(file.parentFile, file.name + ".tmp")
            tmp.writeText(j.toString())
            if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
            true
        }
    } catch (e: Exception) { false }

    // ── the dyno ─────────────────────────────────────────────────────────────
    /** Rebuild the car's power curve and gearbox from a lap. [Result.ok] is false (with [Result.msg]) when the lap can't support it. */
    fun analyse(df: Frame?, gearRatios: List<Double>? = null, massKg: Double? = null, tyreRadiusM: Double? = null): Result {
        val res = Result()
        if (df == null || df.n < 120 || REQ.any { !df.has(it) }) {
            res.msg = "Needs a full lap with engine data (not a leaderboard ghost)."
            return res
        }
        val n = df.n
        val t = Np.cummax(col(df, "t"))
        for (i in 0 until n) t[i] += i * 1e-6
        val dAll = DoubleArray(n - 1) { t[it + 1] - t[it] }.filter { it > 1e-4 }.toDoubleArray()
        if (dAll.size < 20) { res.msg = "This lap has no usable timestamps."; return res }
        val dt = Np.median(dAll)
        var w = max(3, Math.rint(0.2 / dt).toInt())
        if (w % 2 == 0) w += 1

        val spd = col(df, "speed_kmh")
        val v = smooth(DoubleArray(n) { spd[it] / 3.6 }, w)
        val a = gradient(v, t)
        val rpmRaw = col(df, "rpm")
        val rpm = smooth(rpmRaw, w)
        val gArr = col(df, "gear")
        val gear = IntArray(n) { Math.rint(gArr[it]).toInt() }
        val thr = col(df, "throttle"); val brk = col(df, "brake")
        val angY = col(df, "ang_y")
        val ay = DoubleArray(n) { abs(angY[it]) * v[it] }
        val vx = col(df, "vel_x"); val vy = col(df, "vel_y"); val vz = col(df, "vel_z")
        val sinRaw = DoubleArray(n) {
            val sp = sqrt(vx[it] * vx[it] + vy[it] * vy[it] + vz[it] * vz[it])
            if (sp > 3.0) vy[it] / max(sp, 1e-6) else 0.0
        }
        val sinT = Np.clip(smooth(sinRaw, (w * 3) or 1), -0.3, 0.3)

        val slip = listOf("fl", "fr", "rl", "rr").map { col(df, "tyre_slip_$it") }
        var movingN = 0; var slipHigh = 0
        for (i in 0 until n) if (v[i] > 5) { movingN++; for (s in slip) if (s[i] > 0.2) slipHigh++ }
        val haveSlip = movingN > 0 && slipHigh.toDouble() / (4.0 * movingN) > 0.5
        val slipMax = DoubleArray(n) { i -> if (haveSlip) slip.maxOf { it[i] } else 1.0 }
        val slipMin = DoubleArray(n) { i -> if (haveSlip) slip.minOf { it[i] } else 1.0 }

        val pad = max(3, Math.rint(0.25 / dt).toInt())
        val near = nearShift(gear, pad)

        // rolling resistance + aero drag from the coast-down
        val coast = BooleanArray(n) { thr[it] < 0.03 && brk[it] < 0.03 && v[it] > 12 && ay[it] < 1.5 && gear[it] >= 1 && !near[it] }
        var c0 = C0_DEFAULT; var c1 = C1_DEFAULT; var src = "assumed"
        if (count(coast) >= 60) {
            val vc = pick(v, coast)
            if (Np.percentile(vc, 95.0) - Np.percentile(vc, 5.0) >= 10) {
                val y = pick(DoubleArray(n) { -(a[it] + G * sinT[it]) }, coast)
                val x2 = DoubleArray(vc.size) { vc[it] * vc[it] }
                val (f1, f0) = Np.polyfit1(x2, y)
                if (f0 in 0.02..0.4 && f1 in 5e-5..8e-4) { c0 = f0; c1 = f1; src = "fitted" }
            }
        }
        res.resist = Resist(c0, c1, src)

        // full-throttle, grip-limited-by-engine samples
        val fSpec = DoubleArray(n) { a[it] + G * sinT[it] + c0 + c1 * v[it] * v[it] }
        val pSpec = DoubleArray(n) { fSpec[it] * v[it] }
        val m = BooleanArray(n) {
            thr[it] > 0.97 && brk[it] < 0.02 && gear[it] >= 1 && !near[it] && v[it] > 3.0 && ay[it] < 3.0 &&
                slipMax[it] < 1.08 && rpm[it] > 1000 && fSpec[it] > 0 && pSpec[it] > 0 && pSpec[it] < 2500
        }
        res.nSamples = count(m)
        if (res.nSamples < 80) {
            res.msg = "Not enough clean full-throttle driving in this lap (${res.nSamples} samples). Use a lap with a few long straights."
            return res
        }

        val obsMax = Np.percentile(pick(rpm, BooleanArray(n) { rpm[it] > 0 }), 99.8)
        val cands = arrayListOf(obsMax)
        for (cn in listOf("rpm_limiter", "max_rpm")) {
            val c = col(df, cn).filter { it > 1000 }
            if (c.isNotEmpty()) cands.add(Np.median(c.toDoubleArray()))
        }
        val revLim = min(cands.maxOrNull()!!, obsMax * 1.25)
        res.revLim = revLim

        val bw = max(50.0, Math.rint(revLim / 70.0 / 50.0) * 50.0)
        val rpmM = pick(rpm, m)
        val lo = floor(Np.percentile(rpmM, 1.0) / bw) * bw
        val hi = Np.percentile(rpmM, 99.5) + bw
        // Overlapping rpm windows (+/-1.5 bins), so the thinly covered launch region still gets a value.
        val pm = pick(pSpec, m)
        val boostM = pick(col(df, "boost"), m)
        val centers = ArrayList<Double>(); val q1l = ArrayList<Double>(); val q3l = ArrayList<Double>()
        val bo = ArrayList<Double>(); val bst = ArrayList<Double>()
        for (ci in 0 until arangeCount(lo, hi + bw, bw)) {
            val c = lo + ci * bw
            val sel = BooleanArray(rpmM.size) { abs(rpmM[it] - c) <= 1.5 * bw }
            if (count(sel) < 6) continue
            val ps = pick(pm, sel)
            centers.add(c)
            q1l.add(Np.percentile(ps, 25.0)); q3l.add(Np.percentile(ps, 75.0))
            bo.add(Np.median(ps)); bst.add(Np.median(pick(boostM, sel)))
        }
        if (centers.size < 8) { res.msg = "The lap doesn't sweep enough of the rev range to draw a curve."; return res }
        val cen = centers.toDoubleArray()
        val med = smooth(bo.toDoubleArray(), 3)
        val q1 = smooth(q1l.toDoubleArray(), 3); val q3 = smooth(q3l.toDoubleArray(), 3)
        val pEdge = { r: Double -> Np.interp(r, cen, med) }
        val pAt = { r: Double -> if (r.isNaN() || r < cen[0] || r > cen[cen.size - 1]) Double.NaN else Np.interp(r, cen, med) }

        var ipk = 0
        for (i in med.indices) if (med[i] > med[ipk]) ipk = i
        val torque = DoubleArray(cen.size) { med[it] / (2 * PI * cen[it] / 60.0) }
        var itq = 0
        for (i in torque.indices) if (torque[i] > torque[itq]) itq = i
        res.rpm = cen; res.pWkg = med; res.q1 = q1; res.q3 = q3; res.tqNmkg = torque; res.boost = bst.toDoubleArray()
        res.peakRpm = cen[ipk]; res.peakWkg = med[ipk]; res.peakTqRpm = cen[itq]; res.peakTqNmkg = torque[itq]
        res.coverLo = cen[0]; res.coverHi = cen[cen.size - 1]
        res.pAt = pAt; res.pEdge = pEdge
        res.hpPerTonne = med[ipk] * 1000.0 / HP_W
        val mass = if (massKg != null && massKg in 300.0..5000.0) massKg else null
        res.mass = mass
        val kP = (mass ?: 1000.0) / HP_W          // W/kg -> hp (or hp per tonne)
        val kT = mass ?: 1000.0                   // Nm/kg -> Nm (or Nm per tonne)
        res.unitP = if (mass != null) "hp" else "hp/t"
        res.unitT = if (mass != null) "Nm" else "Nm/t"
        res.pDisp = DoubleArray(med.size) { med[it] * kP }
        res.q1Disp = DoubleArray(q1.size) { q1[it] * kP }
        res.q3Disp = DoubleArray(q3.size) { q3[it] * kP }
        res.tDisp = DoubleArray(torque.size) { torque[it] * kT }
        res.peakPDisp = med[ipk] * kP
        res.peakTDisp = torque[itq] * kT

        // gearbox: measured rpm per m/s in each gear
        val ratios = (gearRatios ?: emptyList()).filter { it.isFinite() }
        val ks = LinkedHashMap<Int, Double>(); val kn = LinkedHashMap<Int, Int>()
        val okK = BooleanArray(n) { !near[it] && v[it] > 3 && slipMax[it] < 1.05 && slipMin[it] > 0.95 && rpm[it] > 1000 }
        for (g in 1..8) {
            val s = BooleanArray(n) { okK[it] && gear[it] == g }
            val c = count(s)
            if (c >= 15) {
                ks[g] = Np.median(DoubleArray(n) { rpm[it] / v[it] }.let { pick(it, s) })
                kn[g] = c
            }
        }
        val srcK = HashMap<Int, String>()
        for (g in ks.keys) srcK[g] = "measured"
        if (ks.isNotEmpty() && ratios.isNotEmpty()) {
            val gref = kn.maxByOrNull { it.value }!!.key
            if (gref <= ratios.size && ratios[gref - 1] > 0) {
                for (g in 1..min(ratios.size, 8)) {
                    if (g !in ks && ratios[g - 1] > 0) {
                        ks[g] = ks[gref]!! * ratios[g - 1] / ratios[gref - 1]
                        srcK[g] = "from ratio"
                    }
                }
            }
        }
        if (ks.isEmpty()) { res.msg = "Couldn't pin down any gear (too little steady driving in each)."; return res }

        val nPos = if (ratios.isEmpty()) 0 else ratios.count { it > 0 }
        res.nGears = max(nPos, ks.keys.maxOrNull()!!)
        val gs = ks.keys.sorted()
        // optimal shift points: where the next gear starts pulling harder
        val opt = HashMap<Int, Double>(); val land = HashMap<Int, Double>()
        for (g in gs) {
            if ((g + 1) !in ks) continue
            val rho = ks[g + 1]!! / ks[g]!!
            if (!(rho > 0.4 && rho < 0.98)) continue
            val step = max(25.0, bw / 4.0)
            val cnt = arangeCount(res.peakRpm, revLim, step)
            if (cnt < 2) continue
            var any = false; var firstBetter = -1
            for (j in 0 until cnt) {
                val r = res.peakRpm + j * step
                val pa = pAt(r); val pb = pAt(r * rho)
                if (pa.isFinite() && pb.isFinite()) {
                    any = true
                    if (pb >= pa && firstBetter < 0) firstBetter = j
                }
            }
            if (!any) continue
            val rOpt = if (firstBetter >= 0) res.peakRpm + firstBetter * step else revLim
            opt[g] = rOpt; land[g] = rOpt * rho
        }
        // the shifts you actually made
        val mine = HashMap<Int, ArrayList<Double>>()
        for (i in 1 until n) {
            if (gear[i] == gear[i - 1] + 1 && gear[i - 1] >= 1 && thr[max(0, i - 4)] > 0.9) mine.getOrPut(gear[i - 1]) { ArrayList() }.add(rpmRaw[i - 1])
        }
        val gears = ArrayList<Gear>()
        for (g in gs) {
            val ratio = if (g <= ratios.size && ratios[g - 1] > 0) ratios[g - 1] else null
            val kg = ks[g]!!
            val fd = if (tyreRadiusM != null && tyreRadiusM != 0.0 && ratio != null) kg * 2 * PI * tyreRadiusM / 60.0 / ratio else null
            gears.add(Gear(
                g, ratio, kg, srcK[g]!!, 3.6 * 1000.0 / kg, revLim / kg * 3.6,
                BooleanArray(n) { gear[it] == g }.let { mean(it) }, opt[g], land[g],
                mine[g]?.takeIf { it.isNotEmpty() }?.let { Np.median(it.toDoubleArray()) }, fd))
        }
        res.gears = gears

        // thrust (in g) against speed for each gear
        val curves = LinkedHashMap<Int, Pair<DoubleArray, DoubleArray>>()
        for (g in gs) {
            val vv = Np.linspace(max(2.0, cen[0] / ks[g]!!), revLim / ks[g]!!, 80)
            curves[g] = DoubleArray(vv.size) { vv[it] * 3.6 } to DoubleArray(vv.size) { pEdge(ks[g]!! * vv[it]) / vv[it] / G }
        }
        res.curves = curves

        // true top speed: rev limiter in the tallest gear, or where drag wins first
        val gt = gs.last()
        val vv = Np.linspace(2.0, revLim / ks[gt]!!, 500)
        var lastPos = -1
        for (i in vv.indices) if (pEdge(ks[gt]!! * vv[i]) / vv[i] - (c0 + c1 * vv[i] * vv[i]) > 0) lastPos = i
        res.top = if (lastPos >= 0 && lastPos < vv.size - 2) Top(vv[lastPos] * 3.6, "drag", gt) else Top(vv[vv.size - 1] * 3.6, "rev limiter", gt)
        res.ok = true
        return res
    }

    // ── tuning hints ─────────────────────────────────────────────────────────
    /** Understeer ratio per corner phase (steering used / steering a neutral car needs); ~1 neutral. Null when unreadable. */
    private fun balance(df: Frame, v: DoubleArray): Map<String, Double>? {
        val n = df.n
        val wb = col(df, "wheel_base").filter { it > 0.5 }
        if (wb.isEmpty() || !df.has("wheel_steering_angle_l")) return null
        val L = Np.median(wb.toDoubleArray())
        val sl = col(df, "wheel_steering_angle_l"); val sr = col(df, "wheel_steering_angle_r")
        var d = DoubleArray(n) { (sl[it] + sr[it]) / 2.0 }
        if (Np.percentile(DoubleArray(n) { abs(d[it]) }, 99.0) > 1.6) d = DoubleArray(n) { Math.toRadians(d[it]) }   // degrees, not radians
        val yaw = col(df, "ang_y")
        val ok = BooleanArray(n) { v[it] > 12 && abs(yaw[it]) * v[it] > 2.0 && abs(d[it]) > 0.01 }
        if (count(ok) < 60) return null
        val dn = DoubleArray(n) { L * yaw[it] / max(v[it], 1.0) }
        if (pearson(pick(d, ok), pick(dn, ok)) < 0) d = DoubleArray(n) { -d[it] }
        val ratio = DoubleArray(n) { (d[it] / (if (abs(dn[it]) < 1e-6) 1e-6 else dn[it])).coerceIn(-3.0, 5.0) }
        val thr = col(df, "throttle"); val brk = col(df, "brake")
        val phases = linkedMapOf(
            "entry" to BooleanArray(n) { ok[it] && brk[it] > 0.1 },
            "mid" to BooleanArray(n) { ok[it] && thr[it] <= 0.5 && brk[it] <= 0.1 },
            "exit" to BooleanArray(n) { ok[it] && thr[it] > 0.5 && brk[it] < 0.05 },
            "fast" to BooleanArray(n) { ok[it] && v[it] > 50 },
        )
        val out = LinkedHashMap<String, Double>()
        for ((k, msk) in phases) if (count(msk) >= 25) out[k] = Np.median(pick(ratio, msk))
        return out.ifEmpty { null }
    }

    /** Plain-English tuning hints from the lap, each tied to a measured signal. They give a DIRECTION to try, never a number. */
    fun advise(df: Frame?, res: Result?, gearRatios: List<Double>? = null): List<Hint> {
        val hints = ArrayList<Hint>()
        fun add(sev: Int, area: String, finding: String, fix: String) { hints.add(Hint(sev, area, finding, fix)) }
        if (df == null || df.n < 120) return hints
        val n = df.n
        val spd = col(df, "speed_kmh")
        val v = DoubleArray(n) { spd[it] / 3.6 }
        val thr = col(df, "throttle"); val brk = col(df, "brake")
        val rpm = col(df, "rpm")
        val gArr = col(df, "gear")
        val gear = IntArray(n) { Math.rint(gArr[it]).toInt() }
        val t = col(df, "t")
        val dts = DoubleArray(n - 1) { t[it + 1] - t[it] }.filter { it > 1e-4 }
        val dt = if (dts.isNotEmpty()) Np.median(dts.toDoubleArray()) else 1.0 / 60

        // gearing (needs the dyno)
        if (res != null && res.ok) {
            val rl = res.revLim; val pk = res.peakWkg; val pkRpm = res.peakRpm
            val gears = res.gears
            val topG = if (res.nGears != 0) res.nGears else gears.last().g
            val covHi = res.coverHi
            if (covHi < 0.93 * rl) add(1, "Dyno", "The curve stops at ${fixed(covHi, 0)} rpm: you never held full throttle higher (limiter ~${fixed(rl, 0)}).",
                "Hold a gear to the limiter on one straight, or shift later, so the top of the power curve and the best shift points can be measured.")
            var limN = 0
            for (i in 0 until n) if (rpm[i] >= 0.985 * rl && thr[i] > 0.9 && gear[i] >= 1) limN++
            val limS = limN * dt
            if (limS >= 1.5) add(2, "Gearing", "You sat on the rev limiter for ${fixed(limS, 1)} s this lap.",
                "Lengthen the final gear (raise top speed). You gain top speed and lose some pull. A taller gear that still reaches the limiter is wasted.")
            var iTop = 0
            for (i in 0 until n) if (v[i] > v[iTop]) iTop = i
            if (gear[iTop] >= 1 && thr[iTop] > 0.9 && rpm[iTop] < 0.9 * pkRpm) add(2, "Gearing",
                "At your top speed the engine was at ${fixed(rpm[iTop], 0)} rpm, well under its ${fixed(pkRpm, 0)} rpm power peak.",
                "Shorten the final gear: the car is drag-limited there, so you lose almost no top speed and gain acceleration everywhere.")
            val acc = BooleanArray(n) { thr[it] > 0.97 && brk[it] < 0.02 && gear[it] >= 1 && rpm[it] > 1000 }
            val accN = count(acc)
            if (accN > 100) {
                var lowN = 0
                for (i in 0 until n) if (acc[i]) { val p = res.pAt(rpm[i]).let { if (it.isNaN()) pk else it }; if (p < 0.85 * pk) lowN++ }
                val low = lowN.toDouble() / accN
                if (low > 0.25) add(2, "Gearing", "${fixed(low * 100, 0)}% of your full-throttle time was below 85% of peak power.",
                    "The gears are too long (or too widely spaced) for this track. Shorten the final gear or close up the ratios you use on corner exit.")
            }
            for (gd in gears.dropLast(1)) {
                if (gd.optShift != null && gd.landRpm != null) {
                    val pLand = res.pAt(gd.landRpm)
                    if (pLand.isFinite() && pLand < 0.82 * pk) add(2, "Gearing",
                        "Upshift ${gd.g}>${gd.g + 1} drops you to ${fixed(gd.landRpm, 0)} rpm, only ${fixed(pLand / pk * 100, 0)}% of peak power.",
                        "Close the gap: shorten gear ${gd.g + 1} (or lengthen ${gd.g}) so the drop is smaller. Close ratios suit a peaky engine.")
                }
            }
            val diffs = gears.filter { it.yourShift != null && it.optShift != null }.map { it.yourShift!! - it.optShift!! }
            if (diffs.size >= 2) {
                val avg = diffs.average()
                if (avg < -0.05 * rl) add(1, "Shifting", "You upshift about ${fixed(-avg, 0)} rpm earlier than the curve says is best.",
                    "Hold each gear closer to its shift point (see the table): the next gear only pulls harder past that rpm.")
                else if (avg > 0.04 * rl) add(1, "Shifting", "You upshift about ${fixed(avg, 0)} rpm later than the best crossover.",
                    "Shift a little earlier; past the crossover the next gear already accelerates harder.")
            }
            for (gd in gears) if (gd.g in 2 until topG && gd.share < 0.02) add(1, "Gearing",
                "Gear ${gd.g} is barely used here (${fixed(gd.share * 100, 1)}% of the lap).",
                "Only the gears you actually use matter on a track: spread the ratios you do use so they sit in the power band.")
            val topShare = mean(BooleanArray(n) { gear[it] == topG })
            if (topShare < 0.02 && topG >= 4) add(2, "Gearing", "Top gear ($topG) is almost never used on this track.",
                "Shorten the final gear for tracks with few straights: you lose a top speed you never reach and gain acceleration out of every corner.")
        }

        // balance
        val bal = balance(df, v)
        if (bal != null) {
            fun u(k: String) = bal[k] ?: 1.0
            if (u("entry") > 1.45) add(3, "Balance", "Understeer on corner entry (steering ${fixed(u("entry"), 1)}x what the turn needs).",
                "Lower the differential braking sensitivity; check front toe-out and rake (a little front toe-out or positive rake helps turn-in).")
            else if (u("entry") < 0.75) add(2, "Balance", "The rear rotates or steps out on corner entry.",
                "Move brake balance toward the front, and raise differential braking sensitivity a little.")
            if (u("mid") > 1.45) add(3, "Balance", "Mid-corner understeer (steering ${fixed(u("mid"), 1)}x what the turn needs).",
                "Lower the front anti-roll bar or raise the rear one. Keep front and rear natural frequency balanced, and don't run the ride height too low.")
            else if (u("mid") < 0.75) add(2, "Balance", "The car is loose through the middle of corners.",
                "Raise the front anti-roll bar or lower the rear one.")
            if (u("exit") > 1.45) add(3, "Balance", "Understeer on throttle at corner exit (steering ${fixed(u("exit"), 1)}x).",
                "Lower the differential initial torque (too high pushes wide). On rear-wheel drive, raise acceleration sensitivity for more rotation under power; on front-wheel drive, even out front tyre spin.")
            else if (u("exit") < 0.75) add(3, "Balance", "Power oversteer on corner exit.",
                "Lower the differential acceleration sensitivity, and add rear toe-in on rear-wheel drive for stability under power.")
            if (u("fast") > 1.45) add(2, "Aero", "Understeer in fast corners.", "Add rear downforce (it costs top speed and tyre wear).")
            else if (u("fast") < 0.75) add(2, "Aero", "The rear is light in fast corners.", "Add rear downforce for stability, or reduce front downforce.")
        }

        // traction + brakes
        val sfl = col(df, "tyre_slip_fl"); val sfr = col(df, "tyre_slip_fr"); val srl = col(df, "tyre_slip_rl"); val srr = col(df, "tyre_slip_rr")
        if (Np.max(sfl) > 0.2) {
            val pOn = BooleanArray(n) { thr[it] > 0.8 && v[it] > 3 && gear[it] >= 1 }
            val pN = count(pOn)
            if (pN > 100) {
                var spin = 0; var rearN = 0; var frontN = 0
                for (i in 0 until n) if (pOn[i]) {
                    val rear = max(srl[i], srr[i]); val front = max(sfl[i], sfr[i])
                    if (max(rear, front) > 1.15) spin++
                    if (rear > 1.15) rearN++
                    if (front > 1.15) frontN++
                }
                val spinF = spin.toDouble() / pN
                if (spinF > 0.12) {
                    val axle = if (rearN.toDouble() / pN >= frontN.toDouble() / pN) "rear" else "front"
                    add(2, "Traction", "Wheelspin ($axle axle) in ${fixed(spinF * 100, 0)}% of your full-throttle time.",
                        "Add rear toe-in for stability under power (rear-wheel drive), review differential acceleration sensitivity, and lift sooner or progressively on exit.")
                }
            }
            val bOn = BooleanArray(n) { brk[it] > 0.5 && v[it] > 10 }
            val bN = count(bOn)
            if (bN > 60) {
                var fl = 0; var rr = 0
                for (i in 0 until n) if (bOn[i]) { if (min(sfl[i], sfr[i]) < 0.85) fl++; if (min(srl[i], srr[i]) < 0.85) rr++ }
                val flF = fl.toDouble() / bN; val rrF = rr.toDouble() / bN
                if (flF > 0.08 && flF >= rrF) add(2, "Brakes", "The front tyres lock in ${fixed(flF * 100, 0)}% of heavy braking.",
                    "Move brake balance a click or two toward the rear, or brake slightly earlier and softer.")
                else if (rrF > 0.05) add(2, "Brakes", "The rear tyres lock in ${fixed(rrF * 100, 0)}% of heavy braking.",
                    "Move brake balance toward the front for stability.")
            }
        }

        // tyres + ride height
        val tfl = col(df, "tyre_temp_fl"); val tfr = col(df, "tyre_temp_fr"); val trl = col(df, "tyre_temp_rl"); val trr = col(df, "tyre_temp_rr")
        val ft = DoubleArray(n) { (tfl[it] + tfr[it]) / 2 }; val rt = DoubleArray(n) { (trl[it] + trr[it]) / 2 }
        if (Np.max(ft) > 20 && Np.max(rt) > 20) {
            val mv = BooleanArray(n) { v[it] > 5 }
            val dtp = if (count(mv) > 0) Np.mean(pick(rt, mv)) - Np.mean(pick(ft, mv)) else 0.0
            if (dtp > 10) add(1, "Tyres", "The rear tyres run ${fixed(dtp, 0)}C hotter than the fronts.",
                "They will wear faster: try moving ballast forward and adjust the spring rate to match.")
            else if (dtp < -10) add(1, "Tyres", "The front tyres run ${fixed(-dtp, 0)}C hotter than the rears.",
                "Fronts are doing the work: lower the front anti-roll bar or add rear downforce to share it.")
        }
        val rh = col(df, "ride_height_mm")
        val rhMoving = pick(rh, BooleanArray(n) { v[it] > 10 })
        if (Np.max(rh) > 5 && rhMoving.isNotEmpty() && rhMoving.count { it < 5.0 }.toDouble() / rhMoving.size > 0.01)
            add(1, "Suspension", "The car may be bottoming out (ride height near zero).",
                "Raise the ride height, or stiffen the springs: keep it as low as you can without bottoming.")

        return hints.sortedByDescending { it.sev }
    }
}
