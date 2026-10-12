package app.trace

import android.graphics.Bitmap
import android.graphics.Canvas
import android.graphics.Paint
import android.graphics.Path
import android.graphics.Typeface
import app.trace.charts.Cmaps
import app.trace.telemetry.*
import java.io.ByteArrayOutputStream
import java.text.SimpleDateFormat
import java.util.Locale
import kotlin.math.*

class CardChart(
    val title: String, val bars: Boolean, val x: DoubleArray, val y: DoubleArray,
    val ylabel: String = "", val best: Int? = null,
)
class CardMap(val x: DoubleArray, val z: DoubleArray, val v: DoubleArray)
class CardSpec(
    val kind: String, val date: String, val car: String, val track: String, val era: String,
    val bigLabel: String, val bigValue: String, val tiles: List<Pair<String, String>>,
    val chart: CardChart, val map: CardMap?,
)
class CardFonts(val mono: Typeface, val bold: Typeface)

/** share_card.py ported to android.graphics: same blocks, palette and fonts, laid out portrait (1080x1920) for phones. */
object ShareCard {
    const val W = 1080
    const val H = 1920
    private const val M = 54f

    private fun col(hex: Long) = (0xFF000000 or hex).toInt()
    private val BG = col(0x07080f)
    private val PANEL = col(0x0d0e1a)
    private val GRID = col(0x1c1e30)
    private val FG = col(0xc8d3f5)
    private val DIM = col(0x6b7194)
    private val CYAN = col(0x00f0d4)
    private val GREEN = col(0x39ff85)

    // ── spec builders ─────────────────────────────────────────────────────────
    private fun Meta.s(k: String): String? = this[k]?.toString()?.takeIf { it.isNotEmpty() }

    private fun std1(a: DoubleArray): Double {
        if (a.size < 2) return 0.0
        val m = a.average()
        return sqrt(a.sumOf { (it - m) * (it - m) } / (a.size - 1))
    }

