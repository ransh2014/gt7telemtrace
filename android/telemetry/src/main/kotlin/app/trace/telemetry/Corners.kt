package app.trace.telemetry

import kotlin.math.*

/** corners.py: per-corner analysis for the Lap Analyst "Corners" tab and the Race Analyst. */
object Corners {
    const val LAT_G_MIN = 0.5
    const val MERGE_GAP_M = 40.0
    const val MIN_CORNER_M = 15.0
    const val BRAKE_ON = 0.1
    const val THROTTLE_ON = 0.5
    const val BRAKE_LOOKBACK_M = 250.0
    const val MATCH_TOL_M = 80.0
    const val LABEL_BRAKE_M = 6.0
    const val LABEL_THROTTLE_M = 5.0
    const val TIME_LOSS_MIN_S = 0.005
    const val CLEAN_LAP_FACTOR = 1.07

    class Corner(val number: Int, val start: Double, val end: Double, val apex: Double, val brakePos: Double?,
                 val throttlePos: Double?, val minSpeed: Double, val brakePeak: Double, val trail: Boolean)

    class CornerDelta(val number: Int, val refNumber: Int, val brakeM: Double?, val minSpeedKmh: Double,
                      val throttleM: Double?, val timeS: Double?)

    class CornerSpread(val refNumber: Int, val n: Int, val meanTimeS: Double, val stdTimeS: Double, val stdBrakeM: Double?)

    private val NEEDED = listOf("track_position", "speed_kmh", "throttle", "brake", "lat_g", "t")

    /** Sorted by position, duplicate positions dropped; null when the lap is too short to read. */
    fun prepare(df: Frame?): Frame? {
        if (df == null || NEEDED.any { !df.has(it) } || df.n < 20) return null
        val s = df.sortedBy("track_position")
        val pos = s["track_position"]
        val keep = ArrayList<Int>()
        for (i in 0 until s.n) if (i == 0 || pos[i] != pos[i - 1]) keep.add(i)   // sorted, so equal values are adjacent
        val d = s.take(keep.toIntArray())
        if (d.n == 0 || Np.max(d["track_position"]) - Np.min(d["track_position"]) < 100) return null
        return d
    }

    fun detectCorners(df: Frame?): List<Corner> {
        val d = prepare(df) ?: return emptyList()
        val pos = d["track_position"]; val spd = d["speed_kmh"]; val thr = d["throttle"]; val brk = d["brake"]
        val lat = d["lat_g"]; val n = d.n
        val inside = BooleanArray(n) { abs(lat[it]) > LAT_G_MIN }
        val runs = ArrayList<IntArray>()
        var i = 0
        while (i < n) {
            if (inside[i]) { var j = i; while (j + 1 < n && inside[j + 1]) j++; runs.add(intArrayOf(i, j)); i = j + 1 } else i++
        }
        val merged = ArrayList<IntArray>()
        for (r in runs) {
            if (merged.isNotEmpty() && pos[r[0]] - pos[merged.last()[1]] < MERGE_GAP_M) merged.last()[1] = r[1] else merged.add(r)
        }
        val kept = merged.filter { pos[it[1]] - pos[it[0]] >= MIN_CORNER_M }
        val out = ArrayList<Corner>()
        for ((k, ab) in kept.withIndex()) {
            val a = ab[0]; val b = ab[1]
            var apex = a
            for (q in a..b) if (spd[q] < spd[apex]) apex = q
            val floor = if (k > 0) pos[kept[k - 1][1]] else pos[0]
            val loPos = max(floor, pos[a] - BRAKE_LOOKBACK_M)
            var zoneFirst = -1; var zonePeak = 0.0
            for (q in 0..apex) if (pos[q] >= loPos && brk[q] > BRAKE_ON) { if (zoneFirst < 0) zoneFirst = q; if (brk[q] > zonePeak) zonePeak = brk[q] }
            val brakePos = if (zoneFirst >= 0) pos[zoneFirst] else null
            var after = -1
            for (q in apex + 1 until n) if (thr[q] > THROTTLE_ON) { after = q; break }
            out.add(Corner(k + 1, pos[a], pos[b], pos[apex], brakePos, if (after >= 0) pos[after] else null,
                spd[apex], if (zoneFirst >= 0) zonePeak else 0.0, brk[apex] > BRAKE_ON))
        }
        return out
    }

