package app.trace.telemetry

import java.io.File
import kotlin.math.*

class RaceFile(val meta: Meta, val frame: Frame)

/** race_analyst.py data side. */
object RaceLoader {
    val NUM_COLS = listOf(
        "world_x", "world_y", "world_z", "speed_kmh", "throttle", "brake", "steering",
        "clutch", "clutch_engaged", "gear", "suggested_gear", "rpm", "max_rpm",
        "rpm_warning", "rpm_limiter", "rpm_after_clutch", "boost",
        "tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr",
        "tyre_slip_fl", "tyre_slip_fr", "tyre_slip_rl", "tyre_slip_rr",
        "susp_fl", "susp_fr", "susp_rl", "susp_rr", "ride_height_mm",
        "fuel_remaining", "fuel_capacity", "oil_temp", "water_temp", "oil_pressure",
        "ang_x", "ang_y", "ang_z", "vel_x", "vel_y", "vel_z",
        "heading", "track_position", "lap_number", "current_position",
        "total_positions", "in_pit", "t",
    )
    const val PIT_LANE_MAX_KPH = 100.0
    const val REFUEL_MIN_JUMP = 0.5

    fun load(file: File): RaceFile = load(JsonColumns.read(file))

    fun load(raw: JsonColumns.Raw): RaceFile {
        if (!raw.hadSamples && raw.meta.isEmpty()) throw IllegalArgumentException("Not a TRACE race file")
        if (raw.n == 0) throw IllegalArgumentException("No samples in file")
        var df = LapLoader.rawFrame(raw, NUM_COLS, emptyList())
        val n = df.n
        val t0 = df["t"]
        if (n > 1 && t0.toSet().size <= 1) df["t"] = DoubleArray(n) { it * 0.1 }
        for (c in listOf("susp_fl", "susp_fr", "susp_rl", "susp_rr")) { val a = df[c]; df[c] = DoubleArray(n) { a[it] * 1000.0 } }
        df = df.sortedBy("t")
        val dt = dtSeries(df)
        LapLoader.addDerived(df, fuelClipHi = 10.0, dt = dt)
        val inPit = df["in_pit"]; val inferred = inferPitFlag(df)
        df["pit_flag"] = DoubleArray(n) { max(inPit[it], inferred[it]) }
        return RaceFile(raw.meta, df)
    }

    /** Per-sample dt; zero / negative / missing gaps get the median dt. */
    fun dtSeries(df: Frame): DoubleArray = LapLoader.stepDt(df["t"], strict0 = true)

    /** (start, end) sample spans where fuel was added. */
    fun refuelSpans(fuel: DoubleArray, maxGap: Int = 6): List<Pair<Int, Int>> {
        if (fuel.size < 2) return emptyList()
        val up = ArrayList<Int>()
        for (i in 0 until fuel.size - 1) if (fuel[i + 1] - fuel[i] > 0.02) up.add(i + 1)
        val spans = ArrayList<Pair<Int, Int>>()
        var i = 0
        while (i < up.size) {
            var j = i
            while (j + 1 < up.size && up[j + 1] - up[j] <= maxGap) j++
            val start = up[i] - 1; val end = up[j]
            if (fuel[end] - fuel[start] >= REFUEL_MIN_JUMP) spans.add(start to end)
            i = j + 1
        }
        return spans
    }

    fun inferPitFlag(df: Frame): DoubleArray {
        val speed = df["speed_kmh"]; val flag = DoubleArray(df.n)
        for ((s, e) in refuelSpans(df["fuel_remaining"])) {
            var lo = s
            while (lo > 0 && speed[lo - 1] <= PIT_LANE_MAX_KPH) lo--
            var hi = e
            while (hi < df.n - 1 && speed[hi + 1] <= PIT_LANE_MAX_KPH) hi++
            for (q in lo..hi) flag[q] = 1.0
        }
        return flag
    }

    fun fmtDur(t: Any?): String {
        val f = when (t) { is Number -> t.toDouble(); is String -> t.trim().toDoubleOrNull() ?: return "--"; else -> return "--" }
        if (!f.isFinite() || f < 0) return "--"
        val ms = Math.rint(f * 1000).toLong()
        val m = ms / 60000; val rest = ms % 60000
        return "$m:" + fixed(rest / 1000.0, 3).padStart(6, '0')
    }

    fun raceLabel(meta: Meta, short: Boolean = false): String {
        var car = meta["car"]?.toString()?.takeIf { it.isNotEmpty() } ?: "?"
        val track = pyTitle((meta["track"]?.toString()?.takeIf { it.isNotEmpty() } ?: "?").replace("_", " "))
        val dur = meta["race_duration_s"]
        if (!LapMath.isCurrentEra(meta)) car = "$car [pre-${TraceUtil.PHYSICS_ERA}]"
        return if (short) "$car  ${fmtDur(dur ?: 0)}" else "$car @ $track  ${fmtDur(dur ?: 0)}"
    }

