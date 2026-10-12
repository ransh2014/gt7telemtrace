package app.trace.charts

import androidx.compose.ui.graphics.Color
import app.trace.AP
import app.trace.telemetry.*
import kotlin.math.*

/** Everything a chart group needs: the palette, lap/race A (and B when Compare is on) and the X axis in use. */
open class Ctx(val a: AP, val df: Frame, val dfb: Frame?, val race: Boolean) {
    val ACC = a.acc; val CYN = a.cyn; val GRN = a.grn; val YLW = a.yel; val ORG = a.org; val PRP = a.prp
    val FG = a.fg; val DIM = a.dim; val DIM2 = a.dim2
    val CLUTCH = Color(0xFF8899FF)
    val X = if (race) "t" else "track_position"
    val XL = if (race) "Race Time (s)" else "Track Pos (m)"
    val FL get() = ACC; val FR get() = YLW; val RL get() = GRN; val RR get() = CYN

    fun wd(lap: Float, rc: Float) = if (race) rc else lap
    fun f(lap: Float, rc: Float) = if (race) rc else lap

    fun has(d: Frame?, c: String) = d != null && d.has(c)

    // ── layers ───────────────────────────────────────────────────────────────
    fun line(d: Frame, y: String, col: Color, lbl: String? = null, lw: Float = 1.5f, fill: Boolean = false, step: Boolean = false): List<Layer> {
        val xs = d[X]; val ys = d[y]
        val st = if (step) Step.POST else Step.NONE
        val out = ArrayList<Layer>()
        out.add(LineL(xs, ys, col, lw, label = lbl, step = st))
        if (fill) out.add(FillL(xs, ys, DoubleArray(xs.size), col, 0.12f, st))
        return out
    }

    /** Overlay lap/race B: dashed accent line. */
    fun lb(d: Frame?, y: String, lw: Float = 1.2f, alpha: Float = 0.75f): List<Layer> =
        if (d == null || !d.has(X) || !d.has(y)) emptyList() else listOf(LineL(d[X], d[y], ACC, lw, alpha, dash = 1))

    fun lbStep(d: Frame?, y: String, lw: Float = 1.2f, alpha: Float = 0.75f): List<Layer> =
        if (d == null || !d.has(X) || !d.has(y)) emptyList() else listOf(LineL(d[X], d[y], ACC, lw, alpha, dash = 1, step = Step.POST))

    fun plot(d: Frame, y: String, col: Color, lbl: String? = null, lw: Float = 1.5f, alpha: Float = 1f, dash: Int = 0): Layer =
        LineL(d[X], d[y], col, lw, alpha, dash, label = lbl)

    fun plotV(d: Frame, v: DoubleArray, col: Color, lbl: String? = null, lw: Float = 1.5f, alpha: Float = 1f, dash: Int = 0): Layer =
        LineL(d[X], v, col, lw, alpha, dash, label = lbl)

    fun zero(color: Color = DIM) = HLineL(0.0, color, 0.8f, 1)

    fun scaled(a: DoubleArray, k: Double) = DoubleArray(a.size) { a[it] * k }

    /** The standard `_ax(...)`: title, x label, y label, light grid. */
    fun ax(title: String?, yl: String? = null, xl: String? = XL, layers: List<Layer>, grid: Boolean = true,
           legend: Boolean = false, twin: List<Layer> = emptyList(), twinLabel: String? = null, twinColor: Color? = null,
           ylabelColor: Color? = null, xlim: DoubleArray? = null, ylim: DoubleArray? = null, cb: Cb? = null,
           xticks: List<Tick>? = null, yticks: List<Tick>? = null, aspect: Float = 0.62f, legendSize: Float = 7f,
           legendCols: Int = 1, gridAxis: Char = 'b') =
        XY(title = title, xlabel = if (xl.isNullOrEmpty()) null else xl, ylabel = if (yl.isNullOrEmpty()) null else yl,
            layers = layers, twin = twin, twinLabel = twinLabel, twinColor = twinColor, grid = grid, gridAxis = gridAxis,
            xlim = xlim, ylim = ylim, xticks = xticks, yticks = yticks, legend = legend, legendSize = legendSize,
            legendCols = legendCols, cb = cb, ylabelColor = ylabelColor, aspect = aspect)

    fun msg(text: String, size: Float = 11f, aspect: Float = 0.62f) =
        TextP(null, text.split('\n'), DIM, bg = false, size = size, center = true, aspect = aspect)

    fun hasGps(d: Frame) = d.has("world_x") && Np.max(DoubleArray(d.n) { abs(d["world_x"][it]) }) > 1

    fun hist(v: DoubleArray, bins: Int, col: Color, alpha: Float, label: String? = "A", outline: Boolean = false, lw: Float = 1.5f,
             density: Boolean = false, edges: DoubleArray? = null, rwidth: Float = 1f) =
        HistL(v, bins, col, alpha, edges, outline, lw, density, rwidth, label)

    fun scatter(x: DoubleArray, y: DoubleArray, col: Color, s: Float, alpha: Float, label: String? = null, z: Int = 1) =
        ScatterL(x, y, col, s = s, alpha = alpha, label = label, z = z)

    fun absA(a: DoubleArray) = DoubleArray(a.size) { abs(a[it]) }
    fun clip(a: DoubleArray, lo: Double, hi: Double) = Np.clip(a, lo, hi)
    fun col(d: Frame, c: String) = d[c]

