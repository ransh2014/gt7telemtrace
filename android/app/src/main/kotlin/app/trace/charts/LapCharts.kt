package app.trace.charts

import androidx.compose.ui.graphics.Color
import app.trace.AP
import app.trace.telemetry.*
import kotlin.math.*

/** The 19 chart groups of the Lap Analyst, in the PC order. */
object LapCharts {
    val GROUPS = listOf("Inputs", "Engine", "Tyres", "Dynamics", "Maps", "G-Force", "Fuel", "Braking", "Sectors", "Traction",
        "Tele Diff", "Ratings", "Heat Maps", "Timeline", "Extended", "Consensus", "Style", "Corners", "Theoretical")

    class Input(
        val a: AP, val df: Frame, val dfb: Frame?, val extra: List<Extra> = emptyList(),
        val consensus: List<Map<String, Any?>>? = null, val pool: List<Pair<String, Frame>>? = null,
    )

    fun build(name: String, i: Input): Fig {
        val c = Ctx(i.a, i.df, i.dfb, false)
        return when (name) {
            "Inputs" -> c.inputs(i.extra)
            "Engine" -> c.engine()
            "Tyres" -> c.tyres()
            "Dynamics" -> c.dynamics()
            "Maps" -> c.maps()
            "G-Force" -> c.gforce()
            "Fuel" -> c.fuel()
            "Braking" -> c.braking()
            "Sectors" -> c.sectors()
            "Traction" -> c.traction()
            "Tele Diff" -> c.telediff()
            "Ratings" -> c.ratings()
            "Heat Maps" -> c.heatMaps()
            "Timeline" -> c.timeline()
            "Extended" -> c.extended()
            "Consensus" -> c.consensus(i.consensus)
            "Style" -> c.style()
            "Corners" -> c.corners()
            "Theoretical" -> c.theoretical(i.pool)
            else -> Fig(listOf(c.msg("Unknown chart group")), 1)
        }
    }
}

fun Ctx.bars2(title: String, va: DoubleArray, vb: DoubleArray?, yl: String, xlabel: String, colA: Color, colB: Color,
                      showAll: Boolean = true): XY {
    val nb = if (vb != null) min(va.size, vb.size) else va.size
    val bw = if (vb != null) 0.37 else 0.6
    val offA = if (vb != null) -bw / 2 else 0.0
    val xs = DoubleArray(nb) { (it + 1).toDouble() }
    val layers = arrayListOf<Layer>(BarL(DoubleArray(nb) { xs[it] + offA }, va.copyOf(nb), DoubleArray(nb) { bw }, listOf(colA), 0.88f, label = "A"))
    if (vb != null) layers.add(BarL(DoubleArray(nb) { xs[it] + bw / 2 }, vb.copyOf(nb), DoubleArray(nb) { bw }, listOf(colB), 0.78f, label = "B"))
    return ax(title, yl, xlabel, layers, legend = vb != null,
        xticks = if (showAll) xs.map { Tick(it, it.toInt().toString()) } else null)
}