    /** Lap segments [start, end) split at lap_number changes; segments under 3 samples dropped. */
    fun lapSegments(df: Frame): List<IntRange> {
        val laps = df["lap_number"]
        if (laps.isEmpty() || laps.maxOf { abs(it) } == 0.0) return listOf(0 until df.n)
        val bounds = ArrayList<Int>(); bounds.add(0)
        for (i in 0 until laps.size - 1) if (laps[i + 1] - laps[i] != 0.0) bounds.add(i + 1)
        bounds.add(df.n)
        val out = ArrayList<IntRange>()
        for (k in 0 until bounds.size - 1) if (bounds[k + 1] - bounds[k] >= 3) out.add(bounds[k] until bounds[k + 1])
        return out
    }

    /** Segments as the lap table counts them (the grid roll is not a lap). */
    fun countedSegments(df: Frame): List<IntRange> {
        var segs = lapSegments(df)
        if (segs.size > 1) {
            val f = segs.filter { df["lap_number"][it.first].toInt() != 0 }
            if (f.isNotEmpty()) segs = f
        }
        return segs
    }

    class Split(val lap: Int, val timeS: Double, val complete: Boolean, val avgSpeed: Double, val topSpeed: Double,
                val brakePct: Double, val throttlePct: Double, val maxLatG: Double)

    private fun mean(a: DoubleArray, r: IntRange): Double { var s = 0.0; for (i in r) s += a[i]; return s / (r.last - r.first + 1) }

    fun lapSplitStats(df: Frame): List<Split> {
        val segs = countedSegments(df)
        val lastLapSeen = if (df.n > 0) df["lap_number"][df.n - 1].toInt() else 0
        val t = df["t"]; val ln = df["lap_number"]; val spd = df["speed_kmh"]; val brk = df["brake"]; val thr = df["throttle"]; val lat = df["lat_g"]
        val out = ArrayList<Split>()
        for ((k, seg) in segs.withIndex()) {
            val i = k + 1
            val endT = if (i < segs.size) t[segs[i].first] else t[seg.last]
            val lapTime = endT - t[seg.first]
            val lapNo = ln[seg.first].toInt()
            out.add(Split(if (lapNo != 0) lapNo else i, lapTime, i < segs.size || lastLapSeen > lapNo,
                mean(spd, seg), seg.maxOf { spd[it] }, mean(brk, seg) * 100, mean(thr, seg) * 100, seg.maxOf { abs(lat[it]) }))
        }
        return out
    }

    fun countPitStops(df: Frame): Int { val p = df["pit_flag"]; var c = 0; for (i in 1 until p.size) if (p[i] - p[i - 1] > 0) c++; return c }

    class FuelLap(val perLap: Double?, val lapsPerTank: Double?, val lapsUsed: Int)

    fun fuelPerLapStats(df: Frame): FuelLap {
        val segs = lapSegments(df).filter { df["lap_number"][it.first].toInt() != 0 }
        val used = ArrayList<Double>()
        val fuelAll = df["fuel_remaining"]; val pit = df["pit_flag"]
        for (k in 0 until segs.size - 1) {
            val seg = segs[k]; val nxt = segs[k + 1]
            if (seg.last - seg.first + 1 < 2) continue
            val fuel = DoubleArray(seg.last - seg.first + 1) { fuelAll[seg.first + it] }
            val pitMax = seg.maxOf { pit[it] }
            if (refuelSpans(fuel).isNotEmpty() || pitMax > 0) continue
            val burn = fuel[0] - fuelAll[nxt.first]
            if (burn >= 0.05) used.add(burn)
        }
        if (used.isEmpty()) return FuelLap(null, null, 0)
        val fpl = Np.median(used.toDoubleArray())
        var cap = if (df.has("fuel_capacity")) Np.max(df["fuel_capacity"]) else 100.0
        if (cap <= 0) cap = 100.0
        return FuelLap(fpl, cap / fpl, used.size)
    }

