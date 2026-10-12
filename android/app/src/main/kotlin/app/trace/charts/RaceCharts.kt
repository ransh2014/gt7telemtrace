package app.trace.charts

import androidx.compose.ui.graphics.Color
import app.trace.AP
import app.trace.telemetry.*
import kotlin.math.*

/** The 17 chart groups of the Race Analyst, in the PC order. */
object RaceCharts {
    val GROUPS = listOf("Race", "Inputs", "Engine", "Tyres", "Dynamics", "Maps", "G-Force", "Fuel", "Braking", "Laps", "Traction",
        "Tele Diff", "Ratings", "Heat Maps", "Timeline", "Corners", "Consistency")

    class Input(val a: AP, val df: Frame, val dfb: Frame?, val baseline: RaceLoader.Baseline? = null)

    fun build(name: String, i: Input): Fig {
        val c = Ctx(i.a, i.df, i.dfb, true)
        return when (name) {
            "Race" -> c.raceOverview()
            "Inputs" -> c.inputs()
            "Engine" -> c.engine()
            "Tyres" -> c.tyres()
            "Dynamics" -> c.dynamics()
            "Maps" -> c.maps()
            "G-Force" -> c.gforce()
            "Fuel" -> c.fuel()
            "Braking" -> c.braking()
            "Laps" -> c.laps()
            "Traction" -> c.traction()
            "Tele Diff" -> c.telediff()
            "Ratings" -> c.ratings()
            "Heat Maps" -> c.heatMaps()
            "Timeline" -> c.timeline()
            "Corners" -> c.raceCorners(i.baseline ?: RaceLoader.bestLapBaseline(i.df))
            "Consistency" -> c.consistency()
            else -> Fig(listOf(c.msg("Unknown chart group")), 1)
        }
    }
}

fun Ctx.raceOverview(): Fig {
    val d = df; val b = dfb
    val p = ArrayList<Panel>()
    fun grid(x: Frame): String {
        val g = x["current_position"].filter { it > 0 }; val n = x["total_positions"].filter { it > 0 }
        return if (g.isNotEmpty()) "P${g[0].toInt()}" + (if (n.isNotEmpty()) " of ${n[0].toInt()}" else "") else "--"
    }
    var txt = "Grid (A): ${grid(d)}"
    if (b != null) txt += "\nGrid (B): ${grid(b)}"
    p.add(XY(off = true, layers = listOf(TextL(0.5, 0.6, txt, FG, 12f, axes = true),
        TextL(0.5, 0.25, "GT7 sends the starting grid slot only --\nno live race position", DIM, 8f, axes = true))))
    val pit = arrayListOf<Layer>(FillL(d[X], d["pit_flag"], DoubleArray(d.n), ORG, 0.6f, Step.POST))
    pit.add(LineL(DoubleArray(0), DoubleArray(0), ORG, 1f, label = "A"))
    if (b != null) {
        pit.add(FillL(b[X], scaled(b["pit_flag"], 0.6), DoubleArray(b.n), ACC, 0.4f, Step.POST))
        pit.add(LineL(DoubleArray(0), DoubleArray(0), ACC, 1f, label = "B"))
    }
    p.add(ax("Pit Stops (A: ${RaceLoader.countPitStops(d)})", "In Pit", layers = pit, legend = b != null, ylim = doubleArrayOf(-0.05, 1.05)))
    val fl = arrayListOf<Layer>()
    fl.addAll(line(d, "fuel_remaining", GRN, fill = true))
    val fd = Np.diff(d["fuel_remaining"])
    val ri = where(d) { !fd[it].isNaN() && fd[it] > 1 }
    if (ri.isNotEmpty()) fl.add(ScatterL(take(d[X], ri), take(d["fuel_remaining"], ri), YLW, s = 18f, z = 5))
    fl.addAll(lb(b, "fuel_remaining"))
    p.add(ax("Fuel Over Race", "% (GT7)", layers = fl))
    val splits = RaceLoader.lapSplitStats(d)
    if (splits.isNotEmpty()) {
        val ln = DoubleArray(splits.size) { (it + 1).toDouble() }
        val ly = arrayListOf<Layer>(LineL(ln, DoubleArray(splits.size) { splits[it].timeS }, CYN, 1.3f, marker = 4f, label = "A"))
        var leg = false
        if (b != null) {
            val sb = RaceLoader.lapSplitStats(b)
            if (sb.isNotEmpty()) {
                ly.add(LineL(DoubleArray(sb.size) { (it + 1).toDouble() }, DoubleArray(sb.size) { sb[it].timeS }, ACC, 1.1f, 0.8f, 1, label = "B", marker = 4f, markerSquare = true))
                leg = true
            }
        }
        p.add(ax("Lap Time Trend", "s", "Lap", ly, legend = leg))
    } else p.add(XY(off = true))
    return fig(p, 2)
}

