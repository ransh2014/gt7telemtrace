package app.trace.charts

import androidx.compose.ui.graphics.Color
import app.trace.telemetry.*
import java.util.Locale

/** Per-lap inputs the Dyno & Tune group needs beyond the telemetry: gear ratios and the car's weight (kg, if known). */
class DynoIn(val gearsA: List<Double>, val massA: Double?, val gearsB: List<Double>?, val massB: Double?)

private fun f(fmt: String, vararg a: Any?) = String.format(Locale.US, fmt, *a)

/** textwrap.wrap look-alike (greedy, breaks on spaces). */
internal fun wrapText(text: String, width: Int, initial: String = "", subsequent: String = ""): List<String> {
    val out = ArrayList<String>()
    var line = StringBuilder(initial)
    var empty = true
    for (w in text.split(' ').filter { it.isNotEmpty() }) {
        if (!empty && line.length + 1 + w.length > width) {
            out.add(line.toString()); line = StringBuilder(subsequent); empty = true
        }
        if (!empty) line.append(' ')
        line.append(w); empty = false
    }
    if (!empty) out.add(line.toString())
    return out
}

fun Ctx.dyno(din: DynoIn?): Fig {
    if (df.compact) return fig(listOf(msg("A leaderboard ghost only carries driver inputs.\nLoad one of your own saved laps.")), 1)
    val gr = din?.gearsA ?: emptyList()
    val res = Dyno.analyse(df, gr, din?.massA, null)
    if (!res.ok) return fig(listOf(msg("Can't build the dyno from this lap.\n\n" + wrapText(res.msg, 70).joinToString("\n"))), 1)
    val resb = if (dfb != null && !dfb.compact) Dyno.analyse(dfb, din?.gearsB ?: emptyList(), din?.massB, null).takeIf { it.ok } else null
    val palette = listOf(CYN, ACC, YLW, GRN, PRP, ORG, CLUTCH, FG)
    val p = ArrayList<Panel>()

    // power + torque (+ Lap B dashed)
    val r = res.rpm
    val power = ArrayList<Layer>()
    power.add(FillL(r, res.q1Disp, res.q3Disp, ACC, 0.15f))
    power.add(LineL(r, res.pDisp, ACC, 2.0f, label = "Power (${res.unitP})"))
    power.add(ScatterL(doubleArrayOf(res.peakRpm), doubleArrayOf(res.peakPDisp), ACC, s = 5f))
    power.add(TextL(res.peakRpm, res.peakPDisp, f("%.0f %s @ %.0f", res.peakPDisp, res.unitP, res.peakRpm), ACC, 8f, ha = 0f, va = 1f))
    if (resb != null) power.add(LineL(resb.rpm, resb.pDisp, CYN, 1.6f, dash = 1, label = "Power, Lap B"))
    val tq = ArrayList<Layer>()
    tq.add(LineL(r, res.tDisp, YLW, 1.5f, label = "Torque (${res.unitT})"))
    if (resb != null) tq.add(LineL(resb.rpm, resb.tDisp, YLW, 1.2f, 0.6f, dash = 1))
    p.add(ax("Power & torque at the wheels (shaded = spread of the samples)", res.unitP, "Engine rpm", power,
        legend = true, twin = tq, twinLabel = res.unitT, twinColor = YLW))

    // pull in each gear
    val pull = ArrayList<Layer>()
    for ((g, c) in res.curves.toSortedMap()) pull.add(LineL(c.first, c.second, palette[(g - 1) % palette.size], 1.3f, label = g.toString()))
    val dx = ArrayList<Double>(); val dy = ArrayList<Double>()
    for (gd in res.gears) {
        val os = gd.optShift ?: continue
        if (os == 0.0) continue
        val vs = os / gd.k
        dx.add(vs * 3.6); dy.add(res.pEdge(os) / vs / Dyno.G)
    }
    if (dx.isNotEmpty()) pull.add(ScatterL(dx.toDoubleArray(), dy.toDoubleArray(), FG, s = 5f, z = 2))
    p.add(ax("Pull in each gear: dots = best shift points", "thrust (g)", "Speed (km/h)", pull, legend = true, legendCols = 2, legendSize = 6f))

    // gearbox table
    val top = res.top
    val lines = ArrayList<String>()
    lines.add(f("Peak power    %.0f %s at %.0f rpm", res.peakPDisp, res.unitP, res.peakRpm) + (if (res.mass != null) f("   (%.0f hp/t)", res.hpPerTonne) else ""))
    lines.add(f("Peak torque   %.0f %s at %.0f rpm", res.peakTDisp, res.unitT, res.peakTqRpm))
    lines.add(f("Rev limiter   ~%.0f rpm", res.revLim))
    if (top != null) lines.add(f("True top speed %.0f km/h  (limited by %s, gear %d)", top.kmh, top.by, top.gear))
    lines.add(if (res.mass != null) "" else "Enter the car's weight to see real hp and Nm.")
    lines.add("")
    lines.add("Gear  Ratio  km/h/1000  Top km/h  Lap%   Shift: best  yours")
    for (gd in res.gears) {
        val ratio = if (gd.ratio != null && gd.ratio != 0.0) f("%.3f", gd.ratio) else "-"
        val best = if (gd.optShift != null && gd.optShift != 0.0) f("%.0f", gd.optShift) else "-"
        val mine = if (gd.yourShift != null && gd.yourShift != 0.0) f("%.0f", gd.yourShift) else "-"
        val star = if (gd.src != "measured") "*" else " "
        lines.add(f("%3d%s  %6s  %8.1f  %8.0f  %4.0f   %10s  %5s", gd.g, star, ratio, gd.kmhPer1000, gd.topKmh, gd.share * 100, best, mine))
    }
    val rs = res.resist
    lines.add("")
    lines.add(f("Drag model %s  (rolling %.3f, aero %.2fe-4)", rs.src, rs.c0, rs.c1 * 1e4))
    lines.add("Built from ${res.nSamples} clean full-throttle samples. * = gear inferred from its ratio.")
    val fds = res.gears.mapNotNull { it.finalDrive }.sorted()
    if (fds.isNotEmpty()) {
        val med = if (fds.size % 2 == 1) fds[fds.size / 2] else (fds[fds.size / 2 - 1] + fds[fds.size / 2]) / 2
        lines.add(f("Final drive ~%.2f", med))
    }
    p.add(TextP("Engine & gearbox", lines, FG, bg = true, size = 7f))

    // tuning hints
    val hints = Dyno.advise(df, res, gr)
    val out = ArrayList<String>()
    if (resb != null) {
        val dp = (resb.peakWkg / res.peakWkg - 1.0) * 100.0
        out.add(f("Lap B vs Lap A: peak power-to-weight %+.1f%%,", dp))
        out.add(f("peak at %.0f rpm (A: %.0f).", resb.peakRpm, res.peakRpm))
        out.add("")
    }
    if (hints.isNotEmpty()) {
        for (h in hints.take(8)) {
            val tag = if (h.sev >= 3) "!!!" else if (h.sev == 2) "!!" else "!"
            out.addAll(wrapText("[$tag] ${h.area}: ${h.finding}", 66))
            out.addAll(wrapText("-> " + h.fix, 66, "    ", "       "))
            out.add("")
        }
    } else out.add("Nothing stands out: balance, gearing and shift points look healthy for this lap.")
    out.addAll(wrapText("Change one thing at a time, then drive again. These point a direction; the right amount depends on the car. (GTPlanet, Coach Dave, racinggames.gg)", 66))
    p.add(TextP("Tuning hints from this lap", out.take(30), FG, bg = true, size = 7f))
    return fig(p, 1)
}