    fun circle(r: Double, col: Color, lw: Float, alpha: Float = 1f, n: Int = 100): Layer {
        val th = Np.linspace(0.0, 2 * PI, n)
        return LineL(DoubleArray(n) { cos(th[it]) * r }, DoubleArray(n) { sin(th[it]) * r }, col, lw, alpha, dash = 1)
    }

    fun where(d: Frame, mask: (Int) -> Boolean) = (0 until d.n).filter(mask).toIntArray()
    fun take(a: DoubleArray, idx: IntArray) = DoubleArray(idx.size) { a[idx[it]] }

    fun fig(panels: List<Panel>, cols: Int, suptitle: String? = null, footer: String? = null) = Fig(panels, cols, suptitle, footer)

    // ── groups shared by the Lap and Race analysts ───────────────────────────
    fun inputs(extra: List<Extra> = emptyList()): Fig {
        val d = df; val b = dfb
        val p = ArrayList<Panel>()
        fun withExtra(base: List<Layer>, y: String, withLabel: Boolean): List<Layer> {
            val o = ArrayList(base)
            for (e in extra) if (e.df.has(y) && e.df.has(X)) o.add(LineL(e.df[X], e.df[y], e.color, 1.1f, 0.75f, dash = 2, label = if (withLabel) e.label else null))
            return o
        }
        p.add(ax("Speed", "km/h", layers = withExtra(line(d, "speed_kmh", CYN, fill = true) + lb(b, "speed_kmh"), "speed_kmh", true), legend = extra.isNotEmpty(), legendSize = 6f))
        p.add(ax("Throttle", "0–1", layers = withExtra(line(d, "throttle", GRN, fill = true) + lb(b, "throttle"), "throttle", false)))
        p.add(ax("Brake", "0–1", layers = withExtra(line(d, "brake", ACC, fill = true) + lb(b, "brake"), "brake", false)))
        val tb = arrayListOf<Layer>(LineL(d[X], scaled(d["throttle"], 100.0), GRN, 1.5f, label = "Thr A"),
            LineL(d[X], scaled(d["brake"], 100.0), ACC, 1.5f, label = "Brk A"))
        if (b != null) {
            tb.add(LineL(b[X], scaled(b["throttle"], 100.0), GRN, 1.2f, 0.65f, 1, label = "Thr B"))
            tb.add(LineL(b[X], scaled(b["brake"], 100.0), ACC, 1.2f, 0.65f, 1, label = "Brk B"))
        }
        p.add(ax("Throttle + Brake", "%", layers = tb, legend = true, legendSize = if (b != null) 6f else 7f))
        p.add(ax("Gear", "Gear", layers = line(d, "gear", YLW, step = true, fill = true) + lbStep(b, "gear")))
        p.add(ax("Steering", null, layers = line(d, "steering", PRP) + zero() + lb(b, "steering") + withExtra(emptyList(), "steering", false)))
        p.add(ax("Clutch", "0–1", layers = line(d, "clutch", CLUTCH, fill = true) + lb(b, "clutch")))
        val sh = arrayListOf<Layer>(hist(d["speed_kmh"], 40, CYN, 0.85f))
        if (b != null) sh.add(hist(b["speed_kmh"], 40, ACC, 0.55f, "B", outline = true))
        p.add(ax("Speed Distribution", "Count", xl = "km/h", layers = sh, grid = false, legend = b != null))
        val sc = arrayListOf<Layer>(scatter(scaled(d["throttle"], 100.0), d["speed_kmh"], GRN, 1f, f(0.35f, 0.25f)))
        if (b != null) sc.add(scatter(scaled(b["throttle"], 100.0), b["speed_kmh"], ACC, 1f, f(0.22f, 0.18f)))
        p.add(ax("Speed vs Throttle", "km/h", xl = "Throttle %", layers = sc, grid = false))
        return fig(p, 3)
    }

    fun engine(): Fig {
        val d = df; val b = dfb
        val p = ArrayList<Panel>()
        p.add(ax("RPM", "RPM", layers = line(d, "rpm", ORG, fill = true) + lb(b, "rpm")))
        val main = arrayListOf<Layer>(plot(d, "gear", YLW, "Gear"))
        val tw = arrayListOf<Layer>(plot(d, "rpm", ORG, null, 1f, 0.7f))
        if (b != null) {
            main.add(LineL(b[X], b["gear"], ACC, 1.2f, 0.65f, 1))
            tw.add(LineL(b[X], b["rpm"], ORG, 1f, 0.55f, 1))
        }
        p.add(ax("RPM + Gear", "Gear", layers = main, twin = tw, twinLabel = "RPM", twinColor = ORG, ylabelColor = YLW))
        p.add(ax("Boost", "bar", layers = line(d, "boost", ORG, fill = true) + lb(b, "boost")))
        p.add(ax("Oil Temp (GT7 constant)", "°C", layers = line(d, "oil_temp", ACC) + lb(b, "oil_temp")))
        p.add(ax("Water Temp (GT7 constant)", "°C", layers = line(d, "water_temp", CYN) + lb(b, "water_temp")))
        p.add(ax("Oil Pressure", "bar", layers = line(d, "oil_pressure", GRN) + lb(b, "oil_pressure")))
        p.add(ax("RPM @ Clutch", null, layers = line(d, "rpm_after_clutch", PRP) + lb(b, "rpm_after_clutch")))
        val rh = arrayListOf<Layer>(hist(d["rpm"], 40, ORG, 0.85f))
        if (b != null) rh.add(hist(b["rpm"], 40, ACC, 0.5f, "B", outline = true))
        p.add(ax("RPM Distribution", "Count", xl = "RPM", layers = rh, grid = false, legend = b != null))
        val ge = DoubleArray(9) { 1.0 + it }
        val gh = arrayListOf<Layer>(hist(clip(d["gear"], 1.0, 8.0), 8, YLW, 0.85f, edges = ge, rwidth = 0.7f))
        if (b != null) gh.add(hist(clip(b["gear"], 1.0, 8.0), 8, ACC, 0.55f, "B", edges = ge, rwidth = 0.35f))
        p.add(ax("Gear Usage", "Count", xl = "Gear", layers = gh, grid = false, legend = b != null))
        return fig(p, 3)
    }