fun Ctx.sectors(): Fig {
    val d = df
    val sa = Scores.sectorStats(d, 10)
    val na = sa.times.size
    if (sa.segs.count { it.isNotEmpty() } < 2) return fig(listOf(msg("This lap has too few samples to split into sectors")), 1)
    val sb: Scores.SectorStats? = if (dfb != null) Scores.sectorStats(dfb, 10).takeIf { s -> s.segs.count { it.isNotEmpty() } >= 2 } else null
    val p = ArrayList<Panel>()
    p.add(bars2("Sector Times", sa.times, sb?.times, "s", "Sector", CYN, ACC))
    p.add(bars2("Avg Speed/Sector", sa.avg, sb?.avg, "km/h", "Sector", CYN, ACC))
    p.add(bars2("Top Speed/Sector", sa.top, sb?.top, "km/h", "Sector", GRN, ACC))
    if (sb != null) {
        val nb = min(sa.times.size, sb.times.size)
        val delta = DoubleArray(nb) { sb.times[it] - sa.times[it] }
        val theo = (0 until nb).sumOf { min(sa.times[it], sb.times[it]) }
        val xs = DoubleArray(nb) { (it + 1).toDouble() }
        p.add(XY(title = "Sector Δ B−A  |  Theo best: ${fixed(theo, 3)}s", titleSize = 8f, xlabel = "Sector", ylabel = "Δs",
            layers = listOf(BarL(xs, delta, DoubleArray(nb) { 0.65 }, delta.map { if (it < 0) GRN else ACC }, 0.88f), zero()),
            xticks = xs.map { Tick(it, it.toInt().toString()) }))
    } else {
        val cum = Np.cumsum(sa.times)
        val xs = DoubleArray(na) { (it + 1).toDouble() }
        p.add(ax("Cumulative Time", "s", "Sector", listOf(LineL(xs, cum, CYN, 1.5f, marker = 5f)), xticks = xs.map { Tick(it, it.toInt().toString()) }))
    }
    val fin = sa.avg.filter { it.isFinite() }
    p.add(Heat("Speed Heatmap by Sector", arrayOf(sa.avg.copyOf(na)), "turbo", fin.minOrNull() ?: 0.0, fin.maxOrNull() ?: 1.0,
        cellText = arrayOf(Array(na) { i -> "S${i + 1}\n${fixed(sa.avg[i], 0)}" }), cellColor = Color.White, cellSize = 6f, aspect = 0.62f))
    p.add(bars2("Avg Brake%/Sector", sa.brk, sb?.brk, "%", "Sector", ACC, PRP))
    if (hasGps(d)) {
        val lines = ArrayList<MapLine>(); val labels = ArrayList<MapLabel>()
        for ((i, seg) in sa.segs.withIndex()) {
            val col = Color(Cmaps.tab10(i.toDouble() / max(na - 1, 1)))
            val xs = DoubleArray(seg.size) { d["world_x"][seg[it]] }; val zs = DoubleArray(seg.size) { d["world_z"][seg[it]] }
            lines.add(MapLine(xs, zs, col, 2.2f, 0.9f))
            val mid = seg.size / 2
            if (mid < seg.size) labels.add(MapLabel(xs[mid], zs[mid], (i + 1).toString(), col))
        }
        p.add(MapP("Track Sector Map", DoubleArray(0), DoubleArray(0), null, null, lines = lines, labels = labels))
    } else p.add(msg("No GPS data", 10f))
    p.add(bars2("Avg Throttle%/Sector", sa.thr, sb?.thr, "%", "Sector", GRN, YLW))
    p.add(bars2("Max |Lat G|/Sector", sa.latg, sb?.latg, "g", "Sector", PRP, ORG))
    return fig(p, 3)
}

fun Ctx.extended(): Fig {
    val d = df
    fun amax(c: String) = Np.max(DoubleArray(d.n) { abs(d[c][it]) })
    val hasExt = amax("wheel_base") > 0 || amax("sway") > 0 || amax("current_lap_ms") > 0
    if (!hasExt) return fig(List(9) { msg("No extended (B/~/C) data\nin this lap — recorded with\nheartbeat 'A' only", 10f) }, 3)
    val b = dfb
    val p = ArrayList<Panel>()
    p.add(ax("Sway", "m/s²", layers = line(d, "sway", PRP) + lb(b, "sway")))
    p.add(ax("Heave", "m/s²", layers = line(d, "heave", CYN) + lb(b, "heave")))
    p.add(ax("Surge", "m/s²", layers = line(d, "surge", YLW) + lb(b, "surge")))
    p.add(ax("Wheel Steering Angle", "rad", layers = listOf(plot(d, "wheel_steering_angle_l", FL, "Front L", 1.3f), plot(d, "wheel_steering_angle_r", FR, "Front R", 1.3f), zero()) +
        lb(b, "wheel_steering_angle_l") + lb(b, "wheel_steering_angle_r"), legend = true))
    p.add(ax("Live Current Lap", "ms", layers = line(d, "current_lap_ms", GRN) + lb(b, "current_lap_ms")))
    p.add(ax("Energy Recovery", null, layers = line(d, "energy_recovery", ORG) + lb(b, "energy_recovery")))
    val surf = d.str("surface_type")
    if (hasGps(d) && surf.maxOf { it.length } > 0) {
        val map = mapOf('T' to 0, 'C' to 1, 'D' to 2, 'G' to 3, 'S' to 4, 's' to 5)
        val code = DoubleArray(d.n) { (surf[it].firstOrNull()?.let { ch -> map[ch] } ?: 0).toDouble() }
        p.add(MapP("Surface (FL) — 0=Tarmac 1=Kerb 2=Dirt 3=Grass 4=Sand 5=Snow", d["world_x"], d["world_z"], code, "tab10"))
    } else p.add(msg("No surface/GPS data", 9f))
    val cats = d.str("car_category").filter { it.isNotEmpty() }
    if (cats.isNotEmpty()) {
        val vc = cats.groupingBy { it }.eachCount().entries.sortedByDescending { it.value }
        p.add(ax("Car Category (samples)", "Count", xl = "", layers = listOf(BarL(DoubleArray(vc.size) { it.toDouble() }, DoubleArray(vc.size) { vc[it].value.toDouble() },
            DoubleArray(vc.size) { 0.8 }, listOf(CYN), 0.85f)), grid = false, xticks = vc.mapIndexed { i, e -> Tick(i.toDouble(), e.key) }))
    } else p.add(msg("No category data", 9f))
    val wb = d["wheel_base"].filter { it > 0 }
    p.add(TextP(null, if (wb.isNotEmpty()) listOf("Wheelbase:", "${fixed(wb.average(), 3)} m") else listOf("No wheelbase data"), FG, bg = false, size = 12f, center = true, aspect = 0.62f))
    return fig(p, 3)
}

