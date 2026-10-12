package app.trace.telemetry

import java.io.File
import kotlin.math.*

class LapFile(val meta: Meta, val frame: Frame)

/** lap_analyst.load_lap_data / load_lap. */
object LapLoader {
    val NUM_COLS = listOf(
        "world_x", "world_y", "world_z", "speed_kmh", "throttle", "brake", "steering",
        "clutch", "clutch_engaged", "gear", "suggested_gear", "rpm", "max_rpm",
        "rpm_warning", "rpm_limiter", "rpm_after_clutch", "boost",
        "tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr",
        "tyre_slip_fl", "tyre_slip_fr", "tyre_slip_rl", "tyre_slip_rr",
        "susp_fl", "susp_fr", "susp_rl", "susp_rr", "ride_height_mm",
        "fuel_remaining", "fuel_capacity", "oil_temp", "water_temp", "oil_pressure",
        "ang_x", "ang_y", "ang_z", "vel_x", "vel_y", "vel_z",
        "heading", "track_position", "t",
        "wheel_rotation", "steering_angular_velocity", "sway", "heave", "surge",
        "energy_recovery", "current_lap_ms",
        "wheel_steering_angle_l", "wheel_steering_angle_r", "wheel_base",
    )

    fun load(file: File): LapFile = load(JsonColumns.read(file))
    fun loadText(text: String): LapFile = load(JsonColumns.parse(text))

    /** Frame columns in pandas order: file order first, then the defaults that were missing. */
    fun rawFrame(raw: JsonColumns.Raw, numCols: List<String>, strCols: List<String>): Frame {
        val f = Frame(raw.n)
        for ((k, v) in raw.nums) {
            if (k in strCols) continue
            if (raw.strs.containsKey(k) && k !in numCols) {
                f.strs[k] = Array(raw.n) { raw.strs[k]!![it] ?: "0" }   // non-numeric text column
            } else f.cols[k] = DoubleArray(raw.n) { val x = v[it]; if (x.isNaN()) 0.0 else x }
        }
        for (k in numCols) if (!f.cols.containsKey(k)) f.cols[k] = DoubleArray(raw.n)
        for (k in strCols) {
            val v = raw.strs[k]
            f.strs[k] = if (raw.nums.containsKey(k)) {
                // present in the file: a missing / null cell became 0 in pandas
                val nv = raw.nums[k]!!
                Array(raw.n) { v?.get(it) ?: (if (nv[it].isNaN()) "0" else numText(nv[it])) }
            } else Array(raw.n) { "" }
        }
        return f
    }

    private fun numText(d: Double) = if (d == floor(d) && abs(d) < 1e15) d.toLong().toString() else d.toString()

    fun load(raw: JsonColumns.Raw): LapFile {
        if (!raw.hadSamples || raw.n == 0) throw IllegalArgumentException(if (raw.hadSamples || raw.meta.isNotEmpty()) "No samples in file" else "This file is not a TRACE lap")
        val rawCols = raw.nums.keys.toSet()
        val df = rawFrame(raw, NUM_COLS, listOf("surface_type", "car_category"))
        val n = df.n
        val lapS = numOf(raw.meta["lap_time_s"])
        val speedMs = DoubleArray(n) { df["speed_kmh"][it] / 3.6 }
        val compact = !("t" in rawCols || "rpm" in rawCols || "tyre_temp_fl" in rawCols)
        val hasT = "t" in rawCols && n > 1 && (Np.max(df["t"]) - Np.min(df["t"])) > 0
        val hasPos = "track_position" in rawCols && Np.max(df["track_position"]) > 0
        if (!hasT) {
            val tt: DoubleArray
            if (hasPos) {
                val dpos = Np.fillNaN(Np.diff(df["track_position"]), 0.0).let { a -> DoubleArray(n) { max(0.0, a[it]) } }
                tt = Np.cumsum(DoubleArray(n) { dpos[it] / max(1.0, speedMs[it]) })
            } else tt = DoubleArray(n) { it * 0.1 }
            if (lapS > 0 && tt[n - 1] > 0) { val k = lapS / tt[n - 1]; for (i in 0 until n) tt[i] *= k }
            df["t"] = tt; df.tDerived = true
        }
        if (!hasPos) {
            val dtp = Np.fillNaN(Np.diff(df["t"]), 0.0).let { a -> DoubleArray(n) { max(0.0, a[it]) } }
            df["track_position"] = Np.cumsum(DoubleArray(n) { speedMs[it] * dtp[it] })
            df.posDerived = true
        }
        df.compact = compact
        for (c in listOf("susp_fl", "susp_fr", "susp_rl", "susp_rr")) { val a = df[c]; df[c] = DoubleArray(n) { a[it] * 1000.0 } }
        addDerived(df, fuelClipHi = 100.0, dt = stepDt(df["t"]))
        return LapFile(raw.meta, df)
    }