    fun tyres(): Fig {
        val d = df; val b = dfb
        val l1 = wd(1.4f, 1.2f); val l2 = wd(1.5f, 1.3f); val l3 = wd(1.2f, 1.0f); val bl = wd(1.0f, 0.8f)
        val sd = f(1.5f, 1.2f)
        val p = ArrayList<Panel>()
        val green = HSpanL(80.0, 100.0, GRN, 0.07f)
        val a0 = arrayListOf<Layer>()
        for ((c, lbl, cl) in listOf(Triple("tyre_temp_fl", "FL", FL), Triple("tyre_temp_fr", "FR", FR), Triple("tyre_temp_rl", "RL", RL), Triple("tyre_temp_rr", "RR", RR)))
            a0.add(plot(d, c, cl, lbl, l1))
        a0.add(green)
        if (b != null) for (c in listOf("tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr")) if (b.has(c)) a0.add(LineL(b[X], b[c], DIM, bl, f(0.45f, 0.4f), 1))
        p.add(ax("All Tyre Temps", "°C", layers = a0, legend = true))
        p.add(ax("Front Temps °C", null, layers = listOf(plot(d, "tyre_temp_fl", FL, "FL", l2), plot(d, "tyre_temp_fr", FR, "FR", l2), green) + lb(b, "tyre_temp_fl") + lb(b, "tyre_temp_fr"), legend = true))
        p.add(ax("Rear Temps °C", null, layers = listOf(plot(d, "tyre_temp_rl", RL, "RL", l2), plot(d, "tyre_temp_rr", RR, "RR", l2), green) + lb(b, "tyre_temp_rl") + lb(b, "tyre_temp_rr"), legend = true))
        p.add(ax("Front vs Rear Avg °C", null, layers = listOf(plot(d, "front_t_avg", FL, "Front avg", l2), plot(d, "rear_t_avg", RL, "Rear avg", l2)) + lb(b, "front_t_avg") + lb(b, "rear_t_avg"), legend = true))
        val s0 = arrayListOf<Layer>()
        for ((c, lbl, cl) in listOf(Triple("tyre_slip_fl", "FL", FL), Triple("tyre_slip_fr", "FR", FR), Triple("tyre_slip_rl", "RL", RL), Triple("tyre_slip_rr", "RR", RR)))
            s0.add(plot(d, c, cl, lbl, l3))
        s0.add(HLineL(1.0, DIM))
        if (b != null) for (c in listOf("tyre_slip_fl", "tyre_slip_fr", "tyre_slip_rl", "tyre_slip_rr")) if (b.has(c)) s0.add(LineL(b[X], b[c], DIM, bl, f(0.35f, 0.3f), 1))
        p.add(ax("All Slip Ratios", "Slip", layers = s0, legend = true))
        p.add(ax("Front Slip", null, layers = listOf(plot(d, "tyre_slip_fl", FL, "FL", l2), plot(d, "tyre_slip_fr", FR, "FR", l2), HLineL(1.0, DIM)) + lb(b, "tyre_slip_fl") + lb(b, "tyre_slip_fr"), legend = true))
        p.add(ax("Rear Slip", null, layers = listOf(plot(d, "tyre_slip_rl", RL, "RL", l2), plot(d, "tyre_slip_rr", RR, "RR", l2), HLineL(1.0, DIM)) + lb(b, "tyre_slip_rl") + lb(b, "tyre_slip_rr"), legend = true))
        p.add(ax("L−R Balance °C", "Left − Right", layers = line(d, "lr_t_bal", YLW) + zero() + lb(b, "lr_t_bal")))
        val sc = arrayListOf<Layer>(scatter(d["speed_kmh"], d["tyre_slip_rr"], RR, sd, f(0.3f, 0.25f)), HLineL(1.0, DIM))
        if (b != null && b.has("tyre_slip_rr")) sc.add(scatter(b["speed_kmh"], b["tyre_slip_rr"], ACC, sd, f(0.2f, 0.18f)))
        p.add(ax("Rear-R Slip vs Speed", "Slip", xl = "km/h", layers = sc, grid = false))
        return fig(p, 3)
    }

