package app.trace.telemetry

import kotlin.math.*

/** Numbers behind the Ratings / Style / Sectors / Braking charts (computed inline in the PC draw_* functions). */
object Scores {
    val LABELS = listOf("Steering\nSmooth", "Braking\nEffic.", "Throttle\nApply", "Corner\nSpeed", "Gear\nEffic.", "Lap\nConsist.", "Traction\nCtrl")

    private fun rows(d: Frame, pred: (Int) -> Boolean) = (0 until d.n).filter(pred)

    private fun meanOf(a: DoubleArray, idx: List<Int>) = if (idx.isEmpty()) Double.NaN else idx.sumOf { a[it] } / idx.size

    /** Shared by the lap and race ratings; the lap-consistency term differs. */
    private fun scores(d: Frame, consist: Double, steerNum: Boolean): DoubleArray {
        val steer = d["steering"]; val spd = d["speed_kmh"]; val brk = d["brake"]; val thr = d["throttle"]; val lat = d["lat_g"]
        val dsteer = Np.fillNaN(Np.diff(steer), 0.0)
        var sd = Np.std(dsteer, 1)
        if (steerNum && sd.isNaN()) sd = 0.0
        val steerSmooth = max(0.0, 1.0 - min(1.0, sd * 5)) * 100
        val braking = rows(d) { brk[it] > 0.1 }
        val brkEff = if (braking.size > 10) min(100.0, meanOf(spd, braking) / max(1.0, Np.mean(spd)) * 100) else 50.0
        val notBrk = rows(d) { brk[it] < 0.05 }
        val thrApp = if (notBrk.isNotEmpty()) notBrk.count { thr[it] > 0.85 }.toDouble() / notBrk.size * 100 else 0.0
        val corner = rows(d) { abs(lat[it]) > 0.5 }
        val crnSpd = if (corner.size > 10) min(100.0, meanOf(spd, corner) / max(1.0, Np.max(spd)) * 120) else 50.0
        val sg = d["suggested_gear"]
        val hasSg = sg.maxOf { abs(it) } > 0
        val g = d["gear"]
        val gearM = if (hasSg) (0 until d.n).count { g[it] == sg[it] }.toDouble() / d.n * 100 else 60.0
        val slipVar = (Np.std(d["tyre_slip_rl"], 1) + Np.std(d["tyre_slip_rr"], 1)) / 2
        val traction = max(0.0, 100.0 - slipVar * 20)
        return doubleArrayOf(steerSmooth, brkEff, thrApp, crnSpd, gearM, consist, traction)
    }

    fun lapScores(d: Frame): DoubleArray {
        val s = LapMath.lapSectors(d, 10)
        val avgs = s.segs.filter { it.isNotEmpty() }.map { seg -> seg.sumOf { d["speed_kmh"][it] } / seg.size }
        val consist = if (avgs.size >= 3) max(0.0, min(100.0, 100.0 - Np.std(avgs.toDoubleArray(), 0) / max(1.0, avgs.average()) * 100)) else 50.0
        return scores(d, consist, true)
    }

    fun raceScores(d: Frame): DoubleArray {
        val lapTimes = RaceLoader.lapSplitStats(d).filter { it.complete && it.timeS > 1 }.map { it.timeS }
        val consist = if (lapTimes.size > 1) max(0.0, min(100.0, 100.0 - (Np.std(lapTimes.toDoubleArray(), 0) / max(1.0, lapTimes.average())) * 100)) else 60.0
        return scores(d, consist, false)
    }

    fun grade(overall: Double) = when {
        overall >= 90 -> "S"; overall >= 80 -> "A"; overall >= 75 -> "B+"; overall >= 70 -> "B"; overall >= 60 -> "C"; else -> "D"
    }

    class Style(val aggression: Double, val balance: Double, val cornerRows: IntArray, val rearMinusFront: DoubleArray)

    /** Per-sample changes scaled to a 10 Hz equivalent so the same lap scores the same at 10 or 60 Hz. */
    fun styleMetrics(d: Frame): Style {
        val step = Np.diff(d["t"]).filter { !it.isNaN() && it > 1e-4 }
        val k = if (step.isNotEmpty()) 0.1 / Np.median(step.toDoubleArray()) else 1.0
        val aggression = min(100.0, Corners.meanAbsDiff(d["steering"], k) * 220 + Corners.meanAbsDiff(d["throttle"], k) * 140 + Corners.meanAbsDiff(d["brake"], k) * 140)
        val lat = d["lat_g"]
        val corner = rows(d) { abs(lat[it]) > 0.5 }
        val hasSlip = listOf("tyre_slip_fl", "tyre_slip_fr", "tyre_slip_rl", "tyre_slip_rr").all { d.has(it) }
        if (corner.size > 10 && hasSlip) {
            val diff = DoubleArray(corner.size) { i -> val r = corner[i]
                (d["tyre_slip_rl"][r] + d["tyre_slip_rr"][r]) / 2 - (d["tyre_slip_fl"][r] + d["tyre_slip_fr"][r]) / 2 }
            return Style(aggression, Np.mean(diff), corner.toIntArray(), diff)
        }
        return Style(aggression, 0.0, corner.toIntArray(), DoubleArray(0))
    }