    private fun timeBetween(d: Frame, p0: Double, p1: Double): Double {
        val pos = d["track_position"]; val t = d["t"]
        return Np.interp(p1, pos, t) - Np.interp(p0, pos, t)
    }

    class Matched(val a: List<Corner>, val b: List<Corner>, val deltas: List<CornerDelta>)

    fun matchCorners(df: Frame, ref: Frame): Matched {
        val a = detectCorners(df); val b = detectCorners(ref)
        val da = prepare(df); val db = prepare(ref)
        val deltas = ArrayList<CornerDelta>()
        if (da == null || db == null) return Matched(a, b, deltas)
        val used = HashSet<Int>()
        for (ca in a) {
            var best: Corner? = null; var bestGap = MATCH_TOL_M
            for (cb in b) {
                val gap = abs(cb.apex - ca.apex)
                if (cb.number !in used && gap <= bestGap) { best = cb; bestGap = gap }
            }
            if (best == null) continue
            used.add(best.number)
            val entry = listOfNotNull(best.brakePos, best.start).min()
            val exit = listOfNotNull(best.throttlePos, best.end).max()
            val timeS = timeBetween(da, entry, exit) - timeBetween(db, entry, exit)
            val cb0 = best.brakePos; val ca0 = ca.brakePos; val ct0 = ca.throttlePos; val bt0 = best.throttlePos
            deltas.add(CornerDelta(ca.number, best.number,
                if (ca0 != null && cb0 != null) ca0 - cb0 else null,
                ca.minSpeed - best.minSpeed,
                if (ct0 != null && bt0 != null) ct0 - bt0 else null,
                timeS))
        }
        return Matched(a, b, deltas)
    }

    private fun smoothness(df: Frame): String {
        val agg = min(100.0, meanAbsDiff(df["steering"]) * 220 + meanAbsDiff(df["throttle"]) * 140 + meanAbsDiff(df["brake"]) * 140)
        return if (agg < 33) "Smooth" else if (agg < 66) "Balanced" else "Aggressive"
    }

    /** mean of |diff| with the first element filled with 0 (pandas .diff().abs().fillna(0).mean()). */
    fun meanAbsDiff(a: DoubleArray, k: Double = 1.0): Double {
        if (a.isEmpty()) return Double.NaN
        var s = 0.0
        for (i in 1 until a.size) s += abs(a[i] - a[i - 1]) * k
        return s / a.size
    }

    fun sessionLabel(df: Frame, corners: List<Corner>, deltas: List<CornerDelta>): String {
        val parts = ArrayList<String>()
        val brake = deltas.mapNotNull { it.brakeM }
        val thr = deltas.mapNotNull { it.throttleM }
        if (brake.isNotEmpty()) {
            val m = brake.average()
            parts.add(if (m >= LABEL_BRAKE_M) "Late braker" else if (m <= -LABEL_BRAKE_M) "Early braker" else "PB braking points")
            if (thr.isNotEmpty()) {
                val t = thr.average()
                if (t <= -LABEL_THROTTLE_M) parts.add("early on throttle") else if (t >= LABEL_THROTTLE_M) parts.add("late on throttle")
            }
        } else if (corners.isNotEmpty()) {
            val braked = corners.filter { it.brakePos != null }
            if (braked.isNotEmpty()) {
                val trail = braked.count { it.trail }.toDouble() / braked.size
                parts.add(if (trail >= 0.5) "Trail braker" else "Straight-line braker")
            }
        }
        val smooth = smoothness(df)
        if (smooth.isNotEmpty()) parts.add(smooth.lowercase())
        val s = parts.joinToString(", ")
        return if (s.isEmpty()) s else s.substring(0, 1).uppercase() + s.substring(1)
    }

    fun timeLossSummary(deltas: List<CornerDelta>, n: Int = 3): List<CornerDelta> =
        deltas.filter { it.timeS != null && it.timeS > TIME_LOSS_MIN_S }.sortedByDescending { it.timeS }.take(n)

