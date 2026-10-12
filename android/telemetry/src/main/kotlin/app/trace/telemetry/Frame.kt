package app.trace.telemetry

/** A small column store standing in for the pandas DataFrame the PC analysts use. */
class Frame(val n: Int) {
    val cols = LinkedHashMap<String, DoubleArray>()
    val strs = LinkedHashMap<String, Array<String>>()
    /** leaderboard ghost: only the six uploaded input channels. */
    var compact = false
    var tDerived = false
    var posDerived = false

    operator fun get(name: String): DoubleArray = cols[name] ?: throw NoSuchElementException("column $name")
    operator fun set(name: String, v: DoubleArray) { require(v.size == n) { "column $name has ${v.size} rows, frame has $n" }; cols[name] = v }
    fun has(name: String) = cols.containsKey(name)
    fun str(name: String): Array<String> = strs[name] ?: Array(n) { "" }

    private fun like(m: Int): Frame { val f = Frame(m); f.compact = compact; f.tDerived = tDerived; f.posDerived = posDerived; return f }

    /** Rows by index (pandas .iloc[idx]). */
    fun take(idx: IntArray): Frame {
        val f = like(idx.size)
        for ((k, v) in cols) f.cols[k] = DoubleArray(idx.size) { v[idx[it]] }
        for ((k, v) in strs) f.strs[k] = Array(idx.size) { v[idx[it]] }
        return f
    }

    /** Rows [a, b). */
    fun slice(a: Int, b: Int): Frame = take(IntArray(maxOf(0, b - a)) { a + it })

    fun where(mask: BooleanArray): Frame = take(mask.indices.filter { mask[it] }.toIntArray())

    /** Stable sort by a numeric column. */
    fun sortedBy(col: String): Frame {
        val c = this[col]
        val idx = (0 until n).sortedWith { a, b -> c[a].compareTo(c[b]) }.toIntArray()
        return take(idx)
    }

    fun copy(): Frame = take(IntArray(n) { it })
}

/** Lap / race JSON metadata (everything except `samples`). */
typealias Meta = Map<String, Any?>

fun Meta.num(key: String, default: Double = 0.0): Double = numOf(this[key], default)
fun Meta.text(key: String): String? = (this[key] as? String)
fun Meta.truthy(key: String): Boolean = when (val v = this[key]) { null -> false; is Boolean -> v; is Number -> v.toDouble() != 0.0; is String -> v.isNotEmpty(); else -> true }

/** analysts' _num(): finite number or default. */
fun numOf(v: Any?, default: Double = 0.0): Double {
    val f = when (v) { is Number -> v.toDouble(); is Boolean -> if (v) 1.0 else 0.0; is String -> v.trim().toDoubleOrNull() ?: return default; else -> return default }
    return if (f.isFinite()) f else default
}
