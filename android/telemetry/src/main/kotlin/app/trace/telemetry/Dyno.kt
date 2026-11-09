package app.trace.telemetry

import kotlin.math.*

/**
 * dyno.py on the phone: power curve, gearbox and tuning hints worked out from one lap.
 * Specific wheel power is P/m = v * (a + g*sin(slope) + c0 + c1*v^2), taken from full-throttle, no-wheelspin,
 * mostly-straight samples and binned by rpm. Absolute numbers are an estimate (about +/-10%); the curve SHAPE,
 * the peak rpm and the shift points are much more reliable.
 */
object Dyno {
    const val G = 9.81
    const val HP_W = 745.7
    private val REQ = listOf("t", "speed_kmh", "rpm", "gear", "throttle", "brake")
    private const val C0_DEFAULT = 0.12
    private const val C1_DEFAULT = 2.8e-4

    class Gear(
        val g: Int, val ratio: Double?, val k: Double, val src: String, val kmhPer1000: Double, val topKmh: Double,
        val share: Double, val optShift: Double?, val landRpm: Double?, val yourShift: Double?, var finalDrive: Double? = null,
    )

    class Resist(val c0: Double, val c1: Double, val src: String)
    class Top(val kmh: Double, val by: String, val gear: Int)

    class Result(val ok: Boolean, val msg: String = "") {
        var resist: Resist = Resist(C0_DEFAULT, C1_DEFAULT, "assumed")
        var nSamples = 0
        var revLim = 0.0
        var rpm = DoubleArray(0); var pWkg = DoubleArray(0); var q1 = DoubleArray(0); var q3 = DoubleArray(0)
        var tqNmkg = DoubleArray(0); var boost = DoubleArray(0)
        var peakRpm = 0.0; var peakWkg = 0.0; var peakTqRpm = 0.0; var peakTqNmkg = 0.0
        var cover = 0.0 to 0.0
        var hpPerTonne = 0.0
        var mass: Double? = null
        var unitP = "hp/t"; var unitT = "Nm/t"
        var pDisp = DoubleArray(0); var q1Disp = DoubleArray(0); var q3Disp = DoubleArray(0); var tDisp = DoubleArray(0)
        var peakPDisp = 0.0; var peakTDisp = 0.0
        var nGears = 0
        var gears: List<Gear> = emptyList()
        var curves: Map<Int, Pair<DoubleArray, DoubleArray>> = emptyMap()
        var top: Top = Top(0.0, "", 0)
        /** nan outside the rpm range the lap covered. */
        var pAt: (Double) -> Double = { Double.NaN }
        /** holds the end values (for extrapolating top speed). */
        var pEdge: (Double) -> Double = { 0.0 }
    }

    class Hint(val sev: Int, val area: String, val finding: String, val fix: String)

    // ── helpers ──────────────────────────────────────────────────────────────
    private fun col(f: Frame, name: String, def: Double = 0.0): DoubleArray =
        if (f.has(name)) f[name].let { a -> DoubleArray(a.size) { if (a[it].isFinite()) a[it] else def } } else DoubleArray(f.n) { def }

    private fun smooth(x: DoubleArray, n: Int): DoubleArray {
        if (n < 3 || x.size < n) return x.copyOf()
        val pad = n / 2
        return DoubleArray(x.size) { i ->
            var s = 0.0
            for (k in 0 until n) s += x[(i - pad + k).coerceIn(0, x.size - 1)]
            s / n
        }
    }

    private fun gradient(f: DoubleArray, t: DoubleArray): DoubleArray {
        val n = f.size
        val out = DoubleArray(n)
        if (n < 2) return out
        out[0] = (f[1] - f[0]) / (t[1] - t[0]); out[n - 1] = (f[n - 1] - f[n - 2]) / (t[n - 1] - t[n - 2])
        for (i in 1 until n - 1) {
            val hd = t[i] - t[i - 1]; val hs = t[i + 1] - t[i]
            out[i] = -hs / (hd * (hd + hs)) * f[i - 1] + (hs - hd) / (hd * hs) * f[i] + hd / (hs * (hd + hs)) * f[i + 1]
        }
        return out
    }

    private fun nearShift(gear: IntArray, pad: Int): BooleanArray {
        val chg = IntArray(gear.size); for (i in 1 until gear.size) if (gear[i] != gear[i - 1]) chg[i] = 1
        val pre = IntArray(gear.size + 1); for (i in gear.indices) pre[i + 1] = pre[i] + chg[i]
        return BooleanArray(gear.size) { i -> pre[min(gear.size, i + pad + 1)] - pre[max(0, i - pad)] > 0 }
    }

