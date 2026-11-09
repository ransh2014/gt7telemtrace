package app.trace.telemetry

/** dashboard._auto_lap_step: arms Record Lap by itself while driving in a session without race laps. Pure state machine. */
class AutoLap {
    enum class Action { NONE, ARM, DISARM_OFF, DISARM_ENDED }

    var active = false; private set          // the current Record Lap arming was done automatically
    private var block = false                // user stopped an auto lap: stay off until the session is left
    private var since: Double? = null
    private var offSince: Double? = null

    /** The user pressed Stop on an auto-armed lap. */
    fun onUserStop() { if (active) { active = false; block = true } }
    fun cleared() { active = false; since = null; offSince = null }

    fun step(enabled: Boolean, inSession: Boolean, canArm: Boolean, busy: Boolean, now: Double): Action {
        if (!enabled) { val was = active; since = null; if (was) { cleared(); return Action.DISARM_OFF }; return Action.NONE }
        if (!inSession) block = false
        if (active) {
            if (inSession) offSince = null
            else if (offSince == null) offSince = now
            else if (now - offSince!! >= LEAVE_HOLD_S) { cleared(); return Action.DISARM_ENDED }
            return Action.NONE
        }
        if (busy || block || !canArm) { since = null; return Action.NONE }
        if (since == null) since = now
        else if (now - since!! >= ARM_HOLD_S) { since = null; offSince = null; active = true; return Action.ARM }
        return Action.NONE
    }

    companion object {
        const val MIN_KPH = 5.0
        const val ARM_HOLD_S = 1.5
        const val LEAVE_HOLD_S = 3.0
        fun inSession(onTrack: Boolean, loading: Boolean, totalLaps: Int) = !loading && onTrack && totalLaps == 0
        fun canArm(inSession: Boolean, paused: Boolean, speedKmh: Double) = inSession && !paused && speedKmh >= MIN_KPH
    }
}