    fun dynamics(): Fig {
        val d = df; val b = dfb
        val l1 = wd(1.2f, 1.0f); val l2 = wd(1.5f, 1.3f); val bl = wd(1.0f, 0.8f)
        val p = ArrayList<Panel>()
        val s0 = arrayListOf<Layer>()
        for ((c, lbl, cl) in listOf(Triple("susp_fl", "FL", FL), Triple("susp_fr", "FR", FR), Triple("susp_rl", "RL", RL), Triple("susp_rr", "RR", RR)))
            s0.add(plot(d, c, cl, lbl, l1))
        if (b != null) for (c in listOf("susp_fl", "susp_fr", "susp_rl", "susp_rr")) if (b.has(c)) s0.add(LineL(b[X], b[c], DIM, bl, 0.4f, 1))
        p.add(ax("All Suspension mm", null, layers = s0, legend = true))
        p.add(ax("Front Suspension mm", null, layers = listOf(plot(d, "susp_fl", FL, "FL", l2), plot(d, "susp_fr", FR, "FR", l2)) + lb(b, "susp_fl") + lb(b, "susp_fr"), legend = true))
        p.add(ax("Rear Suspension mm", null, layers = listOf(plot(d, "susp_rl", RL, "RL", l2), plot(d, "susp_rr", RR, "RR", l2)) + lb(b, "susp_rl") + lb(b, "susp_rr"), legend = true))
        p.add(ax("Ride Height mm", null, layers = line(d, "ride_height_mm", ORG) + lb(b, "ride_height_mm")))
        p.add(ax("Yaw Rate (ang_y)", "rad/s", layers = line(d, "ang_y", PRP) + zero() + lb(b, "ang_y")))
        p.add(ax("Lateral Vel X", "m/s", layers = line(d, "vel_x", CYN) + zero() + lb(b, "vel_x")))
        p.add(ax("Vertical Vel Y", "m/s", layers = line(d, "vel_y", GRN) + zero() + lb(b, "vel_y")))
        val lg = d["long_g"]; val n = d.n
        p.add(ax("Long G", "g", layers = line(d, "long_g", YLW) + zero() + lb(b, "long_g") + listOf(
            FillL(d[X], lg, DoubleArray(n), GRN, 0.12f, where = BooleanArray(n) { lg[it] > 0 }),
            FillL(d[X], lg, DoubleArray(n), ACC, 0.12f, where = BooleanArray(n) { lg[it] < 0 }))))
        p.add(ax("Lat G", "g", layers = line(d, "lat_g", PRP) + zero() + lb(b, "lat_g")))
        return fig(p, 3)
    }

    fun maps(): Fig {
        val d = df
        val what = if (race) "race" else "lap"
        if (!hasGps(d)) return fig(List(4) { msg("No GPS data in this $what") }, 2)
        val suffix = if (race) " — all laps" else ""
        val lwm = wd(2.2f, 1.4f); val am = f(0.9f, 0.75f)
        fun m(col: String, cmap: String, title: String, v: DoubleArray = d[col]) =
            MapP(title + suffix, d["world_x"], d["world_z"], v, cmap, lwm, am)
        return fig(listOf(
            m("speed_kmh", "turbo", "Speed (km/h)"), m("throttle", "Greens", "Throttle"), m("brake", "Reds", "Brake"),
            m("gear", "plasma", "Gear")), 2)
    }

    fun gforce(): Fig {
        val d = df; val b = dfb; val n = d.n
        val lg = d["long_g"]
        val p = ArrayList<Panel>()
        p.add(ax("Longitudinal G", "g", layers = line(d, "long_g", YLW) + zero() + listOf(
            FillL(d[X], lg, DoubleArray(n), GRN, 0.15f, where = BooleanArray(n) { lg[it] > 0 }),
            FillL(d[X], lg, DoubleArray(n), ACC, 0.15f, where = BooleanArray(n) { lg[it] < 0 })) + lb(b, "long_g")))
        p.add(ax("Lateral G", "g", layers = line(d, "lat_g", PRP) + zero() + lb(b, "lat_g")))
        val gg = arrayListOf<Layer>(ScatterL(d["lat_g"], d["long_g"], c = d["speed_kmh"], cmap = "turbo", s = f(2f, 1.5f), alpha = f(0.4f, 0.3f)),
            HLineL(0.0, DIM, 0.5f, 0), VLineL(0.0, DIM, 0.5f, 0), circle(1.0, DIM, 0.6f), circle(2.0, DIM, 0.6f))
        if (b != null) gg.add(scatter(b["lat_g"], b["long_g"], ACC, f(1.5f, 1.2f), f(0.22f, 0.18f)))
        p.add(ax("G-G Diagram", "Long G", xl = "Lat G", layers = gg, grid = false, cb = Cb("turbo", Np.min(d["speed_kmh"]), Np.max(d["speed_kmh"]))))
        p.add(ax("Total G", "g", layers = line(d, "total_g", CYN, fill = true) + lb(b, "total_g")))
        return fig(p, 2)
    }