    fun styleLabel(agg: Double) = if (agg < 33) "Smooth" else if (agg < 66) "Balanced" else "Aggressive"
    fun balanceLabel(bal: Double) = if (bal > 0.015) "Oversteer-leaning" else if (bal < -0.015) "Understeer-leaning" else "Neutral"

    /** Duration (s) of each braking zone (brake > 0.1); a zone open at the lap's end closes on the last sample. */
    fun brakeZoneDurations(df: Frame): List<Double> {
        val b = df["brake"]; val t = df["t"]; val n = b.size
        if (n == 0) return emptyList()
        val on = IntArray(n) { if (b[it] > 0.1) 1 else 0 }
        val starts = ArrayList<Int>(); val ends = ArrayList<Int>()
        for (i in 0..n) {
            val cur = if (i < n) on[i] else 0
            val prev = if (i > 0) on[i - 1] else 0
            if (cur - prev == 1) starts.add(i)
            if (cur - prev == -1) ends.add(min(i, n - 1))
        }
        return starts.indices.map { t[ends[it]] - t[starts[it]] }
    }

    class SectorStats(val segs: List<IntArray>, val times: DoubleArray, val avg: DoubleArray, val top: DoubleArray,
                      val brk: DoubleArray, val thr: DoubleArray, val latg: DoubleArray)

    fun sectorStats(d: Frame, n: Int = 10): SectorStats {
        val s = LapMath.lapSectors(d, n)
        fun agg(col: String, scale: Double = 1.0, fn: (DoubleArray) -> Double) =
            DoubleArray(s.segs.size) { i -> val seg = s.segs[i]; if (seg.isEmpty()) Double.NaN else fn(DoubleArray(seg.size) { d[col][seg[it]] }) * scale }
        return SectorStats(s.segs, s.times, agg("speed_kmh") { Np.mean(it) }, agg("speed_kmh") { Np.max(it) },
            agg("brake", 100.0) { Np.mean(it) }, agg("throttle", 100.0) { Np.mean(it) },
            agg("lat_g") { a -> a.maxOf { abs(it) } })
    }
}

/** Telemetry-diff series (draw_telediff): B resampled onto A's normalised axis. */
object TeleDiff {
    class Axis(val x: DoubleArray, val a: (String) -> DoubleArray, val b: (String) -> DoubleArray)

    /** Lap Analyst: both laps normalised by track position. */
    fun byPosition(dfa: Frame, dfb: Frame): Axis {
        val pa = dfa["track_position"]; val pb = dfb["track_position"]
        val ma = max(Np.max(pa), 1.0); val mb = max(Np.max(pb), 1.0)
        val taS = DoubleArray(pa.size) { pa[it] / ma }.also { it.sort() }
        val tbS = DoubleArray(pb.size) { pb[it] / mb }.also { it.sort() }
        val sa = dfa.sortedBy("track_position"); val sb = dfb.sortedBy("track_position")
        return Axis(taS, { c -> Np.interp(taS, taS, sa[c]) }, { c -> Np.interp(taS, tbS, sb[c]) })
    }

    /** Race Analyst: both races normalised by duration; A is already on its own timebase. */
    fun byTime(dfa: Frame, dfb: Frame): Axis {
        val durA = max(Np.max(dfa["t"]), 1.0); val durB = max(Np.max(dfb["t"]), 1.0)
        val paS = DoubleArray(dfa.n) { dfa["t"][it] / durA }.also { it.sort() }
        val pbS = DoubleArray(dfb.n) { dfb["t"][it] / durB }.also { it.sort() }
        val sa = dfa.sortedBy("t"); val sb = dfb.sortedBy("t")
        return Axis(paS, { c -> sa[c] }, { c -> Np.interp(paS, pbS, sb[c]) })
    }

    fun diff(ax: Axis, col: String): DoubleArray { val a = ax.a(col); val b = ax.b(col); return DoubleArray(a.size) { b[it] - a[it] } }
}