    /** dt per sample: non-positive / missing gaps get the median step (0.1 s when none). */
    fun stepDt(t: DoubleArray, strict0: Boolean = false): DoubleArray {
        val d = Np.diff(t)
        val ok = d.filter { !it.isNaN() && it > (if (strict0) 0.0 else 1e-4) }.toDoubleArray()
        val fb = if (ok.isNotEmpty()) Np.median(ok) else 0.1
        return DoubleArray(d.size) { val x = d[it]; if (x.isNaN() || !(x > (if (strict0) 0.0 else 1e-4))) fb else x }
    }

    /** Derived columns shared by the lap and race loaders. */
    fun addDerived(df: Frame, fuelClipHi: Double, dt: DoubleArray) {
        val n = df.n
        val spd = df["speed_kmh"]; val angY = df["ang_y"]
        val dvRaw = Np.diff(spd)
        val longG = DoubleArray(n) { val dv = (if (dvRaw[it].isNaN()) 0.0 else dvRaw[it]) / 3.6; min(4.0, max(-4.0, dv / dt[it] / 9.81)) }
        val latG = DoubleArray(n) { min(4.0, max(-4.0, angY[it] * spd[it] / 3.6 / 9.81)) }
        df["long_g"] = longG; df["lat_g"] = latG
        df["total_g"] = DoubleArray(n) { sqrt(longG[it] * longG[it] + latG[it] * latG[it]) }
        val fd = Np.diff(df["fuel_remaining"])
        df["fuel_burn"] = DoubleArray(n) { val x = -fd[it] / dt[it]; if (x.isNaN()) 0.0 else min(fuelClipHi, max(0.0, x)) }
        val thr = df["throttle"]; val brk = df["brake"]
        df["coasting"] = DoubleArray(n) { if (thr[it] < 0.05 && brk[it] < 0.05) 1.0 else 0.0 }
        val fl = df["tyre_temp_fl"]; val fr = df["tyre_temp_fr"]; val rl = df["tyre_temp_rl"]; val rr = df["tyre_temp_rr"]
        df["front_t_avg"] = DoubleArray(n) { (fl[it] + fr[it]) / 2 }
        df["rear_t_avg"] = DoubleArray(n) { (rl[it] + rr[it]) / 2 }
        df["lr_t_bal"] = DoubleArray(n) { (fl[it] + rl[it]) / 2 - (fr[it] + rr[it]) / 2 }
    }
}

object LapMath {
    /** share_card.fmt_time / lap_analyst._fmt_laptime. */
    fun fmtTime(t: Double, decimals: Int = 3): String {
        val blank = "--:--." + "-".repeat(decimals)
        if (t.isNaN() || t.isInfinite()) return blank
        val unit = 10.0.pow(decimals).toLong()
        val total = Math.rint(t * unit).toLong()
        val m = Math.floorDiv(total, 60 * unit)
        val rest = Math.floorMod(total, 60 * unit)
        val width = if (decimals > 0) 3 + decimals else 2
        return "$m:" + fixed(rest.toDouble() / unit, decimals).padStart(width, '0')
    }
    fun fmtLapTime(t: Any?): String = fmtTime(numOf(t))

    fun isCurrentEra(meta: Meta) = TraceUtil.isCurrentEra(meta.text("physics_era"), (meta["recorded_at"])?.toString())

    fun lapLabel(meta: Meta, short: Boolean = false): String {
        val t = numOf(meta["lap_time_s"])
        var timeStr = fmtTime(t)
        val car = (meta["car"]?.toString()?.takeIf { it.isNotEmpty() && it != "false" && it != "0" }) ?: "?"
        val track = pyTitle((meta["track"]?.toString()?.takeIf { it.isNotEmpty() } ?: "?").replace("_", " "))
        if (!isCurrentEra(meta)) timeStr += " [pre-${TraceUtil.PHYSICS_ERA}]"
        return if (short) "$car  $timeStr" else "$car @ $track  $timeStr"
    }