fun Ctx.laps(): Fig {
    val d = df
    val sa = RaceLoader.lapSplitStats(d)
    val sbl = if (dfb != null) RaceLoader.lapSplitStats(dfb) else null
    val sbOk = sbl != null && sbl.isNotEmpty()
    fun arr(s: List<RaceLoader.Split>, f: (RaceLoader.Split) -> Double) = DoubleArray(s.size) { f(s[it]) }
    val na = sa.size
    val tA = arr(sa) { it.timeS }; val avgA = arr(sa) { it.avgSpeed }; val topA = arr(sa) { it.topSpeed }
    val brkA = arr(sa) { it.brakePct }; val thrA = arr(sa) { it.throttlePct }; val latA = arr(sa) { it.maxLatG }
    fun bv(f: (RaceLoader.Split) -> Double): DoubleArray? = if (sbOk) arr(sbl!!, f) else null
    val tB = bv { it.timeS }
    val p = ArrayList<Panel>()
    fun b2(title: String, va: DoubleArray, vb: DoubleArray?, yl: String, ca: Color, cb: Color): XY {
        val nb = if (vb != null) min(va.size, vb.size) else va.size
        return bars2(title, va, vb, yl, "Lap", ca, cb, showAll = nb <= 20)
    }
    p.add(b2("Lap Times", tA, tB, "s", CYN, ACC))
    p.add(b2("Avg Speed/Lap", avgA, bv { it.avgSpeed }, "km/h", CYN, ACC))
    p.add(b2("Top Speed/Lap", topA, bv { it.topSpeed }, "km/h", GRN, ACC))
    if (tB != null) {
        val nb = min(tA.size, tB.size)
        val delta = DoubleArray(nb) { tB[it] - tA[it] }
        val xs = DoubleArray(nb) { (it + 1).toDouble() }
        p.add(XY(title = "Lap Δ B−A", titleSize = 8f, xlabel = "Lap", ylabel = "Δs",
            layers = listOf(BarL(xs, delta, DoubleArray(nb) { 0.65 }, delta.map { if (it < 0) GRN else ACC }, 0.88f), zero()),
            xticks = if (nb <= 20) xs.map { Tick(it, it.toInt().toString()) } else null))
    } else {
        val xs = DoubleArray(na) { (it + 1).toDouble() }
        p.add(ax("Cumulative Race Time", "s", "Lap", listOf(LineL(xs, Np.cumsum(tA), CYN, 1.5f, marker = 5f)),
            xticks = if (na <= 20) xs.map { Tick(it, it.toInt().toString()) } else null))
    }
    val fin = avgA.filter { it.isFinite() }
    p.add(Heat("Speed Heatmap by Lap", arrayOf(if (na > 0) avgA else doubleArrayOf(0.0)), "turbo", fin.minOrNull() ?: 0.0, fin.maxOrNull() ?: 0.0,
        cellText = arrayOf(Array(na) { i -> "L${i + 1}\n${fixed(avgA[i], 0)}" }), cellColor = Color.White, cellSize = 6f, aspect = 0.62f))
    p.add(b2("Avg Brake%/Lap", brkA, bv { it.brakePct }, "%", ACC, PRP))
    if (hasGps(d)) {
        val segs = RaceLoader.lapSegments(d)
        val nShow = min(segs.size, 10)
        val lines = (0 until nShow).map { i ->
            val seg = segs[i]
            MapLine(DoubleArray(seg.last - seg.first + 1) { d["world_x"][seg.first + it] }, DoubleArray(seg.last - seg.first + 1) { d["world_z"][seg.first + it] },
                Color(Cmaps.tab10(i.toDouble() / max(nShow - 1, 1))), 1.4f, 0.85f)
        }
        p.add(MapP("Lap Lines (first $nShow)", DoubleArray(0), DoubleArray(0), null, null, lines = lines))
    } else p.add(msg("No GPS data", 10f))
    p.add(b2("Avg Throttle%/Lap", thrA, bv { it.throttlePct }, "%", GRN, YLW))
    p.add(b2("Max |Lat G|/Lap", latA, bv { it.maxLatG }, "g", PRP, ORG))
    return fig(p, 3)
}