fun Ctx.consensus(rows: List<Map<String, Any?>>?): Fig {
    val d = df
    if (rows.isNullOrEmpty()) return fig(List(4) { msg("No community line loaded yet\n(LEADERBOARD panel → Load Community Line)", 10f) }, 2)
    val bx = DoubleArray(rows.size) { numOf(rows[it]["bucket_start"]) }
    fun cy(k: String) = DoubleArray(rows.size) { numOf(rows[it][k]) }
    val p = ArrayList<Panel>()
    p.add(ax("Speed vs Community", "km/h", layers = listOf(plot(d, "speed_kmh", CYN, "You"), LineL(bx, cy("avg_speed"), YLW, 1.8f, dash = 1, label = "Community")), legend = true))
    p.add(ax("Throttle vs Community", "0–1", layers = listOf(plot(d, "throttle", GRN, "You"), LineL(bx, cy("avg_throttle"), YLW, 1.8f, dash = 1, label = "Community")), legend = true))
    p.add(ax("Brake vs Community", "0–1", layers = listOf(plot(d, "brake", ACC, "You"), LineL(bx, cy("avg_brake"), YLW, 1.8f, dash = 1, label = "Community")), legend = true))
    val nLaps = numOf(rows[0]["lap_count"]).toInt()
    p.add(XY(layers = listOf(TextL(0.5, 0.6, "Consensus from\n$nLaps top laps", FG, 13f, axes = true),
        TextL(0.5, 0.3, "${rows.size} track-position buckets (10m each)", DIM, 9f, axes = true)), off = true))
    return fig(p, 2)
}

private fun fmtSpec(v: Double?, width: Int, dec: Int, plus: Boolean, unit: String): String =
    if (v == null) "--".padStart(width + unit.length) else (if (plus) signed(v, dec) else fixed(v, dec)).padStart(width) + unit

fun Ctx.corners(): Fig {
    val d = df
    val cs: List<Corners.Corner>; val deltas: List<Corners.CornerDelta>
    if (dfb != null) { val m = Corners.matchCorners(d, dfb); cs = m.a; deltas = m.deltas } else { cs = Corners.detectCorners(d); deltas = emptyList() }
    val label = Corners.sessionLabel(d, cs, deltas)
    val p = ArrayList<Panel>()
    if (deltas.isNotEmpty()) {
        val nums = deltas.map { it.number }
        val vals = DoubleArray(deltas.size) { deltas[it].timeS ?: 0.0 }
        p.add(XY(title = "Time vs reference per corner (above 0 = lost, s)", xlabel = "Corner",
            layers = listOf(BarL(DoubleArray(nums.size) { it.toDouble() }, vals, DoubleArray(nums.size) { 0.8 }, vals.map { if (it <= 0) GRN else ACC }), HLineL(0.0, FG, 0.8f, 0)),
            grid = true, gridAxis = 'y', xticks = nums.mapIndexed { i, n -> Tick(i.toDouble(), n.toString()) }, xTickSize = 8f, aspect = 0.5f))
    } else {
        val m = if (cs.isEmpty()) "No corners found on this lap." else "Turn Compare ON and load your PB as lap B\nto score each corner against it."
        p.add(TextP(null, m.split('\n'), DIM, bg = true, size = 10f, center = true, aspect = 0.4f))
    }
    val lines = ArrayList<String>()
    if (deltas.isNotEmpty()) {
        lines.add(" #   Brake pt     Min speed      Throttle-on     Time")
        for (x in deltas) lines.add("${x.number.toString().padStart(2)}  ${fmtSpec(x.brakeM, 7, 0, true, " m")}  ${fmtSpec(x.minSpeedKmh, 7, 0, true, " km/h")}" +
            "  ${fmtSpec(x.throttleM, 7, 0, true, " m")}  ${fmtSpec(x.timeS, 7, 2, true, " s")}")
        lines.addAll(listOf("", Corners.describeTimeLoss(deltas), "", "Brake pt +: braked later than reference.  Throttle-on −: back on power earlier.",
            "Min speed +: more speed through the corner."))
    } else {
        lines.add(" #   Brake at     Min speed      Throttle at")
        for (c in cs) lines.add("${c.number.toString().padStart(2)}  ${fmtSpec(c.brakePos, 7, 0, false, " m")}  ${fmtSpec(c.minSpeed, 7, 0, false, " km/h")}  ${fmtSpec(c.throttlePos, 7, 0, false, " m")}")
    }
    p.add(TextP(null, lines, FG, bg = true, size = 8f))
    return fig(p, 1, suptitle = if (label.isNotEmpty()) "Corners — $label" else "Corners")
}