    /** (position, time), both non-decreasing. */
    fun lapXY(df: Frame): Pair<DoubleArray, DoubleArray> =
        Np.cummax(Np.nanToNum(df["track_position"])) to Np.cummax(Np.nanToNum(df["t"]))

    class Sectors(val segs: List<IntArray>, val times: DoubleArray)

    fun lapSectors(df: Frame, n: Int): Sectors {
        val (pos, t) = lapXY(df)
        val length = if (pos.isNotEmpty()) pos[pos.size - 1] else 0.0
        if (df.n < 2 || length <= 0) return Sectors(emptyList(), DoubleArray(0))
        val edges = Np.linspace(0.0, length, n + 1)
        val tEdges = Np.interp(edges, pos, t)
        val idx = IntArray(df.n) { min(n - 1, max(0, Np.searchsorted(edges, pos[it], true) - 1)) }
        val lists = Array(n) { ArrayList<Int>() }
        for (i in 0 until df.n) lists[idx[i]].add(i)
        return Sectors(lists.map { it.toIntArray() }, DoubleArray(n) { tEdges[it + 1] - tEdges[it] })
    }

    fun computeSectorDeltas(a: Frame, b: Frame, sectorLengthM: Double = 25.0): DoubleArray {
        val (pa, ta) = lapXY(a); val (pb, tb) = lapXY(b)
        val la = max(pa[pa.size - 1], 1.0); val lb = max(pb[pb.size - 1], 1.0)
        val nSec = max(1, ceil(la / sectorLengthM).toInt())
        val edgesA = Np.linspace(0.0, la, nSec + 1)
        val edgesB = DoubleArray(nSec + 1) { edgesA[it] / la * lb }
        val dA = Np.diffN(Np.interp(edgesA, pa, ta))
        val dB = Np.diffN(Np.interp(edgesB, pb, tb))
        return DoubleArray(a.n) { val s = min(nSec - 1, max(0, Np.searchsorted(edgesA, pa[it], true) - 1)); dA[s] - dB[s] }
    }

    fun buildStats(meta: Meta, df: Frame): List<Pair<String, String>> {
        val t = numOf(meta["lap_time_s"])
        val fuel = df["fuel_remaining"]
        val fuelUsed = fuel[0] - fuel[df.n - 1]
        val gear = df["gear"]
        var changes = 0; for (i in 1 until df.n) if (gear[i] - gear[i - 1] != 0.0) changes++
        val temps = listOf("tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr").map { Np.mean(df[it]) }
        val thr = df["throttle"]
        return listOf(
            "Lap Time" to fmtTime(t),
            "Samples" to "${df.n}",
            "Top Speed" to fixed(Np.max(df["speed_kmh"]), 1) + " km/h",
            "Avg Speed" to fixed(Np.mean(df["speed_kmh"]), 1) + " km/h",
            "Full Thr%" to fixed(thr.count { it > 0.95 }.toDouble() / df.n * 100, 1) + "%",
            "Coasting%" to fixed(Np.mean(df["coasting"]) * 100, 1) + "%",
            "Max Lat G" to fixed(df["lat_g"].maxOf { abs(it) }, 2) + "g",
            "Max Long G" to fixed(df["long_g"].maxOf { abs(it) }, 2) + "g",
            "Fuel Used" to fixed(fuelUsed, 2),
            "Peak RPM" to fixed(Np.max(df["rpm"]), 0),
            "Gear Changes" to "$changes",
            "Avg Tyre °C" to fixed(Np.mean(temps.toDoubleArray()), 1),
        )
    }

    /** Thirds of the lap by track position: (name, time, avg speed, top speed). */
    fun buildSectorStats(df: Frame): List<List<String>> {
        val s = lapSectors(df, 3)
        val out = ArrayList<List<String>>()
        for ((i, seg) in s.segs.withIndex()) {
            if (seg.isEmpty()) continue
            val sp = DoubleArray(seg.size) { df["speed_kmh"][seg[it]] }
            out.add(listOf("S${i + 1}", fixed(s.times[i], 2) + "s", fixed(Np.mean(sp), 0), fixed(Np.max(sp), 0)))
        }
        return out
    }