    private fun interpNan(x: Double, xs: DoubleArray, ys: DoubleArray): Double =
        if (x < xs.first() || x > xs.last()) Double.NaN else Np.interp(x, xs, ys)

    private fun sel(a: DoubleArray, m: BooleanArray): DoubleArray { val l = ArrayList<Double>(); for (i in a.indices) if (m[i]) l.add(a[i]); return l.toDoubleArray() }
    private fun cnt(m: BooleanArray) = m.count { it }

    // ── the dyno ─────────────────────────────────────────────────────────────
    fun analyse(df: Frame?, gearRatios: List<Double>? = null, massKg: Double? = null, tyreRadiusM: Double? = null): Result {
        if (df == null || df.n < 120 || REQ.any { !df.has(it) }) return Result(false, "Needs a full lap with engine data (not a leaderboard ghost).")
        val n = df.n
        val t0 = col(df, "t")
        val t = DoubleArray(n); var mx = Double.NEGATIVE_INFINITY
        for (i in 0 until n) { mx = max(mx, t0[i]); t[i] = mx + i * 1e-6 }
        val d = (1 until n).map { t[it] - t[it - 1] }.filter { it > 1e-4 }
        if (d.size < 20) return Result(false, "This lap has no usable timestamps.")
        val dt = Np.median(d.toDoubleArray())
        var w = max(3, Math.round(0.2 / dt).toInt()); if (w % 2 == 0) w++

        val v = smooth(DoubleArray(n) { col(df, "speed_kmh")[it] / 3.6 }, w)
        val a = gradient(v, t)
        val rpm = smooth(col(df, "rpm"), w)
        val gear = IntArray(n) { Math.rint(col(df, "gear")[it]).toInt() }
        val thr = col(df, "throttle"); val brk = col(df, "brake")
        val angY = col(df, "ang_y")
        val ay = DoubleArray(n) { abs(angY[it]) * v[it] }
        val vx = col(df, "vel_x"); val vy = col(df, "vel_y"); val vz = col(df, "vel_z")
        val sinRaw = DoubleArray(n) { val sp = sqrt(vx[it] * vx[it] + vy[it] * vy[it] + vz[it] * vz[it]); if (sp > 3.0) vy[it] / max(sp, 1e-6) else 0.0 }
        val sinT = smooth(sinRaw, w * 3 or 1).let { s -> DoubleArray(n) { s[it].coerceIn(-0.3, 0.3) } }

        val slips = listOf("fl", "fr", "rl", "rr").map { col(df, "tyre_slip_$it") }
        val moving = BooleanArray(n) { v[it] > 5 }
        val nMoving = cnt(moving)
        val haveSlip = nMoving > 0 && (0 until n).filter { moving[it] }.let { idx -> idx.count { i -> slips.all { s -> s[i] > 0.2 } }.toDouble() / idx.size } > 0.5
        val slipMax = DoubleArray(n) { i -> if (haveSlip) slips.maxOf { it[i] } else 1.0 }
        val slipMin = DoubleArray(n) { i -> if (haveSlip) slips.minOf { it[i] } else 1.0 }

        val pad = max(3, Math.round(0.25 / dt).toInt())
        val near = nearShift(gear, pad)

        // rolling resistance + aero drag from the coast-down
        val coast = BooleanArray(n) { thr[it] < 0.03 && brk[it] < 0.03 && v[it] > 12 && ay[it] < 1.5 && gear[it] >= 1 && !near[it] }
        var c0 = C0_DEFAULT; var c1 = C1_DEFAULT; var src = "assumed"
        val vc = sel(v, coast)
        if (vc.size >= 60 && Np.percentile(vc, 95.0) - Np.percentile(vc, 5.0) >= 10) {
            val y = DoubleArray(n) { -(a[it] + G * sinT[it]) }
            val yc = sel(y, coast)
            // least squares y = f0 + f1 * v^2
            var sx = 0.0; var sxx = 0.0; var sy = 0.0; var sxy = 0.0
            val m = vc.size.toDouble()
            for (i in vc.indices) { val x = vc[i] * vc[i]; sx += x; sxx += x * x; sy += yc[i]; sxy += x * yc[i] }
            val den = m * sxx - sx * sx
            if (abs(den) > 1e-12) {
                val f1 = (m * sxy - sx * sy) / den; val f0 = (sy - f1 * sx) / m
                if (f0 in 0.02..0.4 && f1 in 5e-5..8e-4) { c0 = f0; c1 = f1; src = "fitted" }
            }
        }

        // full-throttle samples
        val fSpec = DoubleArray(n) { a[it] + G * sinT[it] + c0 + c1 * v[it] * v[it] }
        val pSpec = DoubleArray(n) { fSpec[it] * v[it] }
        val mk = BooleanArray(n) {
            thr[it] > 0.97 && brk[it] < 0.02 && gear[it] >= 1 && !near[it] && v[it] > 3.0 && ay[it] < 3.0 && slipMax[it] < 1.08 && rpm[it] > 1000 &&
                fSpec[it] > 0 && pSpec[it] > 0 && pSpec[it] < 2500
        }
        val nm = cnt(mk)
        if (nm < 80) return Result(false, "Not enough clean full-throttle driving in this lap ($nm samples). Use a lap with a few long straights.")
        val res = Result(true)
        res.resist = Resist(c0, c1, src); res.nSamples = nm

        val obsMax = Np.percentile(sel(rpm, BooleanArray(n) { rpm[it] > 0 }), 99.8)
        val cands = arrayListOf(obsMax)
        for (cn in listOf("rpm_limiter", "max_rpm")) { val c = col(df, cn).filter { it > 1000 }; if (c.isNotEmpty()) cands.add(Np.median(c.toDoubleArray())) }
        val revLim = min(cands.max(), obsMax * 1.25)
        res.revLim = revLim

        val bw = max(50.0, Math.round(revLim / 70.0 / 50.0) * 50.0)
        val rpmM = sel(rpm, mk)
        val lo = floor(Np.percentile(rpmM, 1.0) / bw) * bw
        val hi = Np.percentile(rpmM, 99.5) + bw
        val pm = sel(pSpec, mk); val boostM = sel(col(df, "boost"), mk)
        val centers = ArrayList<Double>(); val q1l = ArrayList<Double>(); val q3l = ArrayList<Double>(); val bo = ArrayList<Double>(); val bst = ArrayList<Double>()
        var c = lo
        while (c <= hi + bw + 1e-9) {
            val idx = rpmM.indices.filter { abs(rpmM[it] - c) <= 1.5 * bw }
            if (idx.size >= 6) {
                val p = DoubleArray(idx.size) { pm[idx[it]] }
                centers.add(c); q1l.add(Np.percentile(p, 25.0)); q3l.add(Np.percentile(p, 75.0)); bo.add(Np.median(p))
                bst.add(Np.median(DoubleArray(idx.size) { boostM[idx[it]] }))
            }
            c += bw
        }
        if (centers.size < 8) return Result(false, "The lap doesn't sweep enough of the rev range to draw a curve.")
        val cen = centers.toDoubleArray()
        val med = smooth(bo.toDoubleArray(), 3)
        val q1 = smooth(q1l.toDoubleArray(), 3); val q3 = smooth(q3l.toDoubleArray(), 3)
        res.pAt = { r -> interpNan(r, cen, med) }
        res.pEdge = { r -> Np.interp(r, cen, med) }
        val ipk = med.indices.maxByOrNull { med[it] }!!
        val torque = DoubleArray(med.size) { med[it] / (2 * PI * cen[it] / 60.0) }
        val itq = torque.indices.maxByOrNull { torque[it] }!!
        res.rpm = cen; res.pWkg = med; res.q1 = q1; res.q3 = q3; res.tqNmkg = torque; res.boost = bst.toDoubleArray()
        res.peakRpm = cen[ipk]; res.peakWkg = med[ipk]; res.peakTqRpm = cen[itq]; res.peakTqNmkg = torque[itq]
        res.cover = cen.first() to cen.last()
        res.hpPerTonne = med[ipk] * 1000.0 / HP_W
        val mass = massKg?.takeIf { it in 300.0..5000.0 }
        res.mass = mass
        val pf = if (mass != null) mass / HP_W else 1000.0 / HP_W
        val tf = mass ?: 1000.0
        res.unitP = if (mass != null) "hp" else "hp/t"; res.unitT = if (mass != null) "Nm" else "Nm/t"
        res.pDisp = DoubleArray(med.size) { med[it] * pf }; res.q1Disp = DoubleArray(med.size) { q1[it] * pf }; res.q3Disp = DoubleArray(med.size) { q3[it] * pf }
        res.tDisp = DoubleArray(med.size) { torque[it] * tf }
        res.peakPDisp = med[ipk] * pf; res.peakTDisp = torque[itq] * tf

        // gearbox: measured rpm per m/s in each gear
        val ratios = (gearRatios ?: emptyList()).filter { it.isFinite() }
        val ks = HashMap<Int, Double>(); val kn = HashMap<Int, Int>()
        val okK = BooleanArray(n) { !near[it] && v[it] > 3 && slipMax[it] < 1.05 && slipMin[it] > 0.95 && rpm[it] > 1000 }
        for (g in 1..8) {
            val s = BooleanArray(n) { okK[it] && gear[it] == g }
            if (cnt(s) >= 15) {
                ks[g] = Np.median(DoubleArray(n) { rpm[it] / max(v[it], 1e-9) }.let { r -> sel(r, s) }); kn[g] = cnt(s)
            }
        }
        val srcK = HashMap<Int, String>(); ks.keys.forEach { srcK[it] = "measured" }
        if (ks.isNotEmpty() && ratios.isNotEmpty()) {
            val gref = kn.maxByOrNull { it.value }!!.key
            if (gref <= ratios.size && ratios[gref - 1] > 0) {
                for (g in 1..min(ratios.size, 8)) if (g !in ks && ratios[g - 1] > 0) { ks[g] = ks[gref]!! * ratios[g - 1] / ratios[gref - 1]; srcK[g] = "from ratio" }
            }
        }
        if (ks.isEmpty()) return Result(false, "Couldn't pin down any gear (too little steady driving in each).")
        res.nGears = max(ratios.count { it > 0 }, ks.keys.max())
        val share = (1..8).associateWith { g -> gear.count { it == g }.toDouble() / n }
        val gs = ks.keys.sorted()
        val opt = HashMap<Int, Double>(); val land = HashMap<Int, Double>()
        for (g in gs) {
            if (g + 1 !in ks) continue
            val rho = ks[g + 1]!! / ks[g]!!
            if (rho !in 0.4..0.98) continue
            val step = max(25.0, bw / 4.0)
            val grid = ArrayList<Double>(); var r = res.peakRpm; while (r < revLim) { grid.add(r); r += step }
            if (grid.size < 2) continue
            var anyValid = false; var rOpt: Double? = null
            for (gr in grid) {
                val pa = res.pAt(gr); val pb = res.pAt(gr * rho)
                if (pa.isFinite() && pb.isFinite()) { anyValid = true; if (pb >= pa && rOpt == null) rOpt = gr }
            }
            if (!anyValid) continue
            val ro = rOpt ?: revLim
            opt[g] = ro; land[g] = ro * rho
        }
        val rawRpm = col(df, "rpm")
        val mine = HashMap<Int, ArrayList<Double>>()
        for (i in 1 until n) if (gear[i] == gear[i - 1] + 1 && gear[i - 1] >= 1 && thr[max(0, i - 4)] > 0.9) mine.getOrPut(gear[i - 1]) { ArrayList() }.add(rawRpm[i - 1])
        val gl = ArrayList<Gear>()
        for (g in gs) {
            val k = ks[g]!!
            val ratio = if (g <= ratios.size && ratios[g - 1] > 0) ratios[g - 1] else null
            val gd = Gear(g, ratio, k, srcK[g]!!, 3.6 * 1000.0 / k, revLim / k * 3.6, share[g] ?: 0.0, opt[g], land[g], mine[g]?.takeIf { it.isNotEmpty() }?.let { Np.median(it.toDoubleArray()) })
            if (tyreRadiusM != null && tyreRadiusM > 0 && ratio != null) gd.finalDrive = k * 2 * PI * tyreRadiusM / 60.0 / ratio
            gl.add(gd)
        }
        res.gears = gl

        val curves = LinkedHashMap<Int, Pair<DoubleArray, DoubleArray>>()
        for (g in gs) {
            val k = ks[g]!!
            val vv = Np.linspace(max(2.0, cen[0] / k), revLim / k, 80)
            curves[g] = DoubleArray(vv.size) { vv[it] * 3.6 } to DoubleArray(vv.size) { res.pEdge(k * vv[it]) / vv[it] / G }
        }
        res.curves = curves

        val gt = gs.last(); val kt = ks[gt]!!
        val vv = Np.linspace(2.0, revLim / kt, 500)
        val fnet = DoubleArray(vv.size) { res.pEdge(kt * vv[it]) / vv[it] - (c0 + c1 * vv[it] * vv[it]) }
        val lastPos = fnet.indices.lastOrNull { fnet[it] > 0 }
        res.top = if (lastPos != null && lastPos < vv.size - 2) Top(vv[lastPos] * 3.6, "drag", gt) else Top(vv.last() * 3.6, "rev limiter", gt)
        return res
    }

