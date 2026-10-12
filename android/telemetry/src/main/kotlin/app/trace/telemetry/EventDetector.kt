package app.trace.telemetry

/** Port of udp._check_events: race start/end and pause/resume detection. Clock is injectable for tests. */
class EventDetector(private val clock: () -> Double = { System.currentTimeMillis() / 1000.0 }) {
    private val callbacks = HashMap<String, MutableList<(Packet) -> Unit>>()
    private var prevPaused = false
    var raceActive = false; private set
    private var speedHoldStart: Double? = null
    private var prevLoading = false
    private var loadingSince: Double? = null
    private var gridArmedUntil = 0.0

    fun register(name: String, fn: (Packet) -> Unit) { val l = callbacks.getOrPut(name) { ArrayList() }; if (fn !in l) l.add(fn) }
    private fun fire(name: String, p: Packet) { callbacks[name]?.forEach { try { it(p) } catch (_: Exception) {} } }

    fun check(parsed: Packet) {
        val paused = parsed["paused"] as? Boolean ?: false
        val onTrack = parsed["car_on_track"] as? Boolean ?: false
        val loading = parsed["loading"] as? Boolean ?: false
        val speed = (parsed["speed_kmh"] as? Number)?.toDouble() ?: 0.0
        val lap = (parsed["lap_number"] as? Number)?.toInt() ?: 0
        val total = (parsed["total_laps"] as? Number)?.toInt() ?: 0
        val now = clock()

        if (paused && !prevPaused) fire("pause", parsed) else if (prevPaused && !paused) fire("resume", parsed)
        prevPaused = paused
        if (prevLoading && !loading) gridArmedUntil = now + GRID_START_ARM_WINDOW
        prevLoading = loading

        val raceOver = total > 0 && lap > total
        if (raceOver) speedHoldStart = null
        else if (!raceActive) {
            if (speed >= ROLLING_START_KPH && total > 0) {
                val s = speedHoldStart
                if (s == null) speedHoldStart = now
                else if (now - s >= ROLLING_START_HOLD) { raceActive = true; speedHoldStart = null; fire("race_start", parsed) }
            } else speedHoldStart = null
            if (!raceActive && onTrack && speed <= GRID_START_KPH_EPS && now <= gridArmedUntil && total > 0) {
                raceActive = true; speedHoldStart = null; fire("race_start", parsed)
            }
        }
        if (raceActive) {
            if (total > 0 && lap > total) { raceActive = false; loadingSince = null; fire("race_end", parsed) }
            else if (loading) {
                val ls = loadingSince
                if (ls == null) loadingSince = now
                else if (now - ls >= RACE_ABANDON_LOADING_S) {
                    raceActive = false; loadingSince = null
                    fire("race_end", LinkedHashMap(parsed).apply { put("race_abandoned", true) })
                }
            } else loadingSince = null
        }
    }

    companion object {
        const val ROLLING_START_KPH = 80.0
        const val ROLLING_START_HOLD = 2.0
        const val GRID_START_KPH_EPS = 0.5
        const val GRID_START_ARM_WINDOW = 8.0
        const val RACE_ABANDON_LOADING_S = 1.0
    }
}