fun Ctx.style(): Fig {
    val d = df; val b = dfb
    val sa = Scores.styleMetrics(d)
    val sbm = if (b != null) Scores.styleMetrics(b) else null
    val p = ArrayList<Panel>()
    val labels = if (b != null) listOf("A", "B") else listOf("A")
    val vals = if (sbm != null) doubleArrayOf(sa.aggression, sbm.aggression) else doubleArrayOf(sa.aggression)
    val cols = vals.map { if (it < 33) GRN else if (it < 66) YLW else ACC }
    val l1 = arrayListOf<Layer>(BarL(DoubleArray(vals.size) { it.toDouble() }, vals, DoubleArray(vals.size) { 0.5 }, cols, 1f, horizontal = true))
    for ((i, v) in vals.withIndex()) l1.add(TextL(v + 2, i.toDouble(), "${fixed(v, 0)} — ${Scores.styleLabel(v)}", FG, 8f, ha = 0f, va = 0.5f))
    p.add(XY(title = "Driving Style (Smooth ↔ Aggressive)", xlabel = "Aggression index (0-100)", layers = l1, xlim = doubleArrayOf(0.0, 130.0),
        ylim = doubleArrayOf(-0.5, vals.size - 0.5), yticks = labels.mapIndexed { i, s -> Tick(i.toDouble(), s) }, grid = true, gridAxis = 'x', xTickSize = 8f))
    val showB = b != null && !b.compact
    val bvals = if (showB && sbm != null) doubleArrayOf(sa.balance, sbm.balance) else doubleArrayOf(sa.balance)
    val bcols = bvals.map { if (it > 0.015) ACC else if (it < -0.015) CYN else DIM }
    val l2 = arrayListOf<Layer>(BarL(DoubleArray(bvals.size) { it.toDouble() }, bvals, DoubleArray(bvals.size) { 0.5 }, bcols, 1f, horizontal = true), VLineL(0.0, FG, 0.8f, 0))
    for ((i, v) in bvals.withIndex()) l2.add(TextL(v + (if (v >= 0) 0.002 else -0.002), i.toDouble(), Scores.balanceLabel(v), FG, 8f, ha = if (v >= 0) 0f else 1f, va = 0.5f))
    val lim = max(0.05, bvals.maxOf { abs(it) } * 1.6)
    p.add(XY(title = "Cornering Balance (← Understeer | Oversteer →)", xlabel = "Rear − front slip while cornering", layers = l2, xlim = doubleArrayOf(-lim, lim),
        ylim = doubleArrayOf(-0.5, bvals.size - 0.5), yticks = labels.take(bvals.size).mapIndexed { i, s -> Tick(i.toDouble(), s) }, grid = true, gridAxis = 'x', xTickSize = 8f))
    val l3 = arrayListOf<Layer>()
    if (sa.cornerRows.size > 10) l3.add(ScatterL(take(d["track_position"], sa.cornerRows), sa.rearMinusFront, CYN, s = 6f, alpha = 0.6f, label = "A"))
    if (sbm != null && b != null && sbm.cornerRows.size > 10) l3.add(ScatterL(take(b["track_position"], sbm.cornerRows), sbm.rearMinusFront, ACC, s = 6f, alpha = 0.5f, label = "B"))
    l3.add(HLineL(0.0, DIM, 0.7f, 0))
    p.add(ax("Balance Through Corners", "Rear − front slip", "Track Position (m)", l3, legend = b != null))
    val bcol = if (sa.balance > 0.015) ACC else if (sa.balance < -0.015) CYN else FG
    p.add(XY(title = "Style Summary", off = true, layers = listOf(
        TextL(0.5, 0.75, Scores.styleLabel(sa.aggression), CYN, 20f, axes = true, bold = true),
        TextL(0.5, 0.55, "Aggression ${fixed(sa.aggression, 0)}/100", FG, 9f, axes = true),
        TextL(0.5, 0.35, Scores.balanceLabel(sa.balance), bcol, 14f, axes = true, bold = true),
        TextL(0.5, 0.18, "Lap A summary", DIM, 8f, axes = true))))
    return fig(p, 2)
}