    fun describeTimeLoss(deltas: List<CornerDelta>, n: Int = 3): String {
        val worst = timeLossSummary(deltas, n)
        if (worst.isEmpty()) return "No corner lost time against the reference"
        return "Time lost: " + worst.joinToString(", ") { "T${it.number} +${fixed(it.timeS!!, 2)}s" }
    }

    class Scored(val refCorners: List<Corner>, val perLap: List<Pair<Int, Map<Int, CornerDelta>>>)

    fun scoreLaps(laps: List<Pair<Int, Frame>>, ref: Frame): Scored {
        val refCorners = detectCorners(ref)
        val out = ArrayList<Pair<Int, Map<Int, CornerDelta>>>()
        for ((id, df) in laps) {
            val deltas = try { matchCorners(df, ref).deltas } catch (e: Exception) { emptyList() }
            out.add(id to deltas.associateBy { it.refNumber })
        }
        return Scored(refCorners, out)
    }

    fun cornerSpreads(scored: List<Pair<Int, Map<Int, CornerDelta>>>, minLaps: Int = 3): List<CornerSpread> {
        val byCorner = LinkedHashMap<Int, ArrayList<CornerDelta>>()
        for ((_, deltas) in scored) for ((refNo, d) in deltas) if (d.timeS != null) byCorner.getOrPut(refNo) { ArrayList() }.add(d)
        val out = ArrayList<CornerSpread>()
        for ((refNo, ds) in byCorner) {
            if (ds.size < minLaps) continue
            val times = DoubleArray(ds.size) { ds[it].timeS!! }
            val brakes = ds.mapNotNull { it.brakeM }.toDoubleArray()
            out.add(CornerSpread(refNo, ds.size, Np.mean(times), Np.std(times, 1), if (brakes.size >= minLaps) Np.std(brakes, 1) else null))
        }
        return out.sortedBy { it.stdTimeS }
    }
}

/** theoretical.py: stitch the fastest micro-sector of every lap into the lap you could drive. */
object Theoretical {
    const val MICRO_SECTOR_M = 25.0
    const val ZONE_M = 100.0
    const val MAX_LENGTH_SPREAD = 0.05
    const val MIN_LAPS = 2
    const val LOSS_MIN_S = 0.001

    class Zone(val start: Double, val end: Double, val loss: Double, val donor: Int)

    class Result(val labels: List<String>, val totals: DoubleArray, val bestIdx: Int, val actual: Double, val theoretical: Double,
                 val gap: Double, val centers: DoubleArray, val donor: IntArray, val loss: DoubleArray, val cumGap: DoubleArray,
                 val zones: List<Zone>, val skipped: Int)

    private fun curve(df: Frame) = LapMath.lapXY(df)

    fun usable(df: Frame?): Boolean {
        if (df == null || !df.has("track_position") || !df.has("t") || df.n < 20) return false
        val (pos, t) = curve(df)
        return pos[pos.size - 1] - pos[0] > 100 && t[t.size - 1] - t[0] > 1
    }

    fun sectorTimes(df: Frame, nSectors: Int): DoubleArray {
        val (pos, t) = curve(df)
        val edges = Np.linspace(pos[0], pos[pos.size - 1], nSectors + 1)
        return Np.diffN(Np.interp(edges, pos, t))
    }