    fun fuel(): Fig {
        val d = df; val b = dfb; val n = d.n
        val p = ArrayList<Panel>()
        val f0 = arrayListOf<Layer>()
        f0.addAll(line(d, "fuel_remaining", GRN, fill = true))
        var legend = false
        if (race) {
            val spans = RaceLoader.refuelSpans(d["fuel_remaining"])
            if (spans.isNotEmpty()) {
                val ii = spans.map { it.second }.toIntArray()
                f0.add(ScatterL(take(d[X], ii), take(d["fuel_remaining"], ii), YLW, s = 20f, alpha = 1f, label = "Refuel", z = 5))
                legend = true
            }
        }
        f0.addAll(lb(b, "fuel_remaining"))
        p.add(ax("Fuel Remaining", "% (GT7)", layers = f0, legend = legend, legendSize = 6f))
        p.add(ax("Fuel Burn Rate", "% per s (GT7 gauge)", layers = line(d, "fuel_burn", ORG) + lb(b, "fuel_burn")))
        val cz = arrayListOf<Layer>(FillL(d[X], d["coasting"], DoubleArray(n), DIM2, 0.6f, Step.POST))
        if (b != null) cz.add(FillL(b[X], b["coasting"], DoubleArray(b.n), ACC, 0.3f, Step.POST))
        p.add(ax("Coasting Zones", "1=coasting", layers = cz))
        val th = arrayListOf<Layer>(hist(scaled(d["throttle"], 100.0), 30, GRN, 0.85f))
        if (b != null) th.add(hist(scaled(b["throttle"], 100.0), 30, ACC, 0.5f, "B", outline = true))
        p.add(ax("Throttle Distribution", "Count", xl = "%", layers = th, grid = false, legend = b != null))
        val bh = arrayListOf<Layer>(hist(scaled(d["brake"], 100.0), 30, ACC, 0.85f))
        if (b != null) bh.add(hist(scaled(b["brake"], 100.0), 30, ACC, 0.5f, "B", outline = true))
        p.add(ax("Brake Distribution", "Count", xl = "%", layers = bh, grid = false, legend = b != null))
        val sb = arrayListOf<Layer>(scatter(scaled(d["brake"], 100.0), d["speed_kmh"], ACC, f(1.5f, 1.2f), f(0.3f, 0.25f)))
        if (b != null) sb.add(scatter(scaled(b["brake"], 100.0), b["speed_kmh"], ACC, f(1.5f, 1.2f), f(0.2f, 0.18f)))
        p.add(ax("Speed vs Brake", "km/h", xl = "Brake %", layers = sb, grid = false))
        return fig(p, 3)
    }

    private fun dtBraking(t: DoubleArray): DoubleArray {
        val dd = Np.diff(t)
        return DoubleArray(dd.size) { val v = dd[it]; if (v.isNaN() || v == 0.0) 0.01 else v }
    }

    fun braking(): Fig {
        val d = df; val b = dfb; val n = d.n
        val brk = d["brake"]; val thr = d["throttle"]
        val p = ArrayList<Panel>()
        val braking = where(d) { brk[it] > 0.05 }
        p.add(ax("Brake Trace", "0–1", layers = line(d, "brake", ACC, fill = true) + lb(b, "brake")))
        val bp = if (braking.isEmpty()) doubleArrayOf(0.0) else scaled(take(brk, braking), 100.0)
        val bh = arrayListOf<Layer>(hist(bp, 30, ACC, 0.85f))
        if (b != null) {
            val bb = b["brake"]; val ib = where(b) { bb[it] > 0.05 }
            if (ib.isNotEmpty()) bh.add(hist(scaled(take(bb, ib), 100.0), 30, ACC, 0.5f, "B", outline = true))
        }
        p.add(ax("Brake Pressure Dist", "Count", xl = "Brake %", layers = bh, grid = false, legend = b != null && bh.size > 1))
        val tb = arrayListOf<Layer>(LineL(d[X], scaled(thr, 100.0), GRN, wd(1.2f, 1.0f), 0.8f, label = "Thr"),
            LineL(d[X], scaled(brk, 100.0), ACC, wd(1.5f, 1.3f), label = "Brk"))
        val overlap = BooleanArray(n) { thr[it] > 0.05 && brk[it] > 0.05 }
        if (overlap.any { it }) tb.add(FillL(d[X], scaled(thr, 100.0), scaled(brk, 100.0), YLW, 0.3f, where = overlap))
        p.add(ax("Trail Braking Overlap", "%", layers = tb, legend = true))
        if (hasGps(d)) p.add(MapP("Brake Intensity (Map)", d["world_x"], d["world_z"], brk, "Reds", wd(2.2f, 1.4f), f(0.9f, 0.75f)))
        else p.add(msg("No GPS data", 10f))
        val lg = d["long_g"]
        p.add(ax("Long G (Deceleration)", "g", layers = line(d, "long_g", YLW) + zero() + listOf(
            FillL(d[X], lg, DoubleArray(n), ACC, 0.2f, where = BooleanArray(n) { lg[it] < 0 })) + lb(b, "long_g")))
        // speed at brake points
        val db = Np.diff(brk)
        val starts = where(d) { !db[it].isNaN() && db[it] > 0.1 && brk[it] > 0.1 }
        val sp = arrayListOf<Layer>(LineL(d[X], d["speed_kmh"], CYN, wd(0.8f, 0.6f), 0.3f))
        if (b != null) sp.add(LineL(b[X], b["speed_kmh"], ACC, wd(0.7f, 0.5f), f(0.22f, 0.18f)))
        if (starts.isNotEmpty()) sp.add(ScatterL(take(d[X], starts), take(d["speed_kmh"], starts), ACC, s = f(18f, 10f), alpha = f(0.75f, 0.6f), z = 3))
        p.add(ax("Speed at Brake Points", "km/h", layers = sp))
        val zones = Scores.brakeZoneDurations(d)
        p.add(ax("Brake Zone Duration", "Count", xl = "s", layers = if (zones.isNotEmpty()) listOf(hist(zones.toDoubleArray(), min(25, zones.size), ACC, 0.85f)) else emptyList(), grid = false))
        val dt2 = if (race) RaceLoader.dtSeries(d) else dtBraking(d["t"])
        val rel = DoubleArray(n) { -(db[it] / dt2[it]) }
        val ri = where(d) { !rel[it].isNaN() && rel[it] > 1.0 }
        p.add(ax("Brake Release Rate", "Δbrake/s", xl = if (race) "Race Time (s)" else "Track Pos", layers =
            if (ri.isNotEmpty()) listOf(ScatterL(take(d[X], ri), take(rel, ri), ORG, s = f(4f, 3f), alpha = f(0.6f, 0.5f))) else emptyList()))
        val sl = d["tyre_slip_rl"]; val sr = d["tyre_slip_rr"]
        if (Np.max(DoubleArray(n) { abs(sl[it]) }) > 0.01 && braking.size > 10) {
            val avg = DoubleArray(braking.size) { (sl[braking[it]] + sr[braking[it]]) / 2 }
            p.add(ax("Rear Slip vs Brake %", "Slip", xl = "Brake %", layers = listOf(
                scatter(scaled(take(brk, braking), 100.0), avg, PRP, f(2f, 1.5f), f(0.3f, 0.25f)), HLineL(1.0, DIM)), grid = false))
        } else p.add(msg("No slip data", 11f))
        return fig(p, 3)
    }