    fun safeName(text: String?, default: String = "lap"): String {
        val s = (text ?: "").trim().map { if (it.isLetterOrDigit() || it in "-_.") it else '_' }.joinToString("")
        return s.trim('.', '_').ifEmpty { default }
    }

    /** lap_analyst.export_csv: the 7-column replay export. */
    fun exportCsv(df: Frame): String {
        val n = df.n
        val pos = df["track_position"]
        val dist: DoubleArray
        if (n > 0 && Np.max(pos) - Np.min(pos) > 0) dist = DoubleArray(n) { pos[it] - pos[0] }
        else {
            val dt = LapLoader.stepDt(df["t"])
            dist = Np.cumsum(DoubleArray(n) { df["speed_kmh"][it] / 3.6 * dt[it] })
        }
        val sb = StringBuilder("distance_m,speed_kmh,throttle,brake,rpm,gear,steering\r\n")
        for (i in 0 until n) {
            sb.append(pyRound(dist[i], 2)).append(',').append(pyRound(df["speed_kmh"][i], 2)).append(',')
                .append(pyRound(df["throttle"][i], 4)).append(',').append(pyRound(df["brake"][i], 4)).append(',')
                .append(pyRound(df["rpm"][i], 1)).append(',').append(df["gear"][i].toInt()).append(',')
                .append(pyRound(df["steering"][i], 4)).append("\r\n")
        }
        return sb.toString()
    }

    /** repr of round(x, d), the way pandas writes floats. */
    fun pyRound(x: Double, d: Int): String {
        if (x.isNaN()) return ""
        val m = Math.pow(10.0, d.toDouble()); val r = Math.rint(x * m) / m
        return pyRepr(r)
    }

    fun pyRepr(x: Double): String {
        if (x.isNaN()) return "nan"
        if (x.isInfinite()) return if (x > 0) "inf" else "-inf"
        if (x == 0.0) return if (1.0 / x < 0) "-0.0" else "0.0"
        val bd = java.math.BigDecimal(java.lang.Double.toString(abs(x)))
        var digits = bd.unscaledValue().toString().trimEnd('0')
        if (digits.isEmpty()) digits = "0"
        val e = bd.precision() - bd.scale() - 1          // scientific exponent
        val body = if (e in -4..15) {
            if (e >= 0) {
                val intPart = digits.padEnd(e + 1, '0').substring(0, e + 1)
                val frac = if (digits.length > e + 1) digits.substring(e + 1) else "0"
                "$intPart.$frac"
            } else "0." + "0".repeat(-e - 1) + digits
        } else {
            val m = if (digits.length > 1) digits[0] + "." + digits.substring(1) else digits
            m + "e" + (if (e < 0) "-" else "+") + abs(e).toString().padStart(2, '0')
        }
        return if (x < 0) "-$body" else body
    }

    /** Whole frame as CSV (lap_analyst._save_df_csv). */
    fun frameCsv(df: Frame): String {
        val sb = StringBuilder()
        val names = df.cols.keys.toList() + df.strs.keys.filter { !df.cols.containsKey(it) }
        sb.append(names.joinToString(",")).append('\n')
        for (i in 0 until df.n) {
            sb.append(names.joinToString(",") { k ->
                df.cols[k]?.let { pyRepr(it[i]) } ?: (df.strs[k]!![i].let { s -> if (s.any { it == ',' || it == '"' || it == '\n' }) "\"" + s.replace("\"", "\"\"") + "\"" else s })
            }).append('\n')
        }
        return sb.toString()
    }

    fun lapFuelUsed(df: Frame): String? {
        if (!df.has("fuel_remaining") || df.n < 2) return null
        val used = df["fuel_remaining"][0] - df["fuel_remaining"][df.n - 1]
        if (!used.isFinite() || used <= 0) return null
        val cap = if (df.has("fuel_capacity")) Np.max(df["fuel_capacity"]) else 100.0
        return when (cap) {
            100.0 -> fixed(used, 1) + "%"
            0.0 -> fixed(used, 1) + "% battery"
            else -> fixed(used, 2) + " of " + cap.toString().removeSuffix(".0")
        }
    }
}