fun Ctx.raceCorners(base: RaceLoader.Baseline): Fig {
    val d = df
    val ref = base.frame ?: return fig(listOf(TextP(null, (base.note.ifEmpty { "No baseline lap available." }).split('\n'), DIM, bg = true, size = 10f, center = true, aspect = 0.6f)), 1)
    val laps = RaceLoader.raceLapFrames(d)
    val scored = Corners.scoreLaps(laps.map { it.lap to it.frame }, ref)
    val nums = scored.refCorners.map { it.number }
    if (laps.isEmpty() || nums.isEmpty())
        return fig(listOf(TextP(null, listOf(if (laps.isNotEmpty()) "No corners found to compare." else "No complete laps in this race."), DIM, bg = true, size = 10f, center = true, aspect = 0.6f)), 1)
    val mat = Array(laps.size) { i -> DoubleArray(nums.size) { j -> scored.perLap[i].second[nums[j]]?.timeS ?: Double.NaN } }
    val finite = mat.flatMap { it.asList() }.filter { it.isFinite() }
    val lim = if (finite.isNotEmpty()) max(0.3, Np.percentile(DoubleArray(finite.size) { abs(finite[it]) }, 90.0)) else 0.3
    val txt: Array<Array<String?>>? = if (laps.size * nums.size <= 400) Array(laps.size) { i -> Array(nums.size) { j -> if (mat[i][j].isFinite()) signed(mat[i][j], 2) else null } } else null
    val heat = Heat("Time lost (red) / gained (green) per corner, s", mat, "RdYlGn_r", -lim, lim, cellText = txt, cellColor = Color(0xFF101010), cellSize = 6f,
        xLabels = nums.map { "T$it" }, yLabels = laps.map { "L${it.lap}  ${RaceLoader.fmtDur(it.timeS)}" }, aspect = (0.25f + laps.size * 0.07f).coerceIn(0.5f, 2.5f))
    val mean = DoubleArray(nums.size) { j ->
        val v = (0 until laps.size).map { mat[it][j] }.filter { it.isFinite() }
        if (v.isEmpty()) 0.0 else v.average()
    }
    val bar = XY(title = "Average per corner over the race (above 0 = slower than the baseline, s)",
        layers = listOf(BarL(DoubleArray(nums.size) { it.toDouble() }, mean, DoubleArray(nums.size) { 0.8 }, mean.map { if (it <= 0) GRN else ACC }), HLineL(0.0, FG, 0.8f, 0)),
        grid = true, gridAxis = 'y', xticks = nums.mapIndexed { i, n -> Tick(i.toDouble(), "T$n") }, aspect = 0.5f)
    return fig(listOf(heat, bar), 1, suptitle = "Corners — every lap vs ${base.label}")
}