    fun traction(): Fig {
        val d = df; val b = dfb; val n = d.n
        val p = ArrayList<Panel>()
        val gg = arrayListOf<Layer>(ScatterL(d["lat_g"], d["long_g"], c = d["speed_kmh"], cmap = "turbo", s = f(2f, 1.5f), alpha = f(0.42f, 0.35f)))
        if (b != null) gg.add(scatter(b["lat_g"], b["long_g"], ACC, f(1.5f, 1.2f), f(0.2f, 0.18f), "B"))
        gg.add(HLineL(0.0, DIM, 0.5f, 0)); gg.add(VLineL(0.0, DIM, 0.5f, 0))
        for (r in listOf(1.0, 2.0, 3.0)) gg.add(circle(r, DIM, 0.5f, 0.7f, 200))
        p.add(ax("G-G Traction Circle", "Long G", xl = "Lat G", layers = gg, grid = false, xlim = doubleArrayOf(-3.5, 3.5), ylim = doubleArrayOf(-3.5, 3.5),
            cb = Cb("turbo", Np.min(d["speed_kmh"]), Np.max(d["speed_kmh"])), legend = b != null))
        p.add(ax("Lateral G", "g", layers = line(d, "lat_g", PRP) + zero() + lb(b, "lat_g")))
        p.add(ax("Total G", "g", layers = line(d, "total_g", CYN, fill = true) + lb(b, "total_g")))
        val s2 = arrayListOf<Layer>(ScatterL(absA(d["lat_g"]), d["speed_kmh"], c = d["throttle"], cmap = "RdYlGn", vmin = 0.0, vmax = 1.0, s = f(2f, 1.5f), alpha = f(0.35f, 0.3f)))
        if (b != null) s2.add(scatter(absA(b["lat_g"]), b["speed_kmh"], ACC, f(1.5f, 1.2f), f(0.18f, 0.15f)))
        p.add(ax("|Lat G| vs Speed", "km/h", xl = "|Lat G|", layers = s2, grid = false, cb = Cb("RdYlGn", 0.0, 1.0)))
        val e = n / 3
        val lat = absA(d["lat_g"])
        val names = if (race) listOf("Early", "Mid", "Late") else listOf("Entry", "Mid", "Exit")
        p.add(ax("|Lat G| ${names[0]}/${names[1]}/${names[2]}", "Density", xl = "|Lat G|", layers = listOf(
            hist(lat.copyOfRange(0, e), 25, GRN, 0.65f, names[0], density = true),
            hist(lat.copyOfRange(e, 2 * e), 25, YLW, 0.65f, names[1], density = true),
            hist(lat.copyOfRange(2 * e, n), 25, ACC, 0.65f, names[2], density = true)), grid = false, legend = true))
        val lg = d["long_g"]
        val pos = lg.filter { it > 0 }.toDoubleArray(); val neg = lg.filter { it < 0 }.map { abs(it) }.toDoubleArray()
        val lh = arrayListOf<Layer>(hist(pos, 25, GRN, 0.75f, "Accel A", density = true), hist(neg, 25, ACC, 0.75f, "Brake A", density = true))
        if (b != null) {
            val lb2 = b["long_g"]
            lh.add(hist(lb2.filter { it > 0 }.toDoubleArray(), 25, GRN, 0.35f, "Accel B", outline = true, density = true))
            lh.add(hist(lb2.filter { it < 0 }.map { abs(it) }.toDoubleArray(), 25, ACC, 0.35f, "Brake B", outline = true, density = true))
        }
        p.add(ax("Long G Distribution", "Density", xl = "|Long G|", layers = lh, grid = false, legend = true))
        return fig(p, 3)
    }

