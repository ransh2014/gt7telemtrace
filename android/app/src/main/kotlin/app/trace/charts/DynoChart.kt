package app.trace.charts

import androidx.compose.ui.graphics.Color
import app.trace.telemetry.*
import kotlin.math.*

/** Inputs the Dyno & Tune group needs beyond the telemetry frame: gear ratios, tyre radius and the car's weight. */
class DynoIn(
    val ratiosA: List<Double>, val tyreA: Double?, val massA: Double?,
    val ratiosB: List<Double> = emptyList(), val tyreB: Double? = null, val massB: Double? = null,
)

private fun wrap(text: String, width: Int, first: String = "", rest: String = ""): List<String> {
    val out = ArrayList<String>(); var cur = first
    for (w in text.split(' ')) {
        if (cur.trim().isEmpty() || cur.length + 1 + w.length <= width) cur += (if (cur.isEmpty() || cur == first && first.isNotEmpty() || cur.endsWith(" ")) "" else " ") + w
        else { out.add(cur); cur = rest + w }
    }
    if (cur.isNotBlank()) out.add(cur)
    return out
}

/** draw_dyno: power and torque curve, thrust per gear, engine and gearbox table, tuning hints. */
fun Ctx.dynoFig(inp: DynoIn?): Fig {
    if (df.compact) return fig(listOf(msg("A leaderboard ghost only carries driver inputs.\nLoad one of your own saved laps.")), 1)
    val res = Dyno.analyse(df, inp?.ratiosA, inp?.massA, inp?.tyreA)
    if (!res.ok) return fig(listOf(msg("Can't build the dyno from this lap.\n\n" + wrap(res.msg, 70).joinToString("\n"))), 1)
    val resb = dfb?.takeIf { !it.compact }?.let { Dyno.analyse(it, inp?.ratiosB, inp?.massB, inp?.tyreB) }?.takeIf { it.ok }
    val palette = listOf(CYN, ACC, YLW, GRN, PRP, ORG, Color(0xFF96949c), Color(0xFFf2f0ec))
    val panels = ArrayList<Panel>()

    val pl = arrayListOf<Layer>(
        FillL(res.rpm, res.q1Disp, res.q3Disp, ACC, 0.15f),
        LineL(res.rpm, res.pDisp, ACC, 2f, label = "Power (${res.unitP})"),
        LineL(doubleArrayOf(res.peakRpm), doubleArrayOf(res.peakPDisp), ACC, 0f, marker = 4f),
        TextL(res.peakRpm, res.peakPDisp, "${fixed(res.peakPDisp, 0)} ${res.unitP} @ ${fixed(res.peakRpm, 0)}", ACC, 8f, ha = 0f, va = 1f),
    )
    if (resb != null) pl.add(LineL(resb.rpm, resb.pDisp, CYN, 1.6f, dash = 1, label = "Power, Lap B"))
    val tl = arrayListOf<Layer>(LineL(res.rpm, res.tDisp, YLW, 1.5f, label = "Torque (${res.unitT})"))
    if (resb != null) tl.add(LineL(resb.rpm, resb.tDisp, YLW, 1.2f, alpha = 0.6f, dash = 1))
    panels.add(ax("Power & torque at the wheels (shaded = spread of the samples)", res.unitP, "Engine rpm", pl, legend = true,
        twin = tl, twinLabel = res.unitT, twinColor = YLW, ylim = doubleArrayOf(0.0, (res.pDisp.max() * 1.15))))

    val gl = ArrayList<Layer>()
    for ((g, c) in res.curves.toSortedMap()) gl.add(LineL(c.first, c.second, palette[(g - 1) % palette.size], 1.3f, label = g.toString()))
    for (gd in res.gears) gd.optShift?.let { r ->
        val vs = r / gd.k
        gl.add(LineL(doubleArrayOf(vs * 3.6), doubleArrayOf(res.pEdge(r) / vs / Dyno.G), FG, 0f, marker = 3.5f))
    }
    panels.add(ax("Pull in each gear: dots = best shift points", "thrust (g)", "Speed (km/h)", gl, legend = true, legendCols = 2))

    val top = res.top
    val lines = arrayListOf(
        "Peak power    ${fixed(res.peakPDisp, 0)} ${res.unitP} at ${fixed(res.peakRpm, 0)} rpm" + (if (res.mass != null) "   (${fixed(res.hpPerTonne, 0)} hp/t)" else ""),
        "Peak torque   ${fixed(res.peakTDisp, 0)} ${res.unitT} at ${fixed(res.peakTqRpm, 0)} rpm",
        "Rev limiter   ~${fixed(res.revLim, 0)} rpm",
        "True top speed ${fixed(top.kmh, 0)} km/h  (limited by ${top.by}, gear ${top.gear})",
        if (res.mass != null) "" else "Enter the car's weight to see real hp and Nm.", "",
        "Gear  Ratio  km/h/1000  Top km/h  Lap%   Shift: best  yours",
    )
    for (gd in res.gears) {
        val star = if (gd.src != "measured") "*" else " "
        lines.add(gd.g.toString().padStart(3) + star + "  " + (gd.ratio?.let { fixed(it, 3) } ?: "-").padStart(6) + "  " + fixed(gd.kmhPer1000, 1).padStart(8) + "  " +
            fixed(gd.topKmh, 0).padStart(8) + "  " + fixed(gd.share * 100, 0).padStart(4) + "   " + (gd.optShift?.let { fixed(it, 0) } ?: "-").padStart(10) + "  " + (gd.yourShift?.let { fixed(it, 0) } ?: "-").padStart(5))
    }
    val rs = res.resist
    lines.addAll(listOf("", "Drag model ${rs.src}  (rolling ${fixed(rs.c0, 3)}, aero ${fixed(rs.c1 * 1e4, 2)}e-4)",
        "Built from ${res.nSamples} clean full-throttle samples. * = gear inferred from its ratio."))
    val fds = res.gears.mapNotNull { it.finalDrive }
    if (fds.isNotEmpty()) lines.add("Final drive ~${fixed(Np.median(fds.toDoubleArray()), 2)}")
    panels.add(TextP("Engine & gearbox", lines, FG, size = 7f))

    val hints = Dyno.advise(df, res)
    val out = ArrayList<String>()
    if (resb != null) {
        val dp = (resb.peakWkg / res.peakWkg - 1.0) * 100.0
        out.addAll(listOf("Lap B vs Lap A: peak power-to-weight ${signed(dp, 1)}%,", "peak at ${fixed(resb.peakRpm, 0)} rpm (A: ${fixed(res.peakRpm, 0)}).", ""))
    }
    if (hints.isNotEmpty()) for (h in hints.take(8)) {
        val tag = if (h.sev >= 3) "!!!" else if (h.sev == 2) "!!" else "!"
        out.addAll(wrap("[$tag] ${h.area}: ${h.finding}", 66)); out.addAll(wrap("-> " + h.fix, 66, "    ", "       ")); out.add("")
    } else out.add("Nothing stands out: balance, gearing and shift points look healthy for this lap.")
    out.addAll(wrap("Change one thing at a time, then drive again. These point a direction; the right amount depends on the car. (GTPlanet, Coach Dave, racinggames.gg)", 66))
    panels.add(TextP("Tuning hints from this lap", out.take(30), FG, size = 7f))
    return fig(panels, 2)
}
