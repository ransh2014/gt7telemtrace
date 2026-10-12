package app.trace.charts

import android.graphics.Canvas
import android.graphics.DashPathEffect
import android.graphics.Paint
import android.graphics.Path
import android.graphics.RectF
import android.graphics.Typeface
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import app.trace.AP
import app.trace.telemetry.Np
import kotlin.math.*

/** Everything the renderer needs to look like the PC apps' matplotlib output. */
class RTheme(val a: AP, val dens: Float, val mono: Typeface, val bold: Typeface) {
    val bg = a.bg.toArgb(); val pnl = a.pnl.toArgb(); val face = a.pnl2.toArgb()
    val fg = a.fg.toArgb(); val dim = a.dim.toArgb(); val dim2 = a.dim2.toArgb()
    /** font size in px for a matplotlib point size (phones are small, so a touch larger than 1:1). */
    fun f(pt: Float) = pt * 1.25f * dens
    /** line/marker size in px for a matplotlib point size. */
    fun g(pt: Float) = pt * 0.9f * dens
}

fun Color.argb(alpha: Float = 1f): Int {
    val c = toArgb()
    val a = (((c ushr 24) and 0xFF) * alpha).roundToInt().coerceIn(0, 255)
    return (a shl 24) or (c and 0xFFFFFF)
}

private fun withAlpha(c: Int, alpha: Float): Int {
    val a = (((c ushr 24) and 0xFF) * alpha).roundToInt().coerceIn(0, 255)
    return (a shl 24) or (c and 0xFFFFFF)
}

object PlotRender {