    fun telediff(): Fig {
        val d = df; val b = dfb
        if (b == null) return fig(List(9) { msg("Load ${if (race) "Race" else "Lap"} B &\nEnable Compare Mode\nto see telemetry diffs") }, 3)
        val axs = if (race) TeleDiff.byTime(d, b) else TeleDiff.byPosition(d, b)
        val xl = if (race) "Race %" else "Lap %"
        val lwd = wd(1.2f, 1.0f)
        fun dp(col: String, title: String, yl: String, lwv: Float = lwd, alpha: Float = 0.15f): XY {
            val dd = TeleDiff.diff(axs, col)
            val m = dd.size
            return ax(title, yl, xl, listOf(
                LineL(axs.x, dd, YLW, lwv), zero(),
                FillL(axs.x, dd, DoubleArray(m), ACC, alpha, where = BooleanArray(m) { dd[it] > 0 }),
                FillL(axs.x, dd, DoubleArray(m), GRN, alpha, where = BooleanArray(m) { dd[it] < 0 })))
        }
        val p = ArrayList<Panel>()
        p.add(dp("speed_kmh", "Δ Speed (B−A)", "Δkm/h")); p.add(dp("throttle", "Δ Throttle (B−A)", "Δ")); p.add(dp("brake", "Δ Brake (B−A)", "Δ"))
        p.add(dp("rpm", "Δ RPM (B−A)", "ΔRPM")); p.add(dp("steering", "Δ Steering (B−A)", "Δ (−1..1)")); p.add(dp("lat_g", "Δ Lat G (B−A)", "Δg"))
        if (!race) {
            val dt = TeleDiff.diff(axs, "t"); val m = dt.size
            val final = if (m > 0) dt[m - 1] else 0.0
            p.add(ax("Δ Time  (final ${signed(final, 3)}s)", "Δs", xl, listOf(
                LineL(axs.x, dt, YLW, 1.5f), zero(),
                FillL(axs.x, dt, DoubleArray(m), ACC, 0.18f, where = BooleanArray(m) { dt[it] > 0 }),
                FillL(axs.x, dt, DoubleArray(m), GRN, 0.18f, where = BooleanArray(m) { dt[it] < 0 }))))
        } else {
            val durA = max(Np.max(d["t"]), 1.0); val durB = max(Np.max(b["t"]), 1.0)
            p.add(TextP(null, listOf("A duration: ${RaceLoader.fmtDur(durA)}", "B duration: ${RaceLoader.fmtDur(durB)}", "Δ: ${signed(durB - durA, 1)}s"),
                a.fg, bg = false, size = 10f, center = true, aspect = 0.62f))
        }
        p.add(dp("long_g", "Δ Long G (B−A)", "Δg"))
        p.add(ax("Speed Overlay", "km/h", xl, listOf(
            LineL(axs.x, axs.a("speed_kmh"), CYN, wd(1.5f, 1.3f), label = "A"),
            LineL(axs.x, axs.b("speed_kmh"), ACC, wd(1.5f, 1.3f), dash = 1, label = "B")), legend = true))
        return fig(p, 3)
    }

    fun ratings(): Fig {
        val d = df; val b = dfb
        val ghostB = b != null && b.compact
        val sa = if (race) Scores.raceScores(d) else Scores.lapScores(d)
        val sb = if (b != null && !ghostB) (if (race) Scores.raceScores(b) else Scores.lapScores(b)) else null
        val labels = Scores.LABELS
        val overallA = sa.average()
        fun cols(v: DoubleArray) = v.map { if (it >= 70) GRN else if (it >= 45) YLW else ACC }
        val bh = if (sb != null) 0.34 else 0.55
        val layers = arrayListOf<Layer>()
        layers.add(BarL(DoubleArray(7) { it + (if (sb != null) bh / 2 else 0.0) }, sa, DoubleArray(7) { bh }, cols(sa), 0.9f, horizontal = true, label = "A"))
        if (sb != null) layers.add(BarL(DoubleArray(7) { it - bh / 2 }, sb, DoubleArray(7) { bh }, cols(sb), 0.65f, horizontal = true, label = "B", hatch = true))
        layers.add(VLineL(70.0, GRN, 0.7f, 1, 0.5f))
        for (i in 0 until 7) layers.add(TextL(sa[i] + 1, i + (if (sb != null) bh / 2 else 0.0), "${fixed(sa[i], 0)}", FG, 7f, ha = 0f, va = 0.5f))
        val bars = XY(title = "Driver Ratings", xlabel = "Score (0–100)", layers = layers, grid = true, gridAxis = 'y', xlim = doubleArrayOf(0.0, 115.0),
            yticks = labels.mapIndexed { i, s -> Tick(i.toDouble(), s) }, ylim = doubleArrayOf(-0.5, 6.5), legend = sb != null, aspect = 0.8f)
        val radarSeries = arrayListOf(RadarS(DoubleArray(7) { sa[it] / 100 }, CYN, false, 0.18f))
        if (sb != null) radarSeries.add(RadarS(DoubleArray(7) { sb[it] / 100 }, ACC, true, 0.1f))
        val radar = Radar("Performance Radar", labels, radarSeries)
        val win = max(1, d.n / (if (race) 40 else 20))
        val tl = arrayListOf<Layer>(
            LineL(d[X], scaled(Np.rollingMean(d["throttle"], win), 100.0), GRN, wd(1.5f, 1.3f), label = "Throttle A"),
            LineL(d[X], scaled(Np.rollingMean(d["brake"], win), 100.0), ACC, wd(1.5f, 1.3f), label = "Brake A"))
        if (b != null) {
            val wb = max(1, b.n / (if (race) 40 else 20))
            tl.add(LineL(b[X], scaled(Np.rollingMean(b["throttle"], wb), 100.0), YLW, wd(1.2f, 1.1f), alpha = 0.75f, dash = 1, label = "Throttle B"))
            tl.add(LineL(b[X], scaled(Np.rollingMean(b["brake"], wb), 100.0), PRP, wd(1.2f, 1.1f), alpha = 0.75f, dash = 1, label = "Brake B"))
        }
        val trend = ax("Rolling Throttle / Brake", "%", xl = if (race) "Race Time (s)" else "Track Position (m)", layers = tl, legend = true)
        val grade = Scores.grade(overallA)
        val gcol = when (grade) { "S", "A", "B+" -> GRN; "B" -> YLW; "C" -> ORG; else -> ACC }
        val gl = arrayListOf<Layer>(TextL(0.5, 0.70, grade, gcol, 48f, axes = true, bold = true), TextL(0.5, 0.42, "${fixed(overallA, 1)} / 100", FG, 13f, axes = true))
        if (sb != null) {
            val ob = sb.average(); val dv = overallA - ob
            gl.add(TextL(0.5, 0.24, "B: ${fixed(ob, 1)}   (Δ ${signed(dv, 1)})", if (dv > 0) GRN else ACC, 9f, axes = true))
        }
        val gradeP = XY(title = "Grade", layers = gl, off = true, aspect = 0.62f)
        return fig(listOf(bars, radar, trend, gradeP), 2, footer = if (ghostB) "Lap B is a leaderboard ghost (inputs only), so it isn't rated." else null)
    }

