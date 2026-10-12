package app.trace.telemetry

import kotlin.math.floor
import kotlin.math.max
import kotlin.math.min

/** Port of udp.StintTracker -- rolling fuel-burn and tyre-stint estimates. */
class StintTracker {
    data class Heat(val frontAvg: Double, val rearAvg: Double, val pctHot: Double, val pctCold: Double)
    data class Snapshot(
        val fuelPerLap: Double?, val fuelBasisLaps: Int, val lapsOfFuel: Double?, val spareLaps: Double?,
        val stopNeeded: Boolean?, val pitByLap: Int?, val stintAge: Int, val stintCleanLaps: Int,
        val paceLossS: Double?, val trendSPerLap: Double?, val tyreState: String?, val heat: Heat?,
    )

    private class HeatAcc { var n = 0; var hot = 0; var cold = 0; var front = 0.0; var rear = 0.0 }

    private val fuelLaps = ArrayDeque<Double>()
    private var prevLap = 0
    private var prevFuel: Double? = null
    private var lapStartFuel: Double? = null
    private var lapDirty = false
    private var totalLaps = 0
    private var lastFuel: Double? = null
    private var heat = HeatAcc()
    private var lastHeat: Heat? = null
    private val stintLaps = ArrayList<Double>()
    private var stintAge = 0

    init { resetAll() }

    fun resetAll() {
        fuelLaps.clear(); prevLap = 0; prevFuel = null; lapStartFuel = null; lapDirty = false
        totalLaps = 0; lastFuel = null; heat = HeatAcc(); lastHeat = null; resetStint()
    }

    fun resetStint() { stintLaps.clear(); stintAge = 0 }

    private fun begin(lap: Int, fuel: Double) { prevLap = lap; lapStartFuel = fuel; lapDirty = false; heat = HeatAcc() }

    fun update(lapNumber: Int, totalLaps: Int, fuel: Double?, lastLapMs: Int, temps: List<Double>? = null, speedKmh: Double = 0.0) {
        val lap = lapNumber
        if (fuel == null || lap <= 0) return
        this.totalLaps = totalLaps
        lastFuel = fuel

        if (prevLap == 0 || lapStartFuel == null) { begin(lap, fuel); prevFuel = fuel; return }
        if (lap < prevLap || lap > prevLap + 1) {
            fuelLaps.clear(); resetStint(); begin(lap, fuel); prevFuel = fuel; return
        }
        val pf = prevFuel
        if (pf != null && fuel - pf > REFUEL_JUMP_PCT) { lapDirty = true; resetStint() }

        if (temps != null && speedKmh > 20) {
            if (temps.size == 4 && temps.all { it > 0 }) {
                heat.n += 1
                heat.hot += if (temps.max() > TYRE_HOT_C) 1 else 0
                heat.cold += if (temps.min() < TYRE_COLD_C) 1 else 0
                heat.front += (temps[0] + temps[1]) / 2.0
                heat.rear += (temps[2] + temps[3]) / 2.0
            }
        }
        if (lap == prevLap + 1) { finishLap(fuel, lastLapMs); begin(lap, fuel) }
        prevFuel = fuel
    }

    private fun finishLap(fuel: Double, lastLapMs: Int) {
        val used = lapStartFuel!! - fuel
        val lapS = if (lastLapMs > 0) lastLapMs / 1000.0 else null
        if (!lapDirty && used >= FUEL_MIN_LAP_USE) {
            fuelLaps.addLast(used)
            while (fuelLaps.size > FUEL_WINDOW_LAPS) fuelLaps.removeFirst()
            if (lapS != null && stintAge > 0) stintLaps.add(lapS)
        }
        stintAge += 1
        val h = heat
        if (h.n >= 5) lastHeat = Heat(h.front / h.n, h.rear / h.n, 100.0 * h.hot / h.n, 100.0 * h.cold / h.n)
    }

    fun snapshot(): Snapshot {
        val fpl = median(fuelLaps)
        var lapsOfFuel: Double? = null; var spare: Double? = null; var stop: Boolean? = null; var pitBy: Int? = null
        if (fpl != null && fpl > 0 && lastFuel != null) {
            lapsOfFuel = lastFuel!! / fpl
            val lap = prevLap
            val isRace = totalLaps > 0 && lap in 1..totalLaps
            if (isRace && lapStartFuel != null) {
                val lapsToRun = totalLaps - lap + 1
                spare = lapStartFuel!! / fpl - lapsToRun
                stop = spare < FUEL_SAFETY_LAPS
            } else stop = true
            if (stop == true) {
                val canFinish = floor(lapsOfFuel - FUEL_SAFETY_LAPS).toInt()
                pitBy = lap + max(0, canFinish - 1)
            }
        }
        var clean = 0; var loss: Double? = null; var trend: Double? = null; var state: String? = null
        if (stintLaps.isNotEmpty()) {
            val valid = validPaceLaps(stintLaps)
            clean = valid.size
            if (valid.size >= PACE_MIN_LAPS) {
                val recent = valid.takeLast(2).min()
                loss = max(0.0, recent - valid.min())
                state = PACE_BANDS.first { loss!! < it.first }.second
            }
            if (valid.size >= TREND_MIN_LAPS) trend = theilSen(valid)
        }
        return Snapshot(fpl, fuelLaps.size, lapsOfFuel, spare, stop, pitBy, stintAge, clean, loss, trend, state, lastHeat)
    }

    companion object {
        const val FUEL_WINDOW_LAPS = 5
        const val FUEL_SAFETY_LAPS = 0.5
        const val FUEL_MIN_LAP_USE = 0.05
        const val REFUEL_JUMP_PCT = 0.5
        const val TYRE_HOT_C = 100.0
        const val TYRE_COLD_C = 60.0
        const val PACE_MIN_LAPS = 3
        const val TREND_MIN_LAPS = 4
        const val PACE_SPIKE = 1.03
        val PACE_BANDS = listOf(0.3 to "Fresh", 0.8 to "Good", 1.5 to "Fading", Double.POSITIVE_INFINITY to "Worn")

        fun median(values: Collection<Double>): Double? {
            val v = values.sorted(); val n = v.size
            if (n == 0) return null
            val mid = n / 2
            return if (n % 2 == 1) v[mid] else (v[mid - 1] + v[mid]) / 2.0
        }

        fun validPaceLaps(laps: List<Double>): List<Double> {
            if (laps.isEmpty()) return emptyList()
            val med = median(laps)!!
            val l = laps.filter { it <= med * 1.25 }
            return l.filterIndexed { i, t -> !(i > 0 && i < l.size - 1 && t > PACE_SPIKE * max(l[i - 1], l[i + 1])) }
        }

        fun theilSen(ys: List<Double>): Double? {
            val slopes = ArrayList<Double>()
            for (i in ys.indices) for (j in i + 1 until ys.size) slopes.add((ys[j] - ys[i]) / (j - i))
            return median(slopes)
        }
    }
}