    fun buildStats(meta: Meta, df: Frame): List<Pair<String, String>> {
        val splits = lapSplitStats(df)
        val lapTimes = splits.filter { it.complete && it.timeS > 1 }.map { it.timeS }
        val best = lapTimes.minOrNull() ?: 0.0
        val avg = if (lapTimes.isNotEmpty()) lapTimes.average() else 0.0
        val fuel = df["fuel_remaining"]
        var fuelUsed = 0.0
        for (i in 0 until fuel.size - 1) { val d = fuel[i + 1] - fuel[i]; if (d < 0) fuelUsed += -d }
        val fl = fuelPerLapStats(df)
        val dur: Any? = if (meta.containsKey("race_duration_s")) meta["race_duration_s"] else df["t"][df.n - 1] - df["t"][0]
        val thr = df["throttle"]
        val pos = df["current_position"]
        val gridIdx = pos.indexOfFirst { it > 0 }
        return listOf(
            "Duration" to fmtDur(dur),
            "Laps" to "${lapTimes.size}" + (if (splits.size > lapTimes.size) " (+${splits.size - lapTimes.size} partial)" else ""),
            "Best Lap" to (if (best != 0.0) fmtDur(best) else "--"),
            "Avg Lap" to (if (avg != 0.0) fmtDur(avg) else "--"),
            "Samples" to "${df.n}",
            "Top Speed" to fixed(Np.max(df["speed_kmh"]), 1) + " km/h",
            "Avg Speed" to fixed(Np.mean(df["speed_kmh"]), 1) + " km/h",
            "Full Thr%" to fixed(thr.count { it > 0.95 }.toDouble() / df.n * 100, 1) + "%",
            "Coasting%" to fixed(Np.mean(df["coasting"]) * 100, 1) + "%",
            "Max Lat G" to fixed(df["lat_g"].maxOf { abs(it) }, 2) + "g",
            "Max Long G" to fixed(df["long_g"].maxOf { abs(it) }, 2) + "g",
            "Fuel Used" to fixed(fuelUsed, 2),
            "Fuel / Lap" to (fl.perLap?.let { if (it != 0.0) fixed(it, 2) else "--" } ?: "--"),
            "Laps / Tank" to (fl.lapsPerTank?.let { if (it != 0.0) fixed(it, 1) else "--" } ?: "--"),
            "Pit Stops" to "${countPitStops(df)}",
            "Grid Pos" to (if (gridIdx >= 0) "P${pos[gridIdx].toInt()}" else "--"),
        )
    }

    class LapFrame(val lap: Int, val timeS: Double, val frame: Frame)

    /** Each complete lap long enough to analyse corner by corner; track_position restarts at the line. */
    fun raceLapFrames(df: Frame): List<LapFrame> {
        val segs = countedSegments(df); val splits = lapSplitStats(df)
        val out = ArrayList<LapFrame>()
        for ((seg, sp) in segs.zip(splits)) {
            if (sp.complete && sp.timeS > 1 && seg.last - seg.first + 1 >= 20) out.add(LapFrame(sp.lap, sp.timeS, df.slice(seg.first, seg.last + 1)))
        }
        return out
    }

    class Baseline(val label: String, val frame: Frame?, val note: String)

    fun bestLapBaseline(df: Frame): Baseline {
        val laps = raceLapFrames(df)
        if (laps.isEmpty()) return Baseline("best lap in this race", null, "No complete lap long enough to analyse in this race.")
        val b = laps.minByOrNull { it.timeS }!!
        return Baseline("best lap in this race (lap ${b.lap}, ${fmtDur(b.timeS)})", b.frame, "")
    }

    /** Where the Dashboard keeps the saved PB lap: <laps>/<track>/reference_<car>.json */
    fun pbReferencePath(meta: Meta, lapsFolder: File): File {
        val track = meta["track"]?.toString() ?: ""
        val car = meta["car"]?.toString()?.takeIf { it.isNotEmpty() } ?: "unknown"
        return File(File(lapsFolder, track), "reference_${TraceUtil.sanitize(car)}.json")
    }

    fun pbFileBaseline(meta: Meta, lapsFolder: File): Baseline {
        val label = "your saved PB lap"
        val path = pbReferencePath(meta, lapsFolder)
        if (!path.exists()) return Baseline(label, null, "No saved PB lap for this car at this track yet (${path.name} not found).")
        val lap = try { LapLoader.load(path) } catch (e: Exception) { return Baseline(label, null, "Couldn't read the saved PB lap: ${e.message}") }
        if (!LapMath.isCurrentEra(lap.meta)) return Baseline(label, null, "Your saved PB lap was driven before GT7 update 1.71 and isn't comparable.")
        return Baseline("your saved PB lap (${fmtDur(numOf(lap.meta["lap_time_s"]))})", lap.frame, "")
    }

    fun exportCsv(df: Frame): String {
        val dt = dtSeries(df)
        val n = df.n
        val dist = Np.cumsum(DoubleArray(n) { df["speed_kmh"][it] / 3.6 * dt[it] })
        val sb = StringBuilder("distance_m,speed_kmh,throttle,brake,rpm,gear,steering\n")
        for (i in 0 until n) {
            sb.append(LapMath.pyRound(dist[i], 2)).append(',').append(LapMath.pyRound(df["speed_kmh"][i], 2)).append(',')
                .append(LapMath.pyRound(df["throttle"][i], 4)).append(',').append(LapMath.pyRound(df["brake"][i], 4)).append(',')
                .append(LapMath.pyRound(df["rpm"][i], 1)).append(',').append(df["gear"][i].toInt()).append(',')
                .append(LapMath.pyRound(df["steering"][i], 4)).append('\n')
        }
        return sb.toString()
    }
}
