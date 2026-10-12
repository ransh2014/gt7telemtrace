package app.trace.charts

import kotlin.math.floor

/** matplotlib colour maps (256-step lookup tables exported from matplotlib itself). */
object Cmaps {
    private val cache = HashMap<String, IntArray>()

    fun lut(name: String): IntArray = cache.getOrPut(name) {
        val hex = CmapData.LUT[name] ?: CmapData.LUT.getValue("turbo")
        IntArray(256) { i -> 0xFF000000.toInt() or hex.substring(i * 6, i * 6 + 6).toInt(16) }
    }

    /** matplotlib maps x in [0,1) to floor(x * 256); 1.0 lands on the last entry. */
    fun idx(t: Double): Int {
        if (t.isNaN()) return 0
        if (t <= 0.0) return 0
        if (t >= 1.0) return 255
        return floor(t * 256).toInt().coerceIn(0, 255)
    }

    fun at(name: String, t: Double): Int = lut(name)[idx(t)]

    /** plt.cm.tab10(x): ListedColormap of 10 colours indexed by floor(x * 10). */
    fun tab10(t: Double): Int {
        val k = if (t.isNaN() || t <= 0.0) 0 else if (t >= 1.0) 9 else floor(t * 10).toInt().coerceIn(0, 9)
        return 0xFF000000.toInt() or CmapData.TAB10.substring(k * 6, k * 6 + 6).toInt(16)
    }
}