    fun theoreticalBest(laps: List<Pair<String, Frame>>, sectorM: Double = MICRO_SECTOR_M, zoneM: Double = ZONE_M): Result? {
        var good = laps.filter { usable(it.second) }
        var skipped = laps.size - good.size
        var lengths = DoubleArray(0)
        if (good.size >= 2) {
            lengths = DoubleArray(good.size) { val (p, _) = curve(good[it].second); p[p.size - 1] - p[0] }
            val med = Np.median(lengths)
            val keep = BooleanArray(good.size) { abs(lengths[it] - med) <= MAX_LENGTH_SPREAD * med }
            skipped += keep.count { !it }
            good = good.filterIndexed { i, _ -> keep[i] }
            lengths = lengths.filterIndexed { i, _ -> keep[i] }.toDoubleArray()
        } else if (good.size == 1) lengths = DoubleArray(1) { val (p, _) = curve(good[0].second); p[p.size - 1] - p[0] }
        if (good.size < MIN_LAPS) return null

        val n = max(4, Math.rint(Np.median(lengths) / sectorM).toInt())
        val mat = Array(good.size) { sectorTimes(good[it].second, n) }
        val totals = DoubleArray(good.size) { Np.sum(mat[it]) }
        var bestIdx = 0
        for (i in totals.indices) if (totals[i] < totals[bestIdx]) bestIdx = i
        val fastest = DoubleArray(n) { s -> var m = Double.POSITIVE_INFINITY; for (r in mat) if (r[s] < m) m = r[s]; m }
        val donor = IntArray(n) { s -> var bi = 0; for (r in mat.indices) if (mat[r][s] < mat[bi][s]) bi = r; bi }
        val loss = DoubleArray(n) { mat[bestIdx][it] - fastest[it] }
        val length = lengths[bestIdx]
        val edges = Np.linspace(0.0, length, n + 1)
        val centers = DoubleArray(n) { (edges[it] + edges[it + 1]) / 2 }
        val perZone = max(1, Math.rint(zoneM / (length / n)).toInt())
        val zones = ArrayList<Zone>()
        var a = 0
        while (a < n) {
            val b = min(n, a + perZone)
            var zl = 0.0; for (q in a until b) zl += loss[q]
            if (zl >= LOSS_MIN_S) {
                var bestGain = Double.NEGATIVE_INFINITY; var bestLap = 0
                for (r in mat.indices) {
                    var sBest = 0.0; var sR = 0.0
                    for (q in a until b) { sBest += mat[bestIdx][q]; sR += mat[r][q] }
                    val gain = sBest - sR
                    if (gain > bestGain) { bestGain = gain; bestLap = r }
                }
                zones.add(Zone(edges[a], edges[b], zl, bestLap))
            }
            a += perZone
        }
        val sorted = zones.sortedByDescending { it.loss }
        return Result(good.map { it.first }, totals, bestIdx, totals[bestIdx], Np.sum(fastest), totals[bestIdx] - Np.sum(fastest),
            centers, donor, loss, Np.cumsum(loss), sorted, skipped)
    }
}

/** consistency.py */
object Consistency {
    const val CLEAN_LAP_FACTOR = 1.07
    const val SCORE_ZERO_CV = 0.02
    const val MIN_LAPS_FOR_SCORE = 3
    const val MIN_LAPS_FOR_TREND = 3

    private fun usable(t: Double) = t.isFinite() && t > 1.0

    fun cleanLaps(times: List<Double>, factor: Double = CLEAN_LAP_FACTOR): List<Pair<Int, Double>> {
        val valid = times.withIndex().filter { usable(it.value) }.map { it.index to it.value }
        if (valid.isEmpty()) return emptyList()
        val best = valid.minOf { it.second }
        return valid.filter { it.second <= best * factor }
    }

    class Stats(val n: Int, val nClean: Int, val best: Double, val avg: Double, val std: Double, val spread: Double,
                val trend: Double?, val score: Int?, val cleanIdx: List<Int>)

    fun lapTimeStats(times: List<Double>, factor: Double = CLEAN_LAP_FACTOR): Stats? {
        val valid = times.filter { usable(it) }
        if (valid.isEmpty()) return null
        val clean = cleanLaps(times, factor)
        val ys = DoubleArray(clean.size) { clean[it].second }
        val xs = DoubleArray(clean.size) { clean[it].first.toDouble() }
        val avg = Np.mean(ys)
        val std = if (ys.size > 1) Np.std(ys, 1) else 0.0
        var trend: Double? = null
        if (ys.size >= MIN_LAPS_FOR_TREND && Np.max(xs) - Np.min(xs) > 0) trend = Np.polyfit1(xs, ys).first
        var score: Int? = null
        if (ys.size >= MIN_LAPS_FOR_SCORE && avg > 0)
            score = Math.rint(100 * min(1.0, max(0.0, 1.0 - (std / avg) / SCORE_ZERO_CV))).toInt()
        return Stats(valid.size, ys.size, Np.min(ys), avg, std, Np.max(ys) - Np.min(ys), trend, score, clean.map { it.first })
    }

    fun trendLabel(trend: Double?): String {
        if (trend == null) return "not enough laps"
        if (abs(trend) < 0.03) return "flat"
        return (if (trend > 0) "slowing " else "speeding up ") + fixed(abs(trend), 2) + " s/lap"
    }
}
