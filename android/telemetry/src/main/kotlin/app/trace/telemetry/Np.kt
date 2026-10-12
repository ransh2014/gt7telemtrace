package app.trace.telemetry

import kotlin.math.*

/** numpy / pandas semantics used by the analysts, on plain DoubleArrays. */
object Np {
    fun linspace(a: Double, b: Double, n: Int): DoubleArray {
        if (n <= 0) return DoubleArray(0)
        if (n == 1) return doubleArrayOf(a)
        val step = (b - a) / (n - 1)
        return DoubleArray(n) { if (it == n - 1) b else a + it * step }
    }

    fun arange(n: Int, step: Double = 1.0) = DoubleArray(n) { it * step }

    /** pandas Series.diff(): first element NaN. */
    fun diff(a: DoubleArray): DoubleArray {
        val o = DoubleArray(a.size)
        if (a.isEmpty()) return o
        o[0] = Double.NaN
        for (i in 1 until a.size) o[i] = a[i] - a[i - 1]
        return o
    }

    /** numpy diff (length n-1). */
    fun diffN(a: DoubleArray): DoubleArray = DoubleArray(max(0, a.size - 1)) { a[it + 1] - a[it] }

    fun fillNaN(a: DoubleArray, v: Double) = DoubleArray(a.size) { if (a[it].isNaN()) v else a[it] }

    fun clip(a: DoubleArray, lo: Double, hi: Double) =
        DoubleArray(a.size) { val x = a[it]; if (x.isNaN()) x else min(hi, max(lo, x)) }

    fun median(a: DoubleArray): Double {
        if (a.isEmpty()) return Double.NaN
        val s = a.copyOf(); s.sort()
        val n = s.size
        return if (n % 2 == 1) s[n / 2] else (s[n / 2 - 1] + s[n / 2]) / 2.0
    }

    fun mean(a: DoubleArray): Double {
        if (a.isEmpty()) return Double.NaN
        var s = 0.0
        for (x in a) s += x
        return s / a.size
    }

    /** ddof=1 is pandas' default, ddof=0 numpy's. */
    fun std(a: DoubleArray, ddof: Int = 1): Double {
        val n = a.size
        if (n - ddof <= 0) return Double.NaN
        val m = mean(a)
        var s = 0.0
        for (x in a) s += (x - m) * (x - m)
        return sqrt(s / (n - ddof))
    }

    fun max(a: DoubleArray): Double { var m = Double.NEGATIVE_INFINITY; for (x in a) if (x > m) m = x; return if (a.isEmpty()) Double.NaN else m }
    fun min(a: DoubleArray): Double { var m = Double.POSITIVE_INFINITY; for (x in a) if (x < m) m = x; return if (a.isEmpty()) Double.NaN else m }
    fun sum(a: DoubleArray): Double { var s = 0.0; for (x in a) s += x; return s }

    fun cumsum(a: DoubleArray): DoubleArray { var s = 0.0; return DoubleArray(a.size) { s += a[it]; s } }
    fun cummax(a: DoubleArray): DoubleArray { var m = Double.NEGATIVE_INFINITY; return DoubleArray(a.size) { if (a[it] > m) m = a[it]; m } }
    fun nanToNum(a: DoubleArray) = DoubleArray(a.size) { val x = a[it]; if (x.isNaN()) 0.0 else if (x == Double.POSITIVE_INFINITY) Double.MAX_VALUE else if (x == Double.NEGATIVE_INFINITY) -Double.MAX_VALUE else x }

    /** np.interp: clamps outside the range. xp must be non-decreasing. */
    fun interp(x: Double, xp: DoubleArray, fp: DoubleArray): Double {
        val n = xp.size
        if (n == 0) return Double.NaN
        if (x.isNaN()) return Double.NaN
        if (x <= xp[0] && (x < xp[0] || n == 1)) return fp[0]
        if (x >= xp[n - 1]) return fp[n - 1]
        // last j with xp[j] <= x
        var lo = 0; var hi = n - 1
        while (hi - lo > 1) { val mid = (lo + hi) ushr 1; if (xp[mid] <= x) lo = mid else hi = mid }
        val j = lo
        val slope = (fp[j + 1] - fp[j]) / (xp[j + 1] - xp[j])
        var r = slope * (x - xp[j]) + fp[j]
        if (r.isNaN()) { r = slope * (x - xp[j + 1]) + fp[j + 1] }
        return r
    }