fun Ctx.theoretical(pool: List<Pair<String, Frame>>?): Fig {
    val laps = pool ?: (listOf("Lap A" to df) + (if (dfb != null) listOf("Lap B" to dfb) else emptyList()))
    val r = Theoretical.theoreticalBest(laps)
        ?: return fig(listOf(TextP(null, listOf("Needs at least two complete laps of this car on this track.",
            "Drive more laps in the Live Dashboard, or load a second lap as Lap B."), DIM, bg = true, size = 10f, center = true, aspect = 0.8f)), 1)
    val palette = listOf(CYN, ACC, YLW, GRN, PRP, ORG, Color(0xFF8899FF), Color(0xFFFF9ECB))
    val cen = r.centers
    val p = ArrayList<Panel>()
    p.add(ax("Time left on the table along the lap (s)", "s", layers = listOf(
        FillL(cen, r.cumGap, DoubleArray(cen.size), ACC, 0.25f, Step.MID), LineL(cen, r.cumGap, ACC, 1.4f, step = Step.MID))))
    val zones = r.zones
    val zl = ArrayList<Layer>()
    if (zones.isNotEmpty()) zl.add(BarL(DoubleArray(zones.size) { (zones[it].start + zones[it].end) / 2 }, DoubleArray(zones.size) { zones[it].loss },
        DoubleArray(zones.size) { (zones[it].end - zones[it].start) * 0.9 }, zones.indices.map { if (it < 3) ACC else DIM }))
    p.add(ax("Where your best lap loses time (per ~100 m zone, s)", "s", layers = zl))
    val step = if (cen.size > 1) cen[1] - cen[0] else 1.0
    val dl = arrayListOf<Layer>(BarL(cen, DoubleArray(cen.size) { 1.0 }, DoubleArray(cen.size) { step }, r.donor.map { palette[it % palette.size] }))
    for ((i, lab) in r.labels.withIndex()) dl.add(BarL(DoubleArray(0), DoubleArray(0), DoubleArray(0), listOf(palette[i % palette.size]), label = lab.take(28)))
    p.add(XY(title = "Which lap holds the fastest time in each part of the track", xlabel = XL, layers = dl, legend = true, legendCols = 2, legendSize = 6f,
        legendBelow = true, noYTicks = true, aspect = 0.8f))
    val best = r.labels[r.bestIdx]
    val lines = arrayListOf("Best real lap   ${LapMath.fmtTime(r.actual)}   (${best.take(30)})", "Theoretical     ${LapMath.fmtTime(r.theoretical)}",
        "On the table    ${fixed(r.gap, 3)} s", "Laps used       ${r.labels.size}")
    if (r.skipped > 0) lines.add("Left out        ${r.skipped} (incomplete or a different length)")
    lines.add("")
    if (zones.isNotEmpty()) {
        lines.add("Held back most at:")
        for (z in zones.take(3)) lines.add("  ${fixed(z.start, 0).padStart(5)}-${fixed(z.end, 0).padEnd(5)} m   +${fixed(z.loss, 3)} s   (${r.labels[z.donor].take(22)} is quicker)")
    } else lines.add("Your best lap is already the quickest everywhere.")
    p.add(TextP("Theoretical best lap", lines, FG, bg = false, size = 8f))
    return fig(p, 2)
}