    // ── shared helpers ───────────────────────────────────────────────────────
    private class Paints(val t: RTheme) {
        val stroke = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeJoin = Paint.Join.ROUND }
        val fill = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.FILL }
        val text = Paint(Paint.ANTI_ALIAS_FLAG).apply { typeface = t.mono }
        val dot = Paint(Paint.ANTI_ALIAS_FLAG).apply { style = Paint.Style.STROKE; strokeCap = Paint.Cap.ROUND }
    }

    private fun dashFor(kind: Int, lw: Float, t: RTheme): DashPathEffect? = when (kind) {
        1 -> DashPathEffect(floatArrayOf(3.7f * lw, 1.6f * lw), 0f)
        2 -> DashPathEffect(floatArrayOf(1f * lw, 1.65f * lw + 1f), 0f)
        else -> null
    }

    private fun lines(s: String) = s.split('\n')

    /** Draws (possibly multi-line) text. ha 0 left..1 right; va 0 bottom..1 top (of the whole block). */
    private fun text(cv: Canvas, p: Paints, s: String, x: Float, y: Float, size: Float, color: Int,
                     ha: Float, va: Float, bold: Boolean = false, rotate: Float = 0f) {
        p.text.textSize = size
        p.text.color = color
        p.text.typeface = if (bold) p.t.bold else p.t.mono
        val ls = lines(s)
        val lh = size * 1.2f
        val blockH = ls.size * lh
        val top = y - blockH * (1f - va)
        if (rotate != 0f) { cv.save(); cv.rotate(rotate, x, y) }
        for ((i, line) in ls.withIndex()) {
            val w = p.text.measureText(line)
            cv.drawText(line, x - w * ha, top + i * lh + size * 0.9f, p.text)
        }
        if (rotate != 0f) cv.restore()
    }

    private fun textW(p: Paints, s: String, size: Float, bold: Boolean = false): Float {
        p.text.textSize = size
        p.text.typeface = if (bold) p.t.bold else p.t.mono
        return lines(s).maxOfOrNull { p.text.measureText(it) } ?: 0f
    }

    /** Shrinks a title to fit, else wraps it onto two lines. */
    private fun fitTitle(p: Paints, title: String, maxW: Float, size: Float): Pair<String, Float> {
        var sz = size
        if (textW(p, title, sz, false) <= maxW) return title to sz
        val minSz = size * 0.8f
        sz = max(minSz, size * maxW / textW(p, title, size))
        if (textW(p, title, sz) <= maxW) return title to sz
        val words = title.split(' ')
        if (words.size < 2) return title to sz
        var best = title; var bestW = Float.MAX_VALUE
        for (k in 1 until words.size) {
            val a = words.subList(0, k).joinToString(" "); val b = words.subList(k, words.size).joinToString(" ")
            val w = max(textW(p, a, sz), textW(p, b, sz))
            if (w < bestW) { bestW = w; best = "$a\n$b" }
        }
        if (bestW > maxW) sz = max(size * 0.65f, sz * maxW / bestW)
        return best to sz
    }

    fun niceTicks(lo: Double, hi: Double, maxN: Int): DoubleArray {
        if (!(hi > lo) || !lo.isFinite() || !hi.isFinite()) return doubleArrayOf(lo)
        val raw = (hi - lo) / max(1, maxN - 1)
        val mag = 10.0.pow(floor(log10(raw)))
        var step = mag * 10
        for (m in doubleArrayOf(1.0, 2.0, 2.5, 5.0, 10.0)) if (m * mag >= raw - 1e-12) { step = m * mag; break }
        val first = ceil(lo / step - 1e-9) * step
        val out = ArrayList<Double>()
        var v = first
        var guard = 0
        while (v <= hi + step * 1e-9 && guard++ < 100) { out.add(if (abs(v) < step * 1e-9) 0.0 else v); v += step }
        return out.toDoubleArray()
    }

    private fun decimalsFor(step: Double): Int {
        if (step <= 0 || !step.isFinite()) return 0
        for (d in 0..6) {
            val v = step * 10.0.pow(d)
            if (abs(v - v.roundToInt()) < 1e-6 * max(1.0, abs(v))) return d
        }
        return 6
    }

    fun fmtTicks(ticks: DoubleArray): List<String> {
        if (ticks.isEmpty()) return emptyList()
        val step = if (ticks.size > 1) ticks[1] - ticks[0] else 1.0
        val d = decimalsFor(step)
        return ticks.map { fixed(it, d) }
    }

    private fun fixed(v: Double, d: Int): String {
        val s = String.format(java.util.Locale.ROOT, "%.${d}f", v)
        return if (s.startsWith("-") && s.trim('-', '0', '.').isEmpty()) s.substring(1) else s
    }

    // ── data limits ──────────────────────────────────────────────────────────
    private class Lim {
        var x0 = Double.POSITIVE_INFINITY; var x1 = Double.NEGATIVE_INFINITY
        var y0 = Double.POSITIVE_INFINITY; var y1 = Double.NEGATIVE_INFINITY
        var stickyY = false; var stickyX = false
        fun ax(v: Double) { if (v.isFinite()) { if (v < x0) x0 = v; if (v > x1) x1 = v } }
        fun ay(v: Double) { if (v.isFinite()) { if (v < y0) y0 = v; if (v > y1) y1 = v } }
    }

    private class HistCalc(val counts: DoubleArray, val edges: DoubleArray)

    private fun histOf(h: HistL): HistCalc {
        val vals = h.values.filter { it.isFinite() }.toDoubleArray()
        var counts: DoubleArray; val edges: DoubleArray
        if (h.edges != null) { edges = h.edges; counts = Np.histogramEdges(vals, edges) }
        else { val r = Np.histogram(vals, max(1, h.bins)); counts = r.first; edges = r.second }
        if (h.density) {
            val tot = counts.sum()
            counts = DoubleArray(counts.size) { i -> val w = edges[i + 1] - edges[i]; if (tot > 0 && w > 0) counts[i] / tot / w else 0.0 }
        }
        return HistCalc(counts, edges)
    }

    private fun limitsOf(layers: List<Layer>, hist: Map<HistL, HistCalc>): Lim {
        val l = Lim()
        for (ly in layers) when (ly) {
            is LineL -> { for (v in ly.x) l.ax(v); for (v in ly.y) l.ay(v) }
            is FillL -> { for (v in ly.x) l.ax(v); for (v in ly.y1) l.ay(v); for (v in ly.y2) l.ay(v) }
            is ScatterL -> { for (v in ly.x) l.ax(v); for (v in ly.y) l.ay(v) }
            is HistL -> { val hc = hist.getValue(ly); l.ax(hc.edges.first()); l.ax(hc.edges.last()); l.ay(0.0); for (c in hc.counts) l.ay(c); l.stickyY = true }
            is BarL -> {
                for (i in ly.pos.indices) {
                    val th = ly.thick[min(i, ly.thick.size - 1)]
                    if (ly.horizontal) { l.ay(ly.pos[i] - th / 2); l.ay(ly.pos[i] + th / 2); l.ax(0.0); l.ax(ly.len[i]) }
                    else { l.ax(ly.pos[i] - th / 2); l.ax(ly.pos[i] + th / 2); l.ay(0.0); l.ay(ly.len[i]) }
                }
                if (ly.horizontal) l.stickyX = true else l.stickyY = true
            }
            is HLineL -> l.ay(ly.y)
            is VLineL -> l.ax(ly.x)
            is HSpanL -> { l.ay(ly.y0); l.ay(ly.y1) }
            is TextL -> {}
        }
        return l
    }

    private fun axisRange(d0: Double, d1: Double, sticky: Boolean): DoubleArray {
        var lo = d0; var hi = d1
        if (!lo.isFinite() || !hi.isFinite()) return doubleArrayOf(0.0, 1.0)
        if (lo == hi) {
            if (lo == 0.0) return doubleArrayOf(-0.055, 0.055)
            val e = abs(lo) * 0.05
            return doubleArrayOf(lo - e - abs(lo) * 0.05, hi + e + abs(hi) * 0.05)
        }
        val m = (hi - lo) * 0.05
        var elo = lo - m; var ehi = hi + m
        if (sticky) { if (lo >= 0.0 && elo < 0.0) elo = 0.0; if (hi <= 0.0 && ehi > 0.0) ehi = 0.0 }
        return doubleArrayOf(elo, ehi)
    }

    // ── decimation of long monotonic series ─────────────────────────────────
    private fun isMonotonic(x: DoubleArray): Boolean {
        var prev = Double.NEGATIVE_INFINITY
        for (v in x) { if (v.isNaN()) return false; if (v < prev) return false; prev = v }
        return true
    }

    /** Indices to keep so a series of n points draws like itself in [cols] pixel columns (min/max per column). */
    private fun decimIdx(x: DoubleArray, y: DoubleArray, cols: Int, x0: Double, x1: Double): IntArray {
        val n = x.size
        if (n <= cols * 4 || !isMonotonic(x) || !(x1 > x0)) return IntArray(n) { it }
        val minI = IntArray(cols) { -1 }; val maxI = IntArray(cols) { -1 }
        for (i in 0 until n) {
            val yi = y[i]
            if (yi.isNaN()) continue
            val c = (((x[i] - x0) / (x1 - x0)) * (cols - 1)).toInt().coerceIn(0, cols - 1)
            if (minI[c] < 0 || yi < y[minI[c]]) minI[c] = i
            if (maxI[c] < 0 || yi > y[maxI[c]]) maxI[c] = i
        }
        val out = java.util.TreeSet<Int>()
        out.add(0); out.add(n - 1)
        for (c in 0 until cols) { if (minI[c] >= 0) out.add(minI[c]); if (maxI[c] >= 0) out.add(maxI[c]) }
        return out.toIntArray()
    }

    // ── main entry ───────────────────────────────────────────────────────────
    fun heightFor(p: Panel, w: Float, t: RTheme): Float {
        if (p is TextP && p.aspect <= 0f) {
            val pr = Paints(t)
            val size = textSizeFor(p, w, pr)
            val titleH = if (p.title != null) t.f(p.titleSize) * 1.6f + 8 * t.dens else 0f
            return titleH + p.lines.size * size * 1.2f + 20 * t.dens
        }
        return w * p.aspect
    }

    private fun textSizeFor(p: TextP, w: Float, pr: Paints): Float {
        val want = pr.t.f(p.size)
        val widest = p.lines.maxOfOrNull { it.length } ?: 1
        val fitSize = (w - 16 * pr.t.dens) / (widest * 0.6f)
        return min(want, max(fitSize, want * 0.62f))
    }

    fun draw(cv: Canvas, p: Panel, w: Float, h: Float, t: RTheme) {
        val pr = Paints(t)
        when (p) {
            is XY -> drawXY(cv, pr, p, w, h)
            is MapP -> drawMap(cv, pr, p, w, h)
            is Radar -> drawRadar(cv, pr, p, w, h)
            is TextP -> drawTextP(cv, pr, p, w, h)
            is Heat -> drawHeat(cv, pr, p, w, h)
        }
    }

    // ── colour bar ───────────────────────────────────────────────────────────
    private fun cbarWidth(pr: Paints, cb: Cb): Float {
        val t = pr.t
        val ticks = niceTicks(cb.vmin, cb.vmax, 5)
        val lab = fmtTicks(ticks)
        val lw = lab.maxOfOrNull { textW(pr, it, t.f(6f)) } ?: 0f
        return 6 * t.dens + 9 * t.dens + 4 * t.dens + lw
    }

    private fun drawCbar(cv: Canvas, pr: Paints, cmap: String, vmin: Double, vmax: Double, x: Float, top: Float, bot: Float) {
        val t = pr.t
        val sw = 9 * t.dens
        val lut = Cmaps.lut(cmap)
        val n = 64
        val hh = (bot - top) / n
        for (i in 0 until n) {
            pr.fill.color = if (cmap == "tab10") Cmaps.tab10(1.0 - (i + 0.5) / n) else lut[Cmaps.idx(1.0 - (i + 0.5) / n)]
            cv.drawRect(x, top + i * hh, x + sw, top + (i + 1) * hh + 0.6f, pr.fill)
        }
        val lo = vmin; var hi = vmax
        if (!(hi > lo)) hi = lo + 1.0
        val ticks = niceTicks(lo, hi, 5)
        val labs = fmtTicks(ticks)
        pr.stroke.color = t.dim; pr.stroke.strokeWidth = max(1f, t.g(0.6f)); pr.stroke.pathEffect = null
        for ((i, tk) in ticks.withIndex()) {
            if (tk < lo || tk > hi) continue
            val yy = bot - ((tk - lo) / (hi - lo)).toFloat() * (bot - top)
            cv.drawLine(x + sw, yy, x + sw + 3 * t.dens, yy, pr.stroke)
            text(cv, pr, labs[i], x + sw + 4 * t.dens, yy, t.f(6f), t.dim, 0f, 0.5f)
        }
    }

    // ── cartesian axes ───────────────────────────────────────────────────────
    private fun drawXY(cv: Canvas, pr: Paints, p: XY, w: Float, h: Float) {
        val t = pr.t; val d = t.dens
        val hist = HashMap<HistL, HistCalc>()
        for (ly in p.layers + p.twin) if (ly is HistL) hist[ly] = histOf(ly)

        val titleFit = if (p.title != null) fitTitle(pr, p.title, w - 12 * d, t.f(p.titleSize)) else null
        val titleH = if (titleFit != null) titleFit.first.split('\n').size * titleFit.second * 1.2f + 8 * d else 5 * d

        if (p.off) {
            // axis("off"): only text layers survive, in axes coordinates
            if (titleFit != null) text(cv, pr, titleFit.first, w / 2, titleH - 6 * d, titleFit.second, (p.titleColor ?: t.a.fg).toArgb(), 0.5f, 1f)
            val L = 0f; val T = titleH; val pw = w; val ph = h - titleH
            for (ly in p.layers) if (ly is TextL) drawTextLayer(cv, pr, ly, L, T, pw, ph, null, null)
            return
        }

        val lim = limitsOf(p.layers, hist)
        val xr = if (p.xlim != null) p.xlim else axisRange(lim.x0, lim.x1, lim.stickyX)
        val yr = if (p.ylim != null) p.ylim else axisRange(lim.y0, lim.y1, lim.stickyY)
        val hasTwin = p.twin.isNotEmpty()
        val limT = if (hasTwin) limitsOf(p.twin, hist) else null
        val yr2 = if (limT != null) axisRange(limT.y0, limT.y1, limT.stickyY) else null

        val tickSz = t.f(p.xTickSize)
        val labSz = t.f(7f)

        // tick candidates (count depends on the final rect, so estimate it first)
        val estW = w * 0.78f; val estH = (h - titleH) * 0.7f
        val nx = max(3, min(9, (estW / (tickSz * 5.5f)).toInt()))
        val ny = max(3, min(8, (estH / (tickSz * 3.2f)).toInt()))
        val xT: List<Pair<Double, String>> = p.xticks?.map { it.at to it.text }
            ?: niceTicks(xr[0], xr[1], nx).let { tk -> val lb = fmtTicks(tk); tk.indices.map { tk[it] to lb[it] } }
        val yT: List<Pair<Double, String>> = p.yticks?.map { it.at to it.text }
            ?: niceTicks(yr[0], yr[1], ny).let { tk -> val lb = fmtTicks(tk); tk.indices.map { tk[it] to lb[it] } }
        val yT2: List<Pair<Double, String>> = if (yr2 != null) niceTicks(yr2[0], yr2[1], ny).let { tk -> val lb = fmtTicks(tk); tk.indices.map { tk[it] to lb[it] } } else emptyList()

        // legend entries
        val entries = ArrayList<LegendE>()
        if (p.legend) for (ly in p.layers + p.twin) {
            when (ly) {
                is LineL -> if (ly.label != null) entries.add(LegendE(ly.label, ly.color.argb(ly.alpha), 0, ly.dash))
                is ScatterL -> if (ly.label != null) entries.add(LegendE(ly.label, ly.color.argb(max(ly.alpha, 0.8f)), 2, 0))
                is HistL -> if (ly.label != null) entries.add(LegendE(ly.label, ly.color.argb(max(ly.alpha, 0.6f)), 1, 0))
                is BarL -> if (ly.label != null) entries.add(LegendE(ly.label, (ly.colors.firstOrNull() ?: Color.Gray).argb(max(ly.alpha, 0.6f)), 1, 0))
                else -> {}
            }
        }

        val yLabW = if (p.ylabel != null) labSz * 1.3f else 0f
        val yTickW = if (p.noYTicks || p.noFrame) 0f else (yT.maxOfOrNull { textW(pr, it.second, tickSz) } ?: 0f) + 5 * d
        val left = max(6 * d, yLabW + yTickW + 5 * d)
        val tickH = if (p.noXTicks || p.noFrame) 0f else (xT.maxOfOrNull { it.second.split('\n').size } ?: 1) * tickSz * 1.2f + 5 * d
        var bottom = tickH + (if (p.xlabel != null) labSz * 1.3f + 3 * d else 0f) + 4 * d
        val rightTwin = if (hasTwin) (yT2.maxOfOrNull { textW(pr, it.second, tickSz) } ?: 0f) + 5 * d + (if (p.twinLabel != null) labSz * 1.3f else 0f) else 0f
        val cbW = if (p.cb != null) cbarWidth(pr, p.cb) + 4 * d else 0f
        val right = 8 * d + rightTwin + cbW
        // legend below the axes
        var legendBelowH = 0f
        val legSz = t.f(p.legendSize)
        if (p.legendBelow && entries.isNotEmpty()) {
            val rows = (entries.size + p.legendCols - 1) / p.legendCols
            legendBelowH = rows * legSz * 1.35f + 8 * d
            bottom += legendBelowH
        }
        val L = left; val T = titleH; val R = w - right; val B = h - bottom
        val pw = R - L; val ph = B - T
        if (pw < 10 || ph < 10) return

        val x0 = xr[0]; val x1 = xr[1]; val y0 = yr[0]; val y1 = yr[1]
        fun sx(v: Double) = (L + (v - x0) / (x1 - x0) * pw).toFloat()
        fun sy(v: Double) = (B - (v - y0) / (y1 - y0) * ph).toFloat()
        val y20 = yr2?.get(0) ?: 0.0; val y21 = yr2?.get(1) ?: 1.0
        fun sy2(v: Double) = (B - (v - y20) / (y21 - y20) * ph).toFloat()

        // title
        if (titleFit != null) text(cv, pr, titleFit.first, L + pw / 2, titleH - 6 * d, titleFit.second, (p.titleColor ?: t.a.fg).toArgb(), 0.5f, 1f)

        // face
        pr.fill.color = t.face
        cv.drawRect(L, T, R, B, pr.fill)

        val cols = max(50, pw.toInt())
        cv.save(); cv.clipRect(L, T, R, B)

        fun drawGrid() {
            if (!p.grid || p.noFrame) return
            pr.stroke.color = withAlpha(t.dim2, 0.4f); pr.stroke.strokeWidth = max(1f, t.g(0.8f)); pr.stroke.pathEffect = null
            if (p.gridAxis != 'y') for ((v, _) in xT) { if (v in x0..x1) cv.drawLine(sx(v), T, sx(v), B, pr.stroke) }
            if (p.gridAxis != 'x') for ((v, _) in yT) { if (v in y0..y1) cv.drawLine(L, sy(v), R, sy(v), pr.stroke) }
        }

        val allLayers = p.layers.map { it to false } + p.twin.map { it to true }
        fun z(ly: Layer) = when (ly) {
            is FillL, is HistL, is BarL, is HSpanL -> 1
            is ScatterL -> ly.z
            is TextL -> 4
            else -> 2
        }
        val ordered = allLayers.sortedBy { z(it.first) }
        var gridDone = false
        for ((ly, isTwin) in ordered) {
            if (!gridDone && z(ly) >= 2) { drawGrid(); gridDone = true }
            val fy: (Double) -> Float = if (isTwin) ::sy2 else ::sy
            when (ly) {
                is LineL -> drawLine(cv, pr, ly, ::sx, fy, x0, x1, cols)
                is FillL -> drawFill(cv, pr, ly, ::sx, fy, x0, x1, cols)
                is ScatterL -> drawScatter(cv, pr, ly, ::sx, fy, L, T, R, B)
                is HistL -> drawHist(cv, pr, ly, hist.getValue(ly), ::sx, fy)
                is BarL -> drawBars(cv, pr, ly, ::sx, fy)
                is HLineL -> {
                    pr.stroke.color = ly.color.argb(ly.alpha); pr.stroke.strokeWidth = max(1f, t.g(ly.lw)); pr.stroke.pathEffect = dashFor(ly.dash, t.g(ly.lw), t)
                    cv.drawLine(L, fy(ly.y), R, fy(ly.y), pr.stroke); pr.stroke.pathEffect = null
                }
                is VLineL -> {
                    pr.stroke.color = ly.color.argb(ly.alpha); pr.stroke.strokeWidth = max(1f, t.g(ly.lw)); pr.stroke.pathEffect = dashFor(ly.dash, t.g(ly.lw), t)
                    cv.drawLine(sx(ly.x), T, sx(ly.x), B, pr.stroke); pr.stroke.pathEffect = null
                }
                is HSpanL -> { pr.fill.color = ly.color.argb(ly.alpha); cv.drawRect(L, min(fy(ly.y0), fy(ly.y1)), R, max(fy(ly.y0), fy(ly.y1)), pr.fill) }
                is TextL -> {}
            }
        }
        if (!gridDone) drawGrid()
        cv.restore()

        // text layers (not clipped, like ax.text)
        for (ly in p.layers) if (ly is TextL) drawTextLayer(cv, pr, ly, L, T, pw, ph, ::sx, ::sy)

        // frame, ticks and labels
        if (!p.noFrame) {
            pr.stroke.color = t.dim; pr.stroke.strokeWidth = max(1f, t.g(0.8f)); pr.stroke.pathEffect = null
            cv.drawLine(L, T, L, B, pr.stroke); cv.drawLine(L, B, R, B, pr.stroke)
            if (hasTwin) cv.drawLine(R, T, R, B, pr.stroke)
            if (!p.noXTicks) for ((v, s) in xT) {
                if (v < x0 - 1e-9 || v > x1 + 1e-9) continue
                cv.drawLine(sx(v), B, sx(v), B + 3 * d, pr.stroke)
                text(cv, pr, s, sx(v), B + 4 * d, tickSz, t.dim, 0.5f, 1f)
            }
            if (!p.noYTicks) for ((v, s) in yT) {
                if (v < y0 - 1e-9 || v > y1 + 1e-9) continue
                cv.drawLine(L - 3 * d, sy(v), L, sy(v), pr.stroke)
                text(cv, pr, s, L - 4 * d, sy(v), tickSz, t.dim, 1f, 0.5f)
            }
            if (hasTwin) for ((v, s) in yT2) {
                if (v < y20 - 1e-9 || v > y21 + 1e-9) continue
                cv.drawLine(R, sy2(v), R + 3 * d, sy2(v), pr.stroke)
                text(cv, pr, s, R + 4 * d, sy2(v), tickSz, t.dim, 0f, 0.5f)
            }
        }
        if (p.xlabel != null) text(cv, pr, p.xlabel, L + pw / 2, B + tickH + 2 * d, labSz, t.dim, 0.5f, 1f)
        if (p.ylabel != null) text(cv, pr, p.ylabel, 2 * d + labSz * 0.6f, T + ph / 2, labSz, (p.ylabelColor?.toArgb() ?: t.dim), 0.5f, 0.5f, rotate = -90f)
        if (hasTwin && p.twinLabel != null) text(cv, pr, p.twinLabel, w - 4 * d - labSz * 0.5f - cbW, T + ph / 2, labSz, (p.twinColor ?: t.a.dim).toArgb(), 0.5f, 0.5f, rotate = 90f)

        if (p.cb != null) drawCbar(cv, pr, p.cb.cmap, p.cb.vmin, p.cb.vmax, R + 6 * d, T, B)

        // legend
        if (entries.isNotEmpty()) {
            val cols2 = max(1, p.legendCols)
            val rows = (entries.size + cols2 - 1) / cols2
            val rowH = legSz * 1.35f
            val sw = 18 * d
            val colW = entries.maxOf { textW(pr, it.label, legSz) } + sw + 12 * d
            val boxW = colW * cols2 + 6 * d; val boxH = rows * rowH + 6 * d
            val bx: Float; val by: Float
            if (p.legendBelow) { bx = L + pw / 2 - boxW / 2; by = B + tickH + (if (p.xlabel != null) labSz * 1.3f + 3 * d else 0f) + 8 * d }
            else { bx = R - boxW - 5 * d; by = T + 5 * d }
            if (!p.legendBelow) {
                pr.fill.color = withAlpha(t.pnl, 0.85f); cv.drawRoundRect(RectF(bx, by, bx + boxW, by + boxH), 3 * d, 3 * d, pr.fill)
                pr.stroke.color = t.dim; pr.stroke.strokeWidth = max(1f, t.g(0.6f)); pr.stroke.pathEffect = null
                cv.drawRoundRect(RectF(bx, by, bx + boxW, by + boxH), 3 * d, 3 * d, pr.stroke)
            }
            for ((i, e) in entries.withIndex()) {
                val c = i % cols2; val r = i / cols2
                val ex = bx + 4 * d + c * colW; val ey = by + 3 * d + r * rowH + rowH / 2
                when (e.kind) {
                    0 -> { pr.stroke.color = e.color; pr.stroke.strokeWidth = max(1.5f, t.g(1.5f)); pr.stroke.pathEffect = dashFor(e.dash, t.g(1.5f), t); cv.drawLine(ex, ey, ex + sw - 4 * d, ey, pr.stroke); pr.stroke.pathEffect = null }
                    1 -> { pr.fill.color = e.color; cv.drawRect(ex, ey - legSz * 0.35f, ex + sw - 4 * d, ey + legSz * 0.35f, pr.fill) }
                    else -> { pr.fill.color = e.color; cv.drawCircle(ex + (sw - 4 * d) / 2, ey, max(2.5f * d, legSz * 0.3f), pr.fill) }
                }
                text(cv, pr, e.label, ex + sw, ey, legSz, t.fg, 0f, 0.5f)
            }
        }
    }

    private class LegendE(val label: String, val color: Int, val kind: Int, val dash: Int)

    private fun drawTextLayer(cv: Canvas, pr: Paints, ly: TextL, L: Float, T: Float, pw: Float, ph: Float,
                              sx: ((Double) -> Float)?, sy: ((Double) -> Float)?) {
        val t = pr.t
        val x: Float; val y: Float
        if (ly.axes || sx == null || sy == null) { x = L + ly.x.toFloat() * pw; y = T + ph - ly.y.toFloat() * ph }
        else { x = sx(ly.x); y = sy(ly.y) }
        val size = t.f(ly.size)
        if (ly.bbox != null) {
            val tw = textW(pr, ly.text, size, ly.bold)
            val th = ly.text.split('\n').size * size * 1.2f
            val bx = x - tw * ly.ha - 2 * t.dens; val by = y - th * (1 - ly.va) - 1 * t.dens
            pr.fill.color = ly.bbox.argb(0.85f)
            cv.drawRoundRect(RectF(bx, by, bx + tw + 4 * t.dens, by + th + 2 * t.dens), 3 * t.dens, 3 * t.dens, pr.fill)
        }
        text(cv, pr, ly.text, x, y, size, ly.color.toArgb(), ly.ha, ly.va, ly.bold)
    }

    private fun stepXY(x: DoubleArray, y: DoubleArray, idx: IntArray, step: Step): Pair<DoubleArray, DoubleArray> {
        val m = idx.size
        if (step == Step.NONE || m < 2) return DoubleArray(m) { x[idx[it]] } to DoubleArray(m) { y[idx[it]] }
        if (step == Step.POST) {
            val rx = DoubleArray(2 * m - 1); val ry = DoubleArray(2 * m - 1)
            var q = 0
            rx[q] = x[idx[0]]; ry[q] = y[idx[0]]; q++
            for (k in 1 until m) { rx[q] = x[idx[k]]; ry[q] = y[idx[k - 1]]; q++; if (q < rx.size) { rx[q] = x[idx[k]]; ry[q] = y[idx[k]]; q++ } }
            return rx.copyOf(q) to ry.copyOf(q)
        }
        // MID: x0,y0 -> mid01,y0 -> mid01,y1 -> ... -> xn,yn
        val rx = ArrayList<Double>(); val ry = ArrayList<Double>()
        rx.add(x[idx[0]]); ry.add(y[idx[0]])
        for (k in 1 until m) {
            val mid = (x[idx[k - 1]] + x[idx[k]]) / 2
            rx.add(mid); ry.add(y[idx[k - 1]]); rx.add(mid); ry.add(y[idx[k]])
        }
        rx.add(x[idx[m - 1]]); ry.add(y[idx[m - 1]])
        return rx.toDoubleArray() to ry.toDoubleArray()
    }

    private fun drawLine(cv: Canvas, pr: Paints, ly: LineL, sx: (Double) -> Float, sy: (Double) -> Float, x0: Double, x1: Double, cols: Int) {
        val t = pr.t
        val idx = decimIdx(ly.x, ly.y, cols, x0, x1)
        val (xs, ys) = stepXY(ly.x, ly.y, idx, ly.step)
        val path = Path()
        var pen = false
        for (i in xs.indices) {
            val xv = xs[i]; val yv = ys[i]
            if (!xv.isFinite() || !yv.isFinite()) { pen = false; continue }
            if (!pen) { path.moveTo(sx(xv), sy(yv)); pen = true } else path.lineTo(sx(xv), sy(yv))
        }
        val lw = max(1f, t.g(ly.lw))
        pr.stroke.color = ly.color.argb(ly.alpha); pr.stroke.strokeWidth = lw
        pr.stroke.pathEffect = dashFor(ly.dash, lw, t)
        cv.drawPath(path, pr.stroke)
        pr.stroke.pathEffect = null
        if (ly.marker > 0f) {
            pr.fill.color = ly.color.argb(ly.alpha)
            val rr = t.g(ly.marker) / 2f * 1.4f
            for (i in ly.x.indices) {
                if (!ly.x[i].isFinite() || !ly.y[i].isFinite()) continue
                if (ly.markerSquare) cv.drawRect(sx(ly.x[i]) - rr, sy(ly.y[i]) - rr, sx(ly.x[i]) + rr, sy(ly.y[i]) + rr, pr.fill)
                else cv.drawCircle(sx(ly.x[i]), sy(ly.y[i]), rr, pr.fill)
            }
        }
    }

    private fun drawFill(cv: Canvas, pr: Paints, ly: FillL, sx: (Double) -> Float, sy: (Double) -> Float, x0: Double, x1: Double, cols: Int) {
        val n = ly.x.size
        if (n < 2) return
        val idx = decimIdx(ly.x, ly.y1, cols, x0, x1)
        val m = idx.size
        pr.fill.color = ly.color.argb(ly.alpha)
        // runs of consecutive "where" samples
        var i = 0
        while (i < m) {
            val ok = ly.where?.get(idx[i]) ?: true
            if (!ok) { i++; continue }
            var j = i
            while (j + 1 < m && (ly.where?.get(idx[j + 1]) ?: true)) j++
            val sub = IntArray(j - i + 1) { idx[i + it] }
            if (sub.size >= 2 || ly.step != Step.NONE) {
                val (tx, t1) = stepXY(ly.x, ly.y1, sub, ly.step)
                val (_, t2) = stepXY(ly.x, ly.y2, sub, ly.step)
                val path = Path()
                var started = false
                for (k in tx.indices) { if (!t1[k].isFinite()) continue; if (!started) { path.moveTo(sx(tx[k]), sy(t1[k])); started = true } else path.lineTo(sx(tx[k]), sy(t1[k])) }
                for (k in tx.indices.reversed()) { if (!t2[k].isFinite()) continue; path.lineTo(sx(tx[k]), sy(t2[k])) }
                path.close()
                cv.drawPath(path, pr.fill)
            }
            i = j + 1
        }
    }

    private fun drawScatter(cv: Canvas, pr: Paints, ly: ScatterL, sx: (Double) -> Float, sy: (Double) -> Float,
                            L: Float, T: Float, R: Float, B: Float) {
        val t = pr.t
        val n = min(ly.x.size, ly.y.size)
        if (n == 0) return
        val stride = max(1, n / 20000)
        val dia = max(1.7f * t.dens * 0.6f, sqrt(ly.s) * t.g(1f) * 1.15f)
        pr.dot.strokeWidth = dia
        if (ly.c == null || ly.cmap == null) {
            pr.dot.color = ly.color.argb(ly.alpha)
            val buf = FloatArray(2 * (n / stride + 1)); var q = 0
            var i = 0
            while (i < n) { if (ly.x[i].isFinite() && ly.y[i].isFinite()) { buf[q++] = sx(ly.x[i]); buf[q++] = sy(ly.y[i]) }; i += stride }
            cv.drawPoints(buf, 0, q, pr.dot)
            return
        }
        val c = ly.c
        var lo = ly.vmin ?: Double.POSITIVE_INFINITY; var hi = ly.vmax ?: Double.NEGATIVE_INFINITY
        if (ly.vmin == null || ly.vmax == null) for (v in c) if (v.isFinite()) { if (ly.vmin == null && v < lo) lo = v; if (ly.vmax == null && v > hi) hi = v }
        val bins = 48
        val tmp = Array(bins) { ArrayList<Float>() }
        var i = 0
        while (i < n) {
            val v = c[i]
            if (ly.x[i].isFinite() && ly.y[i].isFinite() && v.isFinite()) {
                val k = (((v - lo) / (if (hi > lo) hi - lo else 1.0)) * (bins - 1)).roundToInt().coerceIn(0, bins - 1)
                tmp[k].add(sx(ly.x[i])); tmp[k].add(sy(ly.y[i]))
            }
            i += stride
        }
        for (k in 0 until bins) {
            if (tmp[k].isEmpty()) continue
            val arr = tmp[k].toFloatArray()
            val col = Cmaps.at(ly.cmap, k.toDouble() / (bins - 1))
            pr.dot.color = withAlpha(col, ly.alpha)
            cv.drawPoints(arr, pr.dot)
        }
    }

    private fun drawHist(cv: Canvas, pr: Paints, ly: HistL, hc: HistCalc, sx: (Double) -> Float, sy: (Double) -> Float) {
        val t = pr.t
        val e = hc.edges; val c = hc.counts
        if (ly.outline) {
            val path = Path()
            path.moveTo(sx(e[0]), sy(0.0))
            for (i in c.indices) { path.lineTo(sx(e[i]), sy(c[i])); path.lineTo(sx(e[i + 1]), sy(c[i])) }
            path.lineTo(sx(e[e.size - 1]), sy(0.0))
            pr.stroke.color = ly.color.argb(max(ly.alpha, 0.85f)); pr.stroke.strokeWidth = max(1f, t.g(ly.lw)); pr.stroke.pathEffect = null
            cv.drawPath(path, pr.stroke)
        } else {
            pr.fill.color = ly.color.argb(ly.alpha)
            for (i in c.indices) {
                val bw = e[i + 1] - e[i]; val pad = bw * (1 - ly.rwidth) / 2
                val xa = sx(e[i] + pad); val xb = sx(e[i + 1] - pad)
                cv.drawRect(min(xa, xb), min(sy(c[i]), sy(0.0)), max(xa, xb), max(sy(c[i]), sy(0.0)), pr.fill)
            }
        }
    }

    private fun drawBars(cv: Canvas, pr: Paints, ly: BarL, sx: (Double) -> Float, sy: (Double) -> Float) {
        val t = pr.t
        for (i in ly.pos.indices) {
            val col = ly.colors[min(i, ly.colors.size - 1)].argb(ly.alpha)
            val th = ly.thick[min(i, ly.thick.size - 1)]
            val v = ly.len[i]
            if (!v.isFinite()) continue
            pr.fill.color = col
            val r = if (ly.horizontal) RectF(min(sx(0.0), sx(v)), min(sy(ly.pos[i] - th / 2), sy(ly.pos[i] + th / 2)),
                max(sx(0.0), sx(v)), max(sy(ly.pos[i] - th / 2), sy(ly.pos[i] + th / 2)))
            else RectF(min(sx(ly.pos[i] - th / 2), sx(ly.pos[i] + th / 2)), min(sy(0.0), sy(v)),
                max(sx(ly.pos[i] - th / 2), sx(ly.pos[i] + th / 2)), max(sy(0.0), sy(v)))
            cv.drawRect(r, pr.fill)
            if (ly.hatch) {
                pr.stroke.color = withAlpha(0xFFFFFFFF.toInt(), 0.35f); pr.stroke.strokeWidth = max(1f, t.g(0.6f)); pr.stroke.pathEffect = null
                cv.save(); cv.clipRect(r)
                var k = r.left - r.height()
                val stepPx = 6 * t.dens
                while (k < r.right) { cv.drawLine(k, r.bottom, k + r.height(), r.top, pr.stroke); k += stepPx }
                cv.restore()
            }
        }
    }

    // ── track map ────────────────────────────────────────────────────────────
    private fun drawMap(cv: Canvas, pr: Paints, p: MapP, w: Float, h: Float) {
        val t = pr.t; val d = t.dens
        val titleFit = if (p.title != null) fitTitle(pr, p.title, w - 12 * d, t.f(p.titleSize)) else null
        val titleH = if (titleFit != null) titleFit.first.split('\n').size * titleFit.second * 1.2f + 8 * d else 5 * d
        if (titleFit != null) text(cv, pr, titleFit.first, w / 2, titleH - 6 * d, titleFit.second, (p.titleColor ?: t.a.fg).toArgb(), 0.5f, 1f)
        val hasCb = p.v != null && p.cmap != null
        val cb = if (hasCb) Cb(p.cmap!!, p.v!!.filter { it.isFinite() }.minOrNull() ?: 0.0, p.v.filter { it.isFinite() }.maxOrNull() ?: 1.0) else null
        val cbW = if (cb != null) cbarWidth(pr, cb) + 10 * d else 0f
        val L = 4 * d; val T = titleH; val R = w - 4 * d - cbW; val B = h - 4 * d
        val xs = ArrayList<Double>(); val zs = ArrayList<Double>()
        for (i in p.x.indices) { if (p.x[i].isFinite() && p.z[i].isFinite()) { xs.add(p.x[i]); zs.add(p.z[i]) } }
        for (ln in p.lines) for (i in ln.x.indices) { if (ln.x[i].isFinite() && ln.z[i].isFinite()) { xs.add(ln.x[i]); zs.add(ln.z[i]) } }
        if (xs.isEmpty()) return
        val xmin = xs.min(); val xmax = xs.max(); val zmin = zs.min(); val zmax = zs.max()
        val dx = max(xmax - xmin, 1e-6); val dz = max(zmax - zmin, 1e-6)
        val pw = R - L; val ph = B - T
        val sc = min(pw / (dx * 1.1), ph / (dz * 1.1)).toFloat()
        val cx = (L + R) / 2; val cy = (T + B) / 2
        val mx = (xmin + xmax) / 2; val mz = (zmin + zmax) / 2
        fun sx(v: Double) = cx + ((v - mx) * sc).toFloat()
        fun sy(v: Double) = cy - ((v - mz) * sc).toFloat()
        pr.stroke.pathEffect = null
        pr.stroke.strokeCap = Paint.Cap.ROUND
        if (p.v != null && p.cmap != null && cb != null) {
            val n = p.x.size
            val bins = 96
            val tmp = Array(bins) { ArrayList<Float>() }
            val lo = cb.vmin; val hi = cb.vmax
            val stride = if (n > 6000) 2 else 1
            var i = 0
            while (i < n - 1) {
                val xa = p.x[i]; val za = p.z[i]; val xb = p.x[i + 1]; val zb = p.z[i + 1]
                val v = p.v[i]
                if (xa.isFinite() && za.isFinite() && xb.isFinite() && zb.isFinite() && v.isFinite()) {
                    val k = (((v - lo) / (if (hi > lo) hi - lo else 1.0)) * (bins - 1)).roundToInt().coerceIn(0, bins - 1)
                    val tk = tmp[k]
                    tk.add(sx(xa)); tk.add(sy(za)); tk.add(sx(xb)); tk.add(sy(zb))
                }
                i += stride
            }
            pr.stroke.strokeWidth = max(1.5f, t.g(p.lw) * 1.15f)
            for (k in 0 until bins) {
                if (tmp[k].isEmpty()) continue
                val tt = if (hi > lo) k.toDouble() / (bins - 1) else 0.0
                pr.stroke.color = withAlpha(if (p.cmap == "tab10") Cmaps.tab10(tt) else Cmaps.at(p.cmap, tt), p.alpha)
                cv.drawLines(tmp[k].toFloatArray(), pr.stroke)
            }
            drawCbar(cv, pr, p.cmap, cb.vmin, cb.vmax, R + 10 * d, T + ph * 0.1f, B - ph * 0.1f)
        }
        for (ln in p.lines) {
            val path = Path(); var pen = false
            for (i in ln.x.indices) {
                if (!ln.x[i].isFinite() || !ln.z[i].isFinite()) { pen = false; continue }
                if (!pen) { path.moveTo(sx(ln.x[i]), sy(ln.z[i])); pen = true } else path.lineTo(sx(ln.x[i]), sy(ln.z[i]))
            }
            pr.stroke.color = ln.color.argb(ln.alpha); pr.stroke.strokeWidth = max(1.5f, t.g(ln.lw) * 1.15f)
            cv.drawPath(path, pr.stroke)
        }
        pr.stroke.strokeCap = Paint.Cap.BUTT
        for (lb in p.labels) {
            val size = t.f(6f)
            val tw = textW(pr, lb.text, size)
            val x = sx(lb.x); val y = sy(lb.z)
            pr.fill.color = lb.bg.argb(0.85f)
            cv.drawRoundRect(RectF(x - tw / 2 - 3 * d, y - size * 0.7f, x + tw / 2 + 3 * d, y + size * 0.7f), 3 * d, 3 * d, pr.fill)
            text(cv, pr, lb.text, x, y, size, 0xFFFFFFFF.toInt(), 0.5f, 0.5f)
        }
    }

    // ── radar ────────────────────────────────────────────────────────────────
    private fun drawRadar(cv: Canvas, pr: Paints, p: Radar, w: Float, h: Float) {
        val t = pr.t; val d = t.dens
        val titleFit = if (p.title != null) fitTitle(pr, p.title, w - 12 * d, t.f(p.titleSize)) else null
        val titleH = (if (titleFit != null) titleFit.first.split('\n').size * titleFit.second * 1.2f + 8 * d else 5 * d) + 14 * d
        if (titleFit != null) text(cv, pr, titleFit.first, w / 2, titleH - 20 * d, titleFit.second, (p.titleColor ?: t.a.fg).toArgb(), 0.5f, 1f)
        val cx = w / 2; val cy = titleH + (h - titleH) / 2
        val r = min(w / 2 - 44 * d, (h - titleH) / 2 - 22 * d)
        if (r <= 10) return
        pr.fill.color = t.face; cv.drawCircle(cx, cy, r, pr.fill)
        val n = p.labels.size
        pr.stroke.pathEffect = null; pr.stroke.color = withAlpha(t.dim, 0.5f); pr.stroke.strokeWidth = max(1f, t.g(0.8f))
        for (k in 1..5) cv.drawCircle(cx, cy, r * k / 5f, pr.stroke.also { it.style = Paint.Style.STROKE })
        for (i in 0 until n) {
            val a = 2 * PI * i / n
            cv.drawLine(cx, cy, cx + (r * sin(a)).toFloat(), cy - (r * cos(a)).toFloat(), pr.stroke)
            val lx = cx + (r * 1.2 * sin(a)).toFloat(); val ly = cy - (r * 1.2 * cos(a)).toFloat()
            val ha = if (abs(sin(a)) < 0.2) 0.5f else if (sin(a) > 0) 0f else 1f
            val va = if (abs(cos(a)) < 0.2) 0.5f else if (cos(a) > 0) 0f else 1f
            text(cv, pr, p.labels[i], lx, ly, t.f(6f), t.fg, ha, va)
        }
        for (s in p.series) {
            val path = Path()
            for (i in 0..n) {
                val a = 2 * PI * (i % n) / n
                val v = s.values[i % n].coerceIn(0.0, 1.0)
                val x = cx + (r * v * sin(a)).toFloat(); val y = cy - (r * v * cos(a)).toFloat()
                if (i == 0) path.moveTo(x, y) else path.lineTo(x, y)
            }
            path.close()
            pr.fill.color = s.color.argb(s.fillAlpha); cv.drawPath(path, pr.fill)
            pr.stroke.color = s.color.argb(); pr.stroke.strokeWidth = max(1.5f, t.g(1.5f))
            pr.stroke.pathEffect = if (s.dash) dashFor(1, t.g(1.5f), t) else null
            cv.drawPath(path, pr.stroke); pr.stroke.pathEffect = null
        }
    }

    // ── text panel ───────────────────────────────────────────────────────────
    private fun drawTextP(cv: Canvas, pr: Paints, p: TextP, w: Float, h: Float) {
        val t = pr.t; val d = t.dens
        val titleFit = if (p.title != null) fitTitle(pr, p.title, w - 12 * d, t.f(p.titleSize)) else null
        val titleH = if (titleFit != null) titleFit.first.split('\n').size * titleFit.second * 1.2f + 8 * d else 0f
        if (titleFit != null) text(cv, pr, titleFit.first, w / 2, titleH - 6 * d, titleFit.second, (p.titleColor ?: t.a.fg).toArgb(), 0.5f, 1f)
        if (p.bg) { pr.fill.color = t.face; cv.drawRect(0f, titleH, w, h, pr.fill) }
        val size = textSizeFor(p, w, pr)
        if (p.center) {
            text(cv, pr, p.lines.joinToString("\n"), w / 2, titleH + (h - titleH) / 2, size, p.color.toArgb(), 0.5f, 0.5f)
        } else {
            text(cv, pr, p.lines.joinToString("\n"), 8 * d, titleH + 8 * d, size, p.color.toArgb(), 0f, 1f)
        }
    }

    // ── imshow ───────────────────────────────────────────────────────────────
    private fun drawHeat(cv: Canvas, pr: Paints, p: Heat, w: Float, h: Float) {
        val t = pr.t; val d = t.dens
        val rows = p.values.size; val cols = p.values.firstOrNull()?.size ?: 0
        if (rows == 0 || cols == 0) return
        val titleFit = if (p.title != null) fitTitle(pr, p.title, w - 12 * d, t.f(p.titleSize)) else null
        val titleH = if (titleFit != null) titleFit.first.split('\n').size * titleFit.second * 1.2f + 8 * d else 5 * d
        if (titleFit != null) text(cv, pr, titleFit.first, w / 2, titleH - 6 * d, titleFit.second, (p.titleColor ?: t.a.fg).toArgb(), 0.5f, 1f)
        val tickSz = t.f(7f)
        val yLabW = (p.yLabels?.maxOfOrNull { textW(pr, it, tickSz) } ?: 0f) + (if (p.yLabels != null) 6 * d else 0f)
        val xLabH = if (p.xLabels != null) tickSz * 1.2f + 6 * d else 0f
        val cb = if (p.cb) Cb(p.cmap, p.vmin, p.vmax) else null
        val cbW = if (cb != null) cbarWidth(pr, cb) + 8 * d else 0f
        val L = yLabW + 4 * d; val T = titleH; val R = w - 6 * d - cbW; val B = h - xLabH - 4 * d
        val cw = (R - L) / cols; val ch = (B - T) / rows
        val lut = Cmaps.lut(p.cmap)
        pr.fill.color = t.face; cv.drawRect(L, T, R, B, pr.fill)
        for (i in 0 until rows) for (j in 0 until cols) {
            val v = p.values[i][j]
            if (!v.isFinite()) continue
            val tt = if (p.vmax > p.vmin) ((v - p.vmin) / (p.vmax - p.vmin)) else 0.0
            pr.fill.color = lut[Cmaps.idx(tt)]
            cv.drawRect(L + j * cw, T + i * ch, L + (j + 1) * cw + 0.5f, T + (i + 1) * ch + 0.5f, pr.fill)
            val s = p.cellText?.get(i)?.get(j)
            if (s != null) text(cv, pr, s, L + (j + 0.5f) * cw, T + (i + 0.5f) * ch, t.f(p.cellSize), p.cellColor.toArgb(), 0.5f, 0.5f, bold = true)
        }
        p.xLabels?.let { xl ->
            val every = max(1, ceil(xl.size * (tickSz * 3.5f) / (R - L)).toInt())
            for (j in xl.indices) if (j % every == 0) text(cv, pr, xl[j], L + (j + 0.5f) * cw, B + 4 * d, tickSz, t.dim, 0.5f, 1f)
        }
        p.yLabels?.let { yl ->
            val every = max(1, ceil(yl.size * (tickSz * 1.4f) / (B - T)).toInt())
            for (i in yl.indices) if (i % every == 0) text(cv, pr, yl[i], L - 4 * d, T + (i + 0.5f) * ch, tickSz, t.dim, 1f, 0.5f)
        }
        if (cb != null) drawCbar(cv, pr, p.cmap, p.vmin, p.vmax, R + 8 * d, T, B)
    }
}
