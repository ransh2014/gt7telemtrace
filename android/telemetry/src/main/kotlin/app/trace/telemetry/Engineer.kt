package app.trace.telemetry

import kotlin.math.max

/**
 * engineer.py: pit window, undercut / overcut maths, live "pit now" calls and a simulated finishing position.
 * Estimates from the player's own laps; the text says so. Reads the numbers StintTracker.snapshot() produces.
 */
object Engineer {
    const val FUEL_RESERVE_LAPS = 0.5
    const val PIT_LOSS_DEFAULT_S = 24.0
    const val OUT_LAP_PENALTY_S = 1.5

    /** The three StintTracker numbers the engineer reads (so tests need not build a whole Snapshot). */
    class Snap(val lapsOfFuel: Double?, val paceLossS: Double?, val stopNeeded: Boolean?)

    fun snapOf(s: StintTracker.Snapshot) = Snap(s.lapsOfFuel, s.paceLossS, s.stopNeeded)

    class PitWindow(val earliest: Int?, val latest: Int?, val reason: String)
    class Verdict(val works: Boolean?, val marginS: Double?, val text: String)
    class Finish(val costS: Double, val finish: Int?, val text: String)

    fun fuelLatestLap(lap: Int, lapsOfFuel: Double?): Int? {
        if (lapsOfFuel == null) return null
        return lap + max(0, (lapsOfFuel - FUEL_RESERVE_LAPS).toInt() - 1)   // int() truncates toward zero, like Kotlin's toInt()
    }

    fun pitWindow(lap: Int, totalLaps: Int, snap: Snap, pitLossS: Double = PIT_LOSS_DEFAULT_S, freshGainS: Double? = null): PitWindow {
        val latest = fuelLatestLap(lap, snap.lapsOfFuel)
        val gain = freshGainS ?: snap.paceLossS
        val remaining = if (totalLaps != 0) max(0, totalLaps - lap + 1) else 0
        var earliest: Int? = null
        var reason = ""
        if (snap.stopNeeded == false) return PitWindow(null, null, "You can reach the flag on the fuel you have; no stop needed for fuel.")
        if (gain != null && gain != 0.0 && gain > 0 && remaining != 0) {
            val payLaps = (pitLossS + OUT_LAP_PENALTY_S) / gain
            earliest = if (remaining > payLaps) lap else null
            reason = if (earliest == null) "A stop costs about ${fixed(pitLossS, 0)}s and fresh tyres only repay it over ${fixed(payLaps, 0)} laps."
            else "Tyres are about ${fixed(gain, 2)}s/lap off the pace; a stop repays itself in ${fixed(payLaps, 0)} laps."
        } else if (latest != null) reason = "Pit by the fuel limit."
        return PitWindow(earliest, latest, reason)
    }

    fun undercut(gapAheadS: Double?, tyreGainS: Double, lapsToHold: Int = 2): Verdict {
        if (gapAheadS == null) return Verdict(null, null, "No car ahead to undercut.")
        val swing = tyreGainS * lapsToHold - OUT_LAP_PENALTY_S
        val margin = swing - gapAheadS
        val works = margin > 0
        return Verdict(works, margin, "Undercut ${if (works) "works" else "does not work"}: you'd gain about ${fixed(swing, 1)}s over $lapsToHold laps " +
            "against a ${fixed(gapAheadS, 1)}s gap (${signed(margin, 1)}s).")
    }

    fun overcut(gapBehindS: Double?, tyreLossPerLapS: Double, lapsToHold: Int = 2): Verdict {
        if (gapBehindS == null) return Verdict(null, null, "No car behind to cover.")
        val margin = gapBehindS - (tyreLossPerLapS * lapsToHold) + OUT_LAP_PENALTY_S
        val works = margin > 0
        return Verdict(works, margin, "Overcut ${if (works) "holds" else "fails"}: ${signed(margin, 1)}s margin after $lapsToHold laps.")
    }

    fun simulateFinish(position: Int, totalPositions: Int, lap: Int, totalLaps: Int, lapS: Double, pitsNeeded: Int,
                       pitLossS: Double = PIT_LOSS_DEFAULT_S, rivalGapsS: List<Double>? = null): Finish {
        val left = max(0, totalLaps - lap + 1)
        val cost = pitsNeeded * pitLossS
        if (!rivalGapsS.isNullOrEmpty()) {
            val ahead = rivalGapsS.count { it < 0 }
            val passedBy = rivalGapsS.count { it > 0 && it < cost }
            val fin = ahead + passedBy + 1
            return Finish(cost, fin, "Pace ${fixed(lapS, 1)}s/lap over $left laps and $pitsNeeded stop(s) (~${fixed(cost, 0)}s): P$fin projected.")
        }
        return Finish(cost, null, "$pitsNeeded stop(s) will cost about ${fixed(cost, 0)}s over the remaining $left laps.")
    }

    /** The live radio call for this lap, or null. Once per lap (null if lastCalled == lap). Returns (message, kind) with kind "box" or "warn". */
    fun pitCall(lap: Int, totalLaps: Int, snap: Snap, window: PitWindow? = null, lastCalled: Int? = null): Pair<String, String>? {
        if (lastCalled == lap) return null
        val w = window ?: pitWindow(lap, totalLaps, snap)
        val latest = w.latest; val earliest = w.earliest
        if (latest != null && lap >= latest) return "Box this lap. Box, box." to "box"
        if (latest != null && lap == latest - 1) return "Pit next lap." to "warn"
        if (earliest != null && latest != null && lap == earliest && earliest < latest) return "Pit window is open." to "warn"
        return null
    }
}