    // ── tuning hints ─────────────────────────────────────────────────────────
    /** Understeer ratio per corner phase, or null. */
    private fun balance(df: Frame, v: DoubleArray): Map<String, Pair<Double, Int>>? {
        val wbAll = col(df, "wheel_base").filter { it > 0.5 }
        if (wbAll.isEmpty() || !df.has("wheel_steering_angle_l")) return null
        val n = df.n
        val len = Np.median(wbAll.toDoubleArray())
        val l = col(df, "wheel_steering_angle_l"); val r = col(df, "wheel_steering_angle_r")
        var d = DoubleArray(n) { (l[it] + r[it]) / 2.0 }
        if (Np.percentile(DoubleArray(n) { abs(d[it]) }, 99.0) > 1.6) d = DoubleArray(n) { Math.toRadians(d[it]) }
        val yaw = col(df, "ang_y")
        val ok = BooleanArray(n) { v[it] > 12 && abs(yaw[it]) * v[it] > 2.0 && abs(d[it]) > 0.01 }
        if (cnt(ok) < 60) return null
        val dn = DoubleArray(n) { len * yaw[it] / max(v[it], 1.0) }
        val dx = sel(d, ok); val dy = sel(dn, ok)
        val mx = Np.mean(dx); val my = Np.mean(dy)
        var sxy = 0.0; var sxx = 0.0; var syy = 0.0
        for (i in dx.indices) { sxy += (dx[i] - mx) * (dy[i] - my); sxx += (dx[i] - mx).pow(2); syy += (dy[i] - my).pow(2) }
        val corr = sxy / sqrt(max(sxx * syy, 1e-18))
        if (corr < 0) d = DoubleArray(n) { -d[it] }
        val ratio = DoubleArray(n) { (d[it] / (if (abs(dn[it]) < 1e-6) 1e-6 else dn[it])).coerceIn(-3.0, 5.0) }
        val thr = col(df, "throttle"); val brk = col(df, "brake")
        val phases = linkedMapOf(
            "entry" to BooleanArray(n) { ok[it] && brk[it] > 0.1 },
            "mid" to BooleanArray(n) { ok[it] && thr[it] <= 0.5 && brk[it] <= 0.1 },
            "exit" to BooleanArray(n) { ok[it] && thr[it] > 0.5 && brk[it] < 0.05 },
            "fast" to BooleanArray(n) { ok[it] && v[it] > 50 },
        )
        val out = LinkedHashMap<String, Pair<Double, Int>>()
        for ((k, m) in phases) if (cnt(m) >= 25) out[k] = Np.median(sel(ratio, m)) to cnt(m)
        return out.ifEmpty { null }
    }