    fun interp(x: DoubleArray, xp: DoubleArray, fp: DoubleArray) = DoubleArray(x.size) { interp(x[it], xp, fp) }

    /** np.searchsorted(a, v, side). */
    fun searchsorted(a: DoubleArray, v: Double, right: Boolean): Int {
        var lo = 0; var hi = a.size
        while (lo < hi) {
            val mid = (lo + hi) ushr 1
            val goLeft = if (right) v < a[mid] else v <= a[mid]
            if (goLeft) hi = mid else lo = mid + 1
        }
        return lo
    }

    /** np.percentile, linear interpolation. */
    fun percentile(a: DoubleArray, q: Double): Double {
        if (a.isEmpty()) return Double.NaN
        val s = a.copyOf(); s.sort()
        val pos = (s.size - 1) * q / 100.0
        val lo = floor(pos).toInt(); val hi = min(s.size - 1, lo + 1)
        return s[lo] + (s[hi] - s[lo]) * (pos - lo)
    }

    /** np.polyfit(x, y, 1) -> (slope, intercept). */
    fun polyfit1(x: DoubleArray, y: DoubleArray): Pair<Double, Double> {
        val mx = mean(x); val my = mean(y)
        var sxx = 0.0; var sxy = 0.0
        for (i in x.indices) { sxx += (x[i] - mx) * (x[i] - mx); sxy += (x[i] - mx) * (y[i] - my) }
        val m = if (sxx == 0.0) 0.0 else sxy / sxx
        return m to (my - m * mx)
    }

    /** pandas rolling(win, min_periods=1).mean(). */
    fun rollingMean(a: DoubleArray, win: Int): DoubleArray {
        val o = DoubleArray(a.size)
        var s = 0.0
        for (i in a.indices) {
            s += a[i]
            if (i >= win) s -= a[i - win]
            o[i] = s / min(i + 1, win)
        }
        return o
    }

    /** np.histogram(values, bins): counts and edges (last bin closed). */
    fun histogram(v: DoubleArray, bins: Int, range: Pair<Double, Double>? = null): Pair<DoubleArray, DoubleArray> {
        if (v.isEmpty()) return DoubleArray(bins) to linspace(0.0, 1.0, bins + 1)
        var lo = range?.first ?: min(v); var hi = range?.second ?: max(v)
        if (lo == hi) { lo -= 0.5; hi += 0.5 }
        val edges = linspace(lo, hi, bins + 1)
        val counts = DoubleArray(bins)
        for (x in v) {
            if (x < lo || x > hi || x.isNaN()) continue
            var k = ((x - lo) / (hi - lo) * bins).toInt()
            if (k >= bins) k = bins - 1
            // correct for floating point at the edges, as numpy does
            if (x < edges[k]) k-- else if (k + 1 < bins && x >= edges[k + 1]) k++
            counts[k.coerceIn(0, bins - 1)] += 1.0
        }
        return counts to edges
    }

    fun histogramEdges(v: DoubleArray, edges: DoubleArray): DoubleArray {
        val counts = DoubleArray(edges.size - 1)
        for (x in v) {
            if (x.isNaN() || x < edges[0] || x > edges[edges.size - 1]) continue
            var k = searchsorted(edges, x, true) - 1
            if (k >= counts.size) k = counts.size - 1
            counts[k] += 1.0
        }
        return counts
    }
}

/** Python's str.title(). */
fun pyTitle(s: String): String {
    val sb = StringBuilder(); var prevLetter = false
    for (ch in s) {
        if (ch.isLetter()) { sb.append(if (prevLetter) ch.lowercaseChar() else ch.uppercaseChar()); prevLetter = true }
        else { sb.append(ch); prevLetter = false }
    }
    return sb.toString()
}

/** Python's f"{x:.{d}f}" (exact binary value, half-even, keeps the sign of -0.00). */
fun fixed(x: Double, d: Int): String {
    if (x.isNaN()) return "nan"
    if (x.isInfinite()) return if (x > 0) "inf" else "-inf"
    val body = java.math.BigDecimal(abs(x)).setScale(d, java.math.RoundingMode.HALF_EVEN).toPlainString()
    val neg = x < 0 || (x == 0.0 && 1.0 / x < 0)
    return if (neg) "-$body" else body
}

fun signed(x: Double, d: Int): String = (if (x >= 0 || x.isNaN()) "+" else "") + fixed(x, d)