fun Ctx.consistency(): Fig {
    val d = df
    val laps = RaceLoader.raceLapFrames(d)
    val times = laps.map { it.timeS }
    val stats = Consistency.lapTimeStats(times)
        ?: return fig(listOf(TextP(null, listOf("No complete laps in this race."), DIM, bg = true, size = 10f, center = true, aspect = 0.6f)), 1)
    val nos = DoubleArray(laps.size) { laps[it].lap.toDouble() }
    val ts = times.toDoubleArray()
    val clean = BooleanArray(ts.size).also { c -> stats.cleanIdx.forEach { c[it] = true } }
    val p = ArrayList<Panel>()
    val ci = where0(clean); val ni = clean.indices.filter { !clean[it] }.toIntArray()
    val l0 = arrayListOf<Layer>(HSpanL(stats.avg - stats.std, stats.avg + stats.std, CYN, 0.12f), HLineL(stats.avg, CYN, 0.8f, 1),
        LineL(nos, ts, DIM, 1f), ScatterL(take(nos, ci), take(ts, ci), CYN, s = 22f, z = 3), ScatterL(take(nos, ni), take(ts, ni), DIM, s = 22f, z = 3))
    if (stats.trend != null && ci.size >= 2) {
        val xs = take(nos, ci); val ys = take(ts, ci)
        val (k, b0) = Np.polyfit1(xs, ys)
        l0.add(LineL(xs, DoubleArray(xs.size) { k * xs[it] + b0 }, ORG, 1.4f))
    }
    p.add(ax("Lap time by lap (band = ±1 std)", "s", "Lap", l0, ylim = doubleArrayOf(stats.best - 0.5, stats.best * Consistency.CLEAN_LAP_FACTOR + 0.5)))
    val dev = DoubleArray(ts.size) { ts[it] - stats.avg }
    val lim = max(1.0, 3 * stats.std)
    p.add(ax("Lap vs your average (clean laps, s)", "s", "Lap", listOf(
        BarL(nos, DoubleArray(ts.size) { if (clean[it]) dev[it] else 0.0 }, DoubleArray(ts.size) { 0.8 }, dev.map { if (it <= 0) GRN else ACC }), HLineL(0.0, FG, 0.8f, 0)),
        ylim = doubleArrayOf(-lim, lim)))
    val bl = RaceLoader.bestLapBaseline(d)
    var spreads: List<Corners.CornerSpread> = emptyList()
    val blFrame = bl.frame
    if (blFrame != null) {
        val scored = Corners.scoreLaps(laps.map { it.lap to it.frame }, blFrame)
        spreads = Corners.cornerSpreads(scored.perLap)
    }
    if (spreads.isNotEmpty()) {
        val ordered = spreads.sortedBy { it.refNumber }
        val stds = ordered.map { it.stdTimeS }
        val lo = stds.min(); val hi = stds.max()
        p.add(ax("Corner time spread across laps (lower = more consistent)", "std, s", "", listOf(
            BarL(DoubleArray(ordered.size) { it.toDouble() }, stds.toDoubleArray(), DoubleArray(ordered.size) { 0.8 }, stds.map { if (it == lo) GRN else if (it == hi) ACC else CYN })),
            xticks = ordered.mapIndexed { i, c -> Tick(i.toDouble(), "T${c.refNumber}") }))
    } else p.add(TextP(null, listOf("Needs at least 3 laps with matching corners."), DIM, bg = true, size = 10f, center = true, aspect = 0.62f))
    val score = if (stats.score != null) "${stats.score}/100" else "--"
    val lines = arrayListOf("Consistency score   $score", "Clean laps          ${stats.nClean} of ${stats.n}",
        "Best / average      ${RaceLoader.fmtDur(stats.best)} / ${RaceLoader.fmtDur(stats.avg)}", "Spread (max-min)    ${fixed(stats.spread, 2)} s",
        "Std deviation       ±${fixed(stats.std, 2)} s", "Trend               ${Consistency.trendLabel(stats.trend)}")
    if (spreads.isNotEmpty()) {
        val bestC = spreads.first(); val worstC = spreads.last()
        lines.add(""); lines.add("Most consistent corner   T${bestC.refNumber}  (±${fixed(bestC.stdTimeS, 2)} s)")
        lines.add("Least consistent corner  T${worstC.refNumber}  (±${fixed(worstC.stdTimeS, 2)} s)")
        val wb = spreads.filter { it.stdBrakeM != null }
        val loose = wb.maxByOrNull { it.stdBrakeM!! }
        val looseV = loose?.stdBrakeM
        if (loose != null && looseV != null && looseV >= 1) lines.add("Brake point varies most  T${loose.refNumber}  (±${fixed(looseV, 0)} m)")
    }
    p.add(TextP("Consistency report", lines, FG, bg = false, size = 8f))
    return fig(p, 2)
}

private fun where0(m: BooleanArray) = m.indices.filter { m[it] }.toIntArray()