    private fun f0(x: Double) = fixed(x, 0)
    private fun f1(x: Double) = fixed(x, 1)

    fun advise(df: Frame?, res: Result?): List<Hint> {
        val hints = ArrayList<Hint>()
        fun add(sev: Int, area: String, finding: String, fix: String) { hints.add(Hint(sev, area, finding, fix)) }
        if (df == null || df.n < 120) return hints
        val n = df.n
        val v = DoubleArray(n) { col(df, "speed_kmh")[it] / 3.6 }
        val thr = col(df, "throttle"); val brk = col(df, "brake"); val rpm = col(df, "rpm")
        val gear = IntArray(n) { Math.rint(col(df, "gear")[it]).toInt() }
        val t = col(df, "t")
        val dts = (1 until n).map { t[it] - t[it - 1] }.filter { it > 1e-4 }
        val dt = if (dts.isNotEmpty()) Np.median(dts.toDoubleArray()) else 1.0 / 60

        if (res != null && res.ok) {
            val rl = res.revLim; val pk = res.peakWkg; val pkRpm = res.peakRpm
            val gears = res.gears
            val topG = if (res.nGears > 0) res.nGears else gears.last().g
            val covHi = res.cover.second
            if (covHi < 0.93 * rl) add(1, "Dyno", "The curve stops at ${f0(covHi)} rpm: you never held full throttle higher (limiter ~${f0(rl)}).",
                "Hold a gear to the limiter on one straight, or shift later, so the top of the power curve and the best shift points can be measured.")
            val limS = (0 until n).count { rpm[it] >= 0.985 * rl && thr[it] > 0.9 && gear[it] >= 1 } * dt
            if (limS >= 1.5) add(2, "Gearing", "You sat on the rev limiter for ${f1(limS)} s this lap.",
                "Lengthen the final gear (raise top speed). You gain top speed and lose some pull. A taller gear that still reaches the limiter is wasted.")
            val iTop = v.indices.maxByOrNull { v[it] }!!
            if (gear[iTop] >= 1 && thr[iTop] > 0.9 && rpm[iTop] < 0.9 * pkRpm) add(2, "Gearing",
                "At your top speed the engine was at ${f0(rpm[iTop])} rpm, well under its ${f0(pkRpm)} rpm power peak.",
                "Shorten the final gear: the car is drag-limited there, so you lose almost no top speed and gain acceleration everywhere.")
            val acc = (0 until n).filter { thr[it] > 0.97 && brk[it] < 0.02 && gear[it] >= 1 && rpm[it] > 1000 }
            if (acc.size > 100) {
                val low = acc.count { val p = res.pAt(rpm[it]); (if (p.isNaN()) pk else p) < 0.85 * pk }.toDouble() / acc.size
                if (low > 0.25) add(2, "Gearing", "${f0(low * 100)}% of your full-throttle time was below 85% of peak power.",
                    "The gears are too long (or too widely spaced) for this track. Shorten the final gear or close up the ratios you use on corner exit.")
            }
            for (gd in gears.dropLast(1)) {
                if (gd.optShift != null && gd.landRpm != null) {
                    val pl = res.pAt(gd.landRpm)
                    if (pl.isFinite() && pl < 0.82 * pk) add(2, "Gearing", "Upshift ${gd.g}>${gd.g + 1} drops you to ${f0(gd.landRpm)} rpm, only ${f0(pl / pk * 100)}% of peak power.",
                        "Close the gap: shorten gear ${gd.g + 1} (or lengthen ${gd.g}) so the drop is smaller. Close ratios suit a peaky engine.")
                }
            }
            val diffs = gears.filter { it.yourShift != null && it.optShift != null }.map { it.yourShift!! - it.optShift!! }
            if (diffs.size >= 2) {
                val avg = diffs.average()
                if (avg < -0.05 * rl) add(1, "Shifting", "You upshift about ${f0(-avg)} rpm earlier than the curve says is best.",
                    "Hold each gear closer to its shift point (see the table): the next gear only pulls harder past that rpm.")
                else if (avg > 0.04 * rl) add(1, "Shifting", "You upshift about ${f0(avg)} rpm later than the best crossover.",
                    "Shift a little earlier; past the crossover the next gear already accelerates harder.")
            }
            for (gd in gears) if (gd.g in 2 until topG && gd.share < 0.02) add(1, "Gearing", "Gear ${gd.g} is barely used here (${f1(gd.share * 100)}% of the lap).",
                "Only the gears you actually use matter on a track: spread the ratios you do use so they sit in the power band.")
            val topShare = gear.count { it == topG }.toDouble() / n
            if (topShare < 0.02 && topG >= 4) add(2, "Gearing", "Top gear ($topG) is almost never used on this track.",
                "Shorten the final gear for tracks with few straights: you lose a top speed you never reach and gain acceleration out of every corner.")
        }

        val bal = balance(df, v)
        if (bal != null) {
            val u = bal.mapValues { it.value.first }
            fun g(k: String) = u[k] ?: 1.0
            if (g("entry") > 1.45) add(3, "Balance", "Understeer on corner entry (steering ${f1(g("entry"))}x what the turn needs).",
                "Lower the differential braking sensitivity; check front toe-out and rake (a little front toe-out or positive rake helps turn-in).")
            else if (g("entry") < 0.75) add(2, "Balance", "The rear rotates or steps out on corner entry.",
                "Move brake balance toward the front, and raise differential braking sensitivity a little.")
            if (g("mid") > 1.45) add(3, "Balance", "Mid-corner understeer (steering ${f1(g("mid"))}x what the turn needs).",
                "Lower the front anti-roll bar or raise the rear one. Keep front and rear natural frequency balanced, and don't run the ride height too low.")
            else if (g("mid") < 0.75) add(2, "Balance", "The car is loose through the middle of corners.", "Raise the front anti-roll bar or lower the rear one.")
            if (g("exit") > 1.45) add(3, "Balance", "Understeer on throttle at corner exit (steering ${f1(g("exit"))}x).",
                "Lower the differential initial torque (too high pushes wide). On rear-wheel drive, raise acceleration sensitivity for more rotation under power; on front-wheel drive, even out front tyre spin.")
            else if (g("exit") < 0.75) add(3, "Balance", "Power oversteer on corner exit.",
                "Lower the differential acceleration sensitivity, and add rear toe-in on rear-wheel drive for stability under power.")
            if (g("fast") > 1.45) add(2, "Aero", "Understeer in fast corners.", "Add rear downforce (it costs top speed and tyre wear).")
            else if (g("fast") < 0.75) add(2, "Aero", "The rear is light in fast corners.", "Add rear downforce for stability, or reduce front downforce.")
        }

        val s = listOf("fl", "fr", "rl", "rr").associateWith { col(df, "tyre_slip_$it") }
        if (s["fl"]!!.max() > 0.2) {
            val pOn = (0 until n).filter { thr[it] > 0.8 && v[it] > 3 && gear[it] >= 1 }
            if (pOn.size > 100) {
                val rear = pOn.map { max(s["rl"]!![it], s["rr"]!![it]) }; val front = pOn.map { max(s["fl"]!![it], s["fr"]!![it]) }
                val spin = pOn.indices.count { max(rear[it], front[it]) > 1.15 }.toDouble() / pOn.size
                if (spin > 0.12) {
                    val axle = if (rear.count { it > 1.15 } >= front.count { it > 1.15 }) "rear" else "front"
                    add(2, "Traction", "Wheelspin ($axle axle) in ${f0(spin * 100)}% of your full-throttle time.",
                        "Add rear toe-in for stability under power (rear-wheel drive), review differential acceleration sensitivity, and lift sooner or progressively on exit.")
                }
            }
            val bOn = (0 until n).filter { brk[it] > 0.5 && v[it] > 10 }
            if (bOn.size > 60) {
                val fl = bOn.count { min(s["fl"]!![it], s["fr"]!![it]) < 0.85 }.toDouble() / bOn.size
                val rr = bOn.count { min(s["rl"]!![it], s["rr"]!![it]) < 0.85 }.toDouble() / bOn.size
                if (fl > 0.08 && fl >= rr) add(2, "Brakes", "The front tyres lock in ${f0(fl * 100)}% of heavy braking.",
                    "Move brake balance a click or two toward the rear, or brake slightly earlier and softer.")
                else if (rr > 0.05) add(2, "Brakes", "The rear tyres lock in ${f0(rr * 100)}% of heavy braking.", "Move brake balance toward the front for stability.")
            }
        }

        val ft = DoubleArray(n) { (col(df, "tyre_temp_fl")[it] + col(df, "tyre_temp_fr")[it]) / 2 }
        val rt = DoubleArray(n) { (col(df, "tyre_temp_rl")[it] + col(df, "tyre_temp_rr")[it]) / 2 }
        if (ft.max() > 20 && rt.max() > 20) {
            val mv = (0 until n).filter { v[it] > 5 }
            val dtp = if (mv.isNotEmpty()) mv.map { rt[it] }.average() - mv.map { ft[it] }.average() else 0.0
            if (dtp > 10) add(1, "Tyres", "The rear tyres run ${f0(dtp)}C hotter than the fronts.", "They will wear faster: try moving ballast forward and adjust the spring rate to match.")
            else if (dtp < -10) add(1, "Tyres", "The front tyres run ${f0(-dtp)}C hotter than the rears.", "Fronts are doing the work: lower the front anti-roll bar or add rear downforce to share it.")
        }
        val rh = col(df, "ride_height_mm")
        val mv10 = (0 until n).filter { v[it] > 10 }
        if (rh.max() > 5 && mv10.isNotEmpty() && mv10.count { rh[it] < 5.0 }.toDouble() / mv10.size > 0.01)
            add(1, "Suspension", "The car may be bottoming out (ride height near zero).", "Raise the ride height, or stiffen the springs: keep it as low as you can without bottoming.")

        return hints.sortedByDescending { it.sev }
    }
}
