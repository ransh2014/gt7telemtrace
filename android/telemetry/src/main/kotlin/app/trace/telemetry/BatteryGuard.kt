package app.trace.telemetry

/**
 * dashboard.py's low-battery warning: while a lap or race is being recorded on battery power and the
 * charge is low, show a bar, then sound a siren if it goes unacknowledged. Pure -- feed it the reading
 * and the time, so it is unit-tested without a phone.
 */
class BatteryGuard(private val warnPct: Int = WARN_PCT) {
    enum class Event { WARN, CRITICAL, ALARM }
    class Step(val show: Boolean, val events: List<Event>)

    private var warnedAt: Double? = null
    private var acked = false
    private var crit = false

    fun ack() { acked = true }

    /** One check (call about every [CHECK_S] seconds). [pct] is null when there is no battery reading. */
    fun step(pct: Int?, plugged: Boolean, active: Boolean, now: Double): Step {
        val p = pct
        if (p == null || !needsWarning(p, plugged, active, warnPct)) {
            warnedAt = null; acked = false; crit = false
            return Step(false, emptyList())
        }
        val events = ArrayList<Event>()
        if (warnedAt == null) {
            warnedAt = now; acked = false
            events.add(Event.WARN)
            return Step(true, events)
        }
        if (p < CRIT_PCT && !crit) {   // re-alarm once at the critical level
            crit = true; acked = false; warnedAt = now - ALARM_AFTER_S
            events.add(Event.CRITICAL)
        }
        if (alarmDue(warnedAt, now, acked)) events.add(Event.ALARM)
        return Step(!acked, events)
    }

    companion object {
        const val WARN_PCT = 20
        const val CRIT_PCT = 10
        const val ALARM_AFTER_S = 30.0
        const val CHECK_S = 2.0

        /** True when a lap/race is in progress, the device is on battery and the charge is low. */
        fun needsWarning(pct: Int?, plugged: Boolean, active: Boolean, warnPct: Int = WARN_PCT): Boolean =
            active && pct != null && !plugged && pct < warnPct

        /** Loud alarm once the warning has gone unacknowledged for a while. */
        fun alarmDue(warnedAt: Double?, now: Double, acked: Boolean): Boolean =
            !acked && warnedAt != null && now - warnedAt >= ALARM_AFTER_S
    }
}