    /** lap_analyst.lap_card_spec */
    fun lapSpec(meta: Meta, df: Frame): CardSpec {
        val lapS = numOf(meta["lap_time_s"])
        val big = if (meta.truthy("incomplete")) "INCOMPLETE" else if (lapS > 0) LapMath.fmtTime(lapS) else "--:--.---"
        val date = try {
            val src = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.ROOT).apply { isLenient = false }
            val d = src.parse((meta["recorded_at"]?.toString() ?: "").take(15))!!
            SimpleDateFormat("dd MMM yyyy", Locale.ENGLISH).format(d)
        } catch (e: Exception) { "" }
        val top = if (df.has("speed_kmh") && df.n > 0) Np.max(df["speed_kmh"]) else 0.0
        val n = df.n
        val pos = df["track_position"]
        val hasPos = df.has("track_position") && Np.max(pos) - Np.min(pos) > 1
        val xs = if (hasPos) pos else DoubleArray(n) { it.toDouble() }
        val order = (0 until n).sortedBy { xs[it] }
        val spd = df["speed_kmh"]
        val chart = CardChart("speed round the lap", false, DoubleArray(n) { xs[order[it]] }, DoubleArray(n) { spd[order[it]] }, "km/h")
        var map: CardMap? = null
        if (!df.compact && df.has("world_x") && df.has("world_z") && n > 2 && std1(df["world_x"]) > 1 && std1(df["world_z"]) > 1)
            map = CardMap(df["world_x"], df["world_z"], spd)
        return CardSpec(
            "LAP", date,
            meta.s("car_display") ?: meta.s("car") ?: "Unknown car",
            meta.s("track_display") ?: (meta.s("track") ?: "unknown track").replace("_", " "),
            if (LapMath.isCurrentEra(meta)) "" else "pre-${TraceUtil.PHYSICS_ERA} physics",
            "LAP TIME", big,
            listOf("TOP SPEED" to (if (top > 0) fixed(top, 0) + " km/h" else "--"), "FUEL USED" to (LapMath.lapFuelUsed(df) ?: "--")),
            chart, map,
        )
    }

    /** race_analyst.race_card_spec */
    fun raceSpec(meta: Meta, df: Frame): CardSpec {
        val splits = RaceLoader.lapSplitStats(df)
        val done = splits.filter { it.complete && it.timeS > 1 }
        val times = DoubleArray(done.size) { done[it].timeS }
        var best: Int? = null
        for (i in times.indices) if (best == null || times[i] < times[best!!]) best = i
        val date = try {
            val src = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.ROOT).apply { isLenient = false }
            SimpleDateFormat("dd MMM yyyy", Locale.ENGLISH).format(src.parse((meta["recorded_at"]?.toString() ?: "").take(15))!!)
        } catch (e: Exception) { "" }
        val top = if (df.n > 0) Np.max(df["speed_kmh"]) else 0.0
        var map: CardMap? = null
        val bi = best
        if (bi != null) {
            val bestLapNo = done[bi].lap
            for ((seg, sp) in RaceLoader.countedSegments(df).zip(splits)) {
                if (sp.lap == bestLapNo && df.has("world_x") && seg.last - seg.first + 1 > 2) {
                    val f = df.slice(seg.first, seg.last + 1)
                    if (std1(f["world_x"]) > 1 && std1(f["world_z"]) > 1) { map = CardMap(f["world_x"], f["world_z"], f["speed_kmh"]); break }
                }
            }
        }
        return CardSpec(
            "RACE", date,
            meta.s("car_display") ?: meta.s("car") ?: "Unknown car",
            meta.s("track_display") ?: (meta.s("track") ?: "unknown track").replace("_", " "),
            if (LapMath.isCurrentEra(meta)) "" else "pre-${TraceUtil.PHYSICS_ERA} physics",
            "BEST LAP", if (bi != null) LapMath.fmtTime(times[bi], 2) else "--:--.--",
            listOf("LAPS" to "${done.size}", "TOP SPEED" to (if (top > 0) fixed(top, 0) + " km/h" else "--")),
            CardChart("lap times  -  best lap highlighted", true, DoubleArray(done.size) { done[it].lap.toDouble() }, times, best = best),
            map,
        )
    }

    // ── drawing ───────────────────────────────────────────────────────────────
    private fun Canvas.txt(
        s: String, x: Float, cy: Float, size: Float, color: Int, f: CardFonts, bold: Boolean = false,
        align: Paint.Align = Paint.Align.LEFT,
    ) {
        val p = Paint(Paint.ANTI_ALIAS_FLAG)
        p.typeface = if (bold) f.bold else f.mono
        p.textSize = size; p.color = color; p.textAlign = align
        val fm = p.fontMetrics
        drawText(s, x, cy - (fm.ascent + fm.descent) / 2f, p)
    }

    private fun measure(s: String, size: Float, f: CardFonts, bold: Boolean): Float {
        val p = Paint(Paint.ANTI_ALIAS_FLAG)
        p.typeface = if (bold) f.bold else f.mono
        p.textSize = size
        return p.measureText(s)
    }

    /** Biggest size <= base that fits maxW. */
    private fun fit(s: String, base: Float, maxW: Float, f: CardFonts, bold: Boolean): Float {
        val w = measure(s, base, f, bold)
        return if (w <= maxW) base else base * maxW / w
    }

    private fun Canvas.fill(l: Float, t: Float, r: Float, b: Float, color: Int) {
        val p = Paint(); p.color = color; p.style = Paint.Style.FILL
        drawRect(l, t, r, b, p)
    }

    private fun niceTicks(lo: Double, hi: Double, n: Int): List<Double> {
        val span = hi - lo
        if (!(span > 0) || span.isNaN()) return listOf(lo)
        val raw = span / n
        val mag = 10.0.pow(floor(log10(raw)))
        val f = raw / mag
        val step = mag * when { f <= 1.0 -> 1.0; f <= 2.0 -> 2.0; f <= 2.5 -> 2.5; f <= 5.0 -> 5.0; else -> 10.0 }
        val out = ArrayList<Double>()
        var v = ceil(lo / step - 1e-9) * step
        while (v <= hi + step * 1e-9) { out.add(v); v += step }
        return out
    }

    private fun numLabel(v: Double): String = if (abs(v - Math.rint(v)) < 1e-9) Math.rint(v).toLong().toString() else fixed(v, 1)
    private fun clock(v: Double): String = "${(v / 60).toInt()}:" + fixed(v % 60, 1).padStart(4, '0')

    private fun Canvas.blank(f: CardFonts, l: Float, t: Float, r: Float, b: Float, msg: String) {
        fill(l, t, r, b, PANEL)
        txt(msg, (l + r) / 2, (t + b) / 2, 28f, DIM, f, align = Paint.Align.CENTER)
    }

    private fun Canvas.headline(f: CardFonts, spec: CardSpec) {
        val l = M; val t = 230f; val r = W - M; val b = 820f
        fill(l, t, r, b, PANEL)
        val x = l + 48f
        val maxW = r - l - 96f
        // car: whole name, never cut -- shrinks to fit, then wraps onto a second line
        val name = spec.car.trim().ifEmpty { "Unknown car" }
        var size = fit(name, 46f, maxW, f, true)
        var lines = listOf(name)
        if (size < 30f) {
            size = 30f
            val words = name.split(" ")
            val l1 = StringBuilder(); var i = 0
            while (i < words.size && measure((l1.toString() + " " + words[i]).trim(), size, f, true) <= maxW) { l1.append(if (l1.isEmpty()) "" else " ").append(words[i]); i++ }
            val rest = words.drop(i).joinToString(" ")
            lines = if (l1.isEmpty()) listOf(name) else listOf(l1.toString(), rest)
        }
        val lh = size * 1.1f
        for ((k, line) in lines.withIndex()) txt(line, x, 300f - (lines.size - 1) * lh / 2f + k * lh, size, FG, f, true)
        val track = spec.track.ifEmpty { "Unknown track" }
        txt(track, x, 380f, fit(track, if (track.length <= 30) 38f else 28f, maxW, f, false), CYAN, f)
        txt(spec.bigValue.ifEmpty { "--:--.---" }, x, 530f, fit(spec.bigValue, 170f, maxW, f, true), GREEN, f, true)
        var label = spec.bigLabel
        if (spec.era.isNotEmpty()) label += "   [${spec.era}]"
        txt(label, x, 625f, 26f, DIM, f)
        for ((i, tile) in spec.tiles.take(4).withIndex()) {
            val tx = x + (i % 2) * 438f
            val ty = 705f + (i / 2) * 110f
            txt(tile.first, tx, ty, 24f, DIM, f)
            txt(tile.second, tx, ty + 52f, 52f.coerceAtMost(fit(tile.second, 52f, 410f, f, true)), FG, f, true)
        }
    }

    private fun Canvas.chart(f: CardFonts, spec: CardSpec) {
        val ch = spec.chart
        val l = 144f; val t = 915f; val r = W - M; val b = 1215f
        txt(ch.title.uppercase(), M, 875f, 22f, DIM, f)
        if (ch.ylabel.isNotEmpty()) txt(ch.ylabel, r, 875f, 22f, DIM, f, align = Paint.Align.RIGHT)
        val x = ch.x; val y = ch.y
        val gridP = Paint().apply { color = GRID; strokeWidth = 2f; style = Paint.Style.STROKE }
        if (ch.bars) {
            if (x.isEmpty() || x.size != y.size) { blank(f, l, t, r, b, "no complete laps"); return }
            fill(l, t, r, b, PANEL)
            val n = x.size
            val lo = y.min(); val hi = y.max()
            val pad = max(max(hi - lo, lo * 0.01), 0.5)
            val y0 = max(0.0, lo - pad * 0.6); val y1 = hi + pad * 0.25
            val x0 = x.min() - 0.8; val x1 = x.max() + 0.8
            fun sx(v: Double) = (l + (v - x0) / (x1 - x0) * (r - l)).toFloat()
            fun sy(v: Double) = (b - (v - y0) / (y1 - y0) * (b - t)).toFloat()
            for (tick in niceTicks(y0, y1, 5)) {
                drawLine(l, sy(tick), r, sy(tick), gridP)
                txt(clock(tick), l - 12f, sy(tick), 22f, DIM, f, align = Paint.Align.RIGHT)
            }
            val step = max(1, ceil(n / 20.0).toInt())
            val bp = Paint(); bp.style = Paint.Style.FILL
            for (i in 0 until n) {
                bp.color = if (ch.best == i) GREEN else CYAN
                drawRect(sx(x[i] - 0.35), sy(y[i]), sx(x[i] + 0.35), b, bp)
                if (i % step == 0) txt(numLabel(x[i]), sx(x[i]), b + 26f, 20f, DIM, f, align = Paint.Align.CENTER)
            }
            ch.best?.let { if (it in 0 until n) txt(LapMath.fmtTime(y[it], 2), sx(x[it]), sy(y[it]) - 16f, 22f, GREEN, f, true, Paint.Align.CENTER) }
            return
        }
        if (x.size < 2 || x.size != y.size) { blank(f, l, t, r, b, "no data"); return }
        fill(l, t, r, b, PANEL)
        val x0 = x.min(); val x1 = max(x.max(), x0 + 1e-9)
        val ymax = (y.max().takeIf { it != 0.0 } ?: 1.0) * 1.08
        fun sx(v: Double) = (l + (v - x0) / (x1 - x0) * (r - l)).toFloat()
        fun sy(v: Double) = (b - v / ymax * (b - t)).toFloat()
        for (tick in niceTicks(0.0, ymax, 5)) {
            drawLine(l, sy(tick), r, sy(tick), gridP)
            txt(numLabel(tick), l - 12f, sy(tick), 22f, DIM, f, align = Paint.Align.RIGHT)
        }
        for (tick in niceTicks(x0, x1, 5)) {
            drawLine(sx(tick), t, sx(tick), b, gridP)
            txt(numLabel(tick), sx(tick), b + 26f, 22f, DIM, f, align = Paint.Align.CENTER)
        }
        val line = Path(); val area = Path()
        area.moveTo(sx(x[0]), sy(0.0))
        for (i in x.indices) {
            if (i == 0) line.moveTo(sx(x[i]), sy(y[i])) else line.lineTo(sx(x[i]), sy(y[i]))
            area.lineTo(sx(x[i]), sy(y[i]))
        }
        area.lineTo(sx(x[x.size - 1]), sy(0.0)); area.close()
        drawPath(area, Paint(Paint.ANTI_ALIAS_FLAG).apply { color = CYAN; alpha = 38; style = Paint.Style.FILL })
        drawPath(line, Paint(Paint.ANTI_ALIAS_FLAG).apply {
            color = CYAN; style = Paint.Style.STROKE; strokeWidth = 5f; strokeJoin = Paint.Join.ROUND; strokeCap = Paint.Cap.ROUND
        })
    }

    private fun Canvas.trackMap(f: CardFonts, spec: CardSpec) {
        val l = M; val t = 1335f; val r = W - M; val b = 1845f
        txt("TRACK MAP  -  COLOURED BY SPEED", M, 1295f, 22f, DIM, f)
        val m = spec.map
        if (m == null || m.x.size < 3 || m.x.size != m.z.size || m.x.size != m.v.size) { blank(f, l, t, r, b, "no GPS data in this file"); return }
        val x = m.x; val z = m.z; val v = m.v
        val xmin = x.min(); val xmax = x.max(); val zmin = z.min(); val zmax = z.max()
        val rx = max((xmax - xmin) * 1.1, 1e-6); val rz = max((zmax - zmin) * 1.1, 1e-6)
        val scale = min((r - l) / rx, (b - t) / rz)
        val xc = (xmin + xmax) / 2; val zc = (zmin + zmax) / 2
        fun px(a: Double) = ((l + r) / 2 + (a - xc) * scale).toFloat()
        fun py(a: Double) = ((t + b) / 2 - (a - zc) * scale).toFloat()
        val vmin = v.min(); val vmax = v.max().takeIf { it != 0.0 } ?: 1.0
        val p = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeWidth = 9f; strokeCap = Paint.Cap.ROUND }
        for (i in 0 until x.size - 1) {
            if (x[i].isNaN() || z[i].isNaN() || x[i + 1].isNaN() || z[i + 1].isNaN()) continue
            val tt = if (vmax == vmin) 0.0 else (v[i] - vmin) / (vmax - vmin)
            p.color = Cmaps.at("turbo", tt)
            drawLine(px(x[i]), py(z[i]), px(x[i + 1]), py(z[i + 1]), p)
        }
        txt(fixed(v.min(), 0) + " - " + fixed(v.max(), 0) + " km/h", l + 6f, b - 14f, 22f, DIM, f)
    }

    fun render(spec: CardSpec, f: CardFonts): Bitmap {
        val bmp = Bitmap.createBitmap(W, H, Bitmap.Config.ARGB_8888)
        val c = Canvas(bmp)
        c.drawColor(BG)
        c.txt("TRACE", M, 110f, 84f, CYAN, f, true)
        c.txt("GT7 TELEMETRY  -  ${spec.kind} CARD", M, 172f, 26f, DIM, f)
        c.txt(spec.date, W - M, 110f, 32f, DIM, f, align = Paint.Align.RIGHT)
        try { c.headline(f, spec) } catch (e: Exception) { }   // one broken block must not cost the whole card
        try { c.chart(f, spec) } catch (e: Exception) { }
        try { c.trackMap(f, spec) } catch (e: Exception) { }
        c.txt("gt7trace.netlify.app  -  made with TRACE", W / 2f, 1885f, 24f, DIM, f, align = Paint.Align.CENTER)
        return bmp
    }

    fun png(spec: CardSpec, f: CardFonts): ByteArray {
        val bmp = render(spec, f)
        val out = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.PNG, 100, out)
        bmp.recycle()
        return out.toByteArray()
    }
}