    fun heatMaps(): Fig {
        val d = df; val b = dfb
        val hasA = hasGps(d); val hasB = b != null && hasGps(b)
        val m9 = listOf(Triple("speed_kmh", "turbo", "Speed (km/h)"), Triple("throttle", "Greens", "Throttle"), Triple("brake", "Reds", "Brake"),
            Triple("lat_g", "RdBu_r", "Lat G"), Triple("long_g", "RdYlGn", "Long G"), Triple("total_g", "hot", "Total G"),
            Triple("tyre_temp_fl", "YlOrRd", "Tyre Temp FL"), Triple("rpm", "plasma", "RPM"), Triple("steering", "coolwarm", "Steering"))
        val m4 = listOf(Triple("speed_kmh", "turbo", "Speed"), Triple("throttle", "Greens", "Throttle"), Triple("brake", "Reds", "Brake"), Triple("lat_g", "RdBu_r", "Lat G"))
        val lwm = wd(2.2f, 1.4f); val am = f(0.9f, 0.75f)
        if (!hasB) {
            if (!hasA) return fig(List(9) { msg("No GPS data") }, 3)
            return fig(m9.map { (c, cm, t) -> MapP(t, d["world_x"], d["world_z"], d[c], cm, lwm, am) }, 3)
        }
        val out = ArrayList<Panel>()
        val bb = b!!
        for ((c, cm, t) in m4) {
            out.add(MapP("A — $t", d["world_x"], d["world_z"], d[c], cm, lwm, am))
            out.add(if (bb.has(c)) MapP("B — $t", bb["world_x"], bb["world_z"], bb[c], cm, lwm, am) else msg("No $t data in B"))
        }
        return fig(out, 2)
    }

    fun timeline(): Fig {
        val d = df; val b = dfb
        fun band(dd: Frame, label: String, col: Color): XY {
            val xp = dd[X]; val n = dd.n
            val thr = dd["throttle"]; val brk = dd["brake"]
            val ft = BooleanArray(n) { thr[it] > 0.95 }; val bk = BooleanArray(n) { brk[it] > 0.05 }
            val cst = BooleanArray(n) { thr[it] < 0.05 && brk[it] < 0.05 }
            val ly = arrayListOf<Layer>(
                FillL(xp, full(n, 2.0), full(n, 3.0), GRN, 0.85f, Step.POST, ft),
                FillL(xp, full(n, 1.0), full(n, 2.0), ACC, 0.85f, Step.POST, bk),
                FillL(xp, full(n, 0.0), full(n, 1.0), DIM, 0.75f, Step.POST, cst))
            var ylo = -0.1
            if (race) {
                val pit = BooleanArray(n) { dd["pit_flag"][it] > 0.5 }
                if (pit.any { it }) ly.add(FillL(xp, full(n, -0.4), full(n, 0.0), ORG, 0.9f, Step.POST, pit))
                ylo = -0.5
            } else {
                val g = dd["gear"]
                val gc = (0 until n - 1).filter { g[it + 1].toInt() != g[it].toInt() }
                val stp = max(1, gc.size / 30)
                var k = 0
                while (k < gc.size) { val i = gc[k]; ly.add(TextL(xp[i], 3.12, "${g[i + 1].toInt()}", YLW, 5f, ha = 0.5f, va = 0f, clip = true)); k += stp }
            }
            val title = if (race) "Race Timeline — $label  (orange band = pit)" else "Lap Timeline — $label"
            return XY(title = title, xlabel = XL, layers = ly, grid = true, gridAxis = 'x', xlim = doubleArrayOf(Np.min(xp), Np.max(xp)),
                ylim = doubleArrayOf(ylo, 3.5), yticks = listOf(Tick(0.5, "Coast"), Tick(1.5, "Brake"), Tick(2.5, "Full Thr")), titleColor = col, aspect = 0.42f)
        }
        if (b == null) {
            val speed = ax("Speed Trace", "km/h", layers = line(d, "speed_kmh", CYN, lw = wd(1.5f, 1.2f), fill = true), aspect = 0.42f)
            val gear = ax("Gear", "Gear", layers = listOf(LineL(d[X], d["gear"], YLW, wd(1.5f, 1.2f), step = Step.POST),
                FillL(d[X], d["gear"], DoubleArray(d.n), YLW, 0.15f, Step.POST)), aspect = 0.42f)
            return fig(listOf(band(d, "A", CYN), speed, gear), 1)
        }
        return fig(listOf(band(d, "A", CYN), band(b, "B", ACC)), 1)
    }
}

class Extra(val label: String, val df: Frame, val color: Color)
