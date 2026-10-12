package app.trace.telemetry

import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.math.max
import kotlin.math.round
import kotlin.math.sqrt

/** Port of the Dashboard's lap/race recording, save format, personal bests and reference laps. */
class Recorder(
    private val lapsDir: File,
    private val log: (String) -> Unit,
    private val mono: () -> Double = { System.nanoTime() / 1e9 },
    private val onLapSaved: (LapMeta) -> Unit = {},
) {
    data class LapMeta(val lapNum: Int, val lapTimeS: Double, val track: String, val car: String, val incomplete: Boolean, val isPb: Boolean, val file: File?)
    data class Ctx(val track: String, val car: String, val gearRatios: List<Double>)

    @Volatile var recording = false; private set
    @Volatile var waiting = false; private set
    @Volatile var raceRecording = false; private set
    @Volatile var paused = false; private set
    var recordRate = 60
    val lapsSaved = ArrayList<JSONObject>()
    val sessionSummary = ArrayList<JSONObject>()
    private var samples = ArrayList<Map<String, Any?>>()
    private var raceSamples = ArrayList<Map<String, Any?>>()
    private var startT = 0.0
    private var raceStartT = 0.0
    private var prevLap = 0
    private var pausedAt: Double? = null
    private var recDue = 0.0
    private var recDueRace = 0.0
    val sessionStartTs: String = ts()
    var fuelPerLap = 0.0; private set

    private fun ts() = SimpleDateFormat("yyyyMMdd_HHmmss", Locale.ROOT).format(Date())

    // ── record lap ────────────────────────────────────────────────────────────
    /** A manually stopped, unfinished lap waits here for the user's Save / Discard (dashboard._save_lap prompt=True). */
    @Volatile var pendingIncomplete: List<Map<String, Any?>>? = null; private set
    private var pendingCtx: Ctx? = null
    fun resolvePending(save: Boolean) {
        val s = pendingIncomplete; val c = pendingCtx
        pendingIncomplete = null; pendingCtx = null
        if (save && s != null && c != null) saveLap(s, null, true, c) else if (s != null) log("Incomplete lap discarded")
    }

    fun toggleLap(currentLap: Int, ctx: Ctx) { if (recording || waiting) stopLap(ctx, prompt = true) else startLap(currentLap) }

    fun startLap(currentLap: Int) {
        waiting = true; recording = false; samples = ArrayList(); prevLap = currentLap; recDue = 0.0
        log("Armed -- cross the start line to begin recording")
    }

    fun stopLap(ctx: Ctx, prompt: Boolean = false) {
        recording = false; waiting = false
        val s = samples; samples = ArrayList()
        if (s.isEmpty()) log("Stopped -- no samples recorded.")
        else if (prompt) { pendingCtx = ctx; pendingIncomplete = s }
        else saveLap(s, null, true, ctx)
    }

    fun toggleRace() { if (raceRecording) stopRace(false, null) else startRace() }
    fun startRace() { raceRecording = true; raceSamples = ArrayList(); raceStartT = mono(); recDueRace = 0.0; log("Race recording started") }

    fun stopRace(notifyEnd: Boolean, ctx: Ctx?) {
        raceRecording = false
        val s = raceSamples; raceSamples = ArrayList()
        if (s.isNotEmpty() && ctx != null) saveRace(s, ctx) else log("Race recording stopped -- no samples recorded.")
    }

    fun onPause() { if (!paused) pausedAt = mono(); paused = true; log("Auto: game paused -- recording frozen") }
    fun onResume(deltaLapStartShift: (Double) -> Unit = {}) {
        val pa = pausedAt
        if (paused && pa != null) {
            val gap = max(0.0, mono() - pa)
            startT += gap; raceStartT += gap; deltaLapStartShift(gap)
        }
        pausedAt = null; paused = false; log("Auto: game resumed -- recording continues")
    }
    fun onRaceStart() { if (!raceRecording) { startRace(); log("Auto: race start detected -- recording started") } }
    fun onRaceEnd(ctx: Ctx) { if (raceRecording) { log("Auto: race end detected -- stopping recording"); stopRace(true, ctx) } }

    private fun nextDue(due: Double, now: Double, dt: Double): Double {
        val nxt = if (due > 0) due + dt else now + dt
        return if (nxt <= now) now + dt else nxt
    }

    /** Call at ~ 60 Hz with the latest snapshot (dashboard._record_tick). */
    fun tick(d: Packet, connected: Boolean, ctx: Ctx) {
        if (paused || !connected) return
        val minDt = 1.0 / max(recordRate, 1)
        val now = mono()
        if ((recording || waiting) && now >= recDue) { recDue = nextDue(recDue, now, minDt); recordSample(d, ctx) }
        if (raceRecording && now >= recDueRace) { recDueRace = nextDue(recDueRace, now, minDt); recordRaceSample(d, ctx) }
    }

    private fun recordSample(d: Packet, ctx: Ctx) {
        val lap = (d["lap_number"] as? Number)?.toInt() ?: prevLap
        if (waiting) {
            if (lap > prevLap) {
                waiting = false; recording = true; startT = mono(); prevLap = lap
                log("Start line crossed -- recording lap $lap")
            } else prevLap = lap
            return
        }
        if (lap < prevLap) {
            val n = samples.size
            if (n >= 100) { log("Lap counter reset (lap $prevLap -> $lap) -- saving the partial lap ($n samples)"); saveLap(samples, null, true, ctx) }
            else log("Lap counter reset -- discarded a short partial lap")
            samples = ArrayList(); prevLap = lap; recording = false; waiting = true
            return
        }
        if (lap > prevLap) {
            val n = samples.size
            if (n >= 100) {
                log("Lap complete -- $n samples")
                val lastMs = (d["last_lap_ms"] as? Number)?.toInt() ?: 0
                saveLap(samples, if (lastMs > 0) lastMs else null, false, ctx)
            } else log("Lap changed after only $n samples -- not saved")
            samples = ArrayList(); startT = mono()
        }
        prevLap = lap
        samples.add(sampleDict(d, startT))
    }

    private fun recordRaceSample(d: Packet, ctx: Ctx) {
        if (!raceRecording) return
        raceSamples.add(sampleDict(d, raceStartT))
        val n = raceSamples.size
        if (n == RACE_SAMPLE_WARN) log("Race recording is large ($n samples). Consider stopping to save, or a lower REC RATE.")
        else if (n >= RACE_SAMPLE_MAX) { log("Race recording hit the $RACE_SAMPLE_MAX-sample limit -- stopping and saving now."); stopRace(false, ctx) }
    }

    // ── sample dict (dashboard._sample_dict) ─────────────────────────────────
    private fun r4(x: Double) = round(x * 10000.0) / 10000.0

    fun sampleDict(d: Packet, startT: Double): Map<String, Any?> {
        fun f(k: String) = r4((d[k] as? Number)?.toDouble() ?: 0.0)
        fun i(k: String) = (d[k] as? Number)?.toInt() ?: 0
        fun b(k: String) = d[k] as? Boolean ?: false
        val wsa = d["wheel_steering_angle"] as? List<*>
        fun wt(idx: Int) = wsa?.getOrNull(idx)?.let { r4((it as Number).toDouble()) } ?: 0.0
        val m = LinkedHashMap<String, Any?>()
        m["t"] = round((mono() - startT) * 1000.0) / 1000.0
        for (k in listOf("world_x", "world_y", "world_z", "heading", "track_position", "speed_kmh", "vel_x", "vel_y", "vel_z",
            "ang_x", "ang_y", "ang_z", "throttle", "brake", "steering", "clutch")) m[k] = f(k)
        m["steering_source"] = (d["steering_source"] as? String) ?: "heading"
        m["clutch_engaged"] = f("clutch_engaged")
        m["gear"] = i("gear"); m["suggested_gear"] = i("suggested_gear")
        m["rpm"] = f("rpm"); m["max_rpm"] = i("max_rpm"); m["rpm_warning"] = i("rpm_warning"); m["rpm_limiter"] = i("rpm_limiter")
        m["rpm_after_clutch"] = f("rpm_after_clutch"); m["boost"] = f("boost")
        for (k in listOf("tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr", "tyre_slip_fl", "tyre_slip_fr", "tyre_slip_rl", "tyre_slip_rr",
            "susp_fl", "susp_fr", "susp_rl", "susp_rr", "ride_height_mm", "fuel_remaining", "fuel_capacity")) m[k] = f(k)
        m["is_ev"] = b("is_ev")
        m["oil_temp"] = f("oil_temp"); m["water_temp"] = f("water_temp"); m["oil_pressure"] = f("oil_pressure")
        m["lap_number"] = i("lap_number"); m["current_position"] = i("current_position"); m["total_positions"] = i("total_positions")
        m["in_pit"] = b("in_pit"); m["time_of_day"] = i("time_of_day"); m["est_top_speed"] = i("est_top_speed"); m["car_id"] = i("car_id")
        m["flags_8e"] = i("flags_8e"); m["flags_8f"] = i("flags_8f"); m["flags_93"] = i("flags_93")
        m["wheel_rotation"] = f("wheel_rotation"); m["steering_angular_velocity"] = f("steering_angular_velocity")
        m["sway"] = f("sway"); m["heave"] = f("heave"); m["surge"] = f("surge")
        m["torque_vectors"] = (d["torque_vectors"] as? List<*>) ?: emptyList<Double>()
        m["energy_recovery"] = f("energy_recovery"); m["throttle_filtered"] = f("throttle_filtered"); m["brake_filtered"] = f("brake_filtered")
        m["surface_type"] = ((d["surface_type"] as? List<*>) ?: emptyList<String>()).joinToString("")
        m["current_lap_ms"] = i("current_lap_ms")
        m["wheel_steering_angle_l"] = wt(0); m["wheel_steering_angle_r"] = wt(1)
        m["wheel_base"] = f("wheel_base"); m["car_category"] = (d["car_category"] as? String) ?: ""
        return m
    }

    // ── saving ───────────────────────────────────────────────────────────────
    private fun toJson(v: Any?): Any? = when (v) {
        null -> JSONObject.NULL
        is Map<*, *> -> JSONObject().also { o -> v.forEach { (k, x) -> o.put(k as String, toJson(x)) } }
        is List<*> -> JSONArray().also { a -> v.forEach { a.put(toJson(it)) } }
        else -> v
    }

    private fun saveJson(f: File, data: JSONObject, what: String): File? = try {
        f.parentFile?.mkdirs()
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(data.toString())
        if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
        f
    } catch (e: Exception) { log("SAVE FAILED: couldn't write $what to ${f.parent} (${e.message})"); null }

    fun lapDistance(s: List<Map<String, Any?>>): Double {
        fun g(m: Map<String, Any?>, k: String) = (m[k] as? Number)?.toDouble() ?: 0.0
        if (s.any { g(it, "world_x") != 0.0 })
            return (1 until s.size).sumOf { i -> sqrt((g(s[i], "world_x") - g(s[i - 1], "world_x")).let { it * it } + (g(s[i], "world_z") - g(s[i - 1], "world_z")).let { it * it }) }
        return (1 until s.size).sumOf { i -> g(s[i], "speed_kmh") / 3.6 * max(0.0, g(s[i], "t") - g(s[i - 1], "t")) }
    }

    fun referenceFile(track: String, carSafe: String) = File(lapsDir, "$track/reference_$carSafe.json")
    private fun pbFile(track: String) = File(lapsDir, "$track/personal_bests.json")

    private fun loadPbs(track: String): JSONObject = try { JSONObject(pbFile(track).readText()) } catch (_: Exception) { JSONObject() }

    private fun isNewPb(track: String, carSafe: String, t: Double): Boolean {
        val e = loadPbs(track).optJSONObject(carSafe) ?: return true
        var prev: Double? = if (e.has("lap_time_s")) e.getDouble("lap_time_s") else null
        if (prev != null && !TraceUtil.isCurrentEra(e.optString("physics_era", null), e.optString("recorded_at", null))) prev = null
        return prev == null || t < prev
    }

    fun saveLap(s: List<Map<String, Any?>>, lapTimeMs: Int?, incomplete: Boolean, ctx: Ctx) {
        val uiTrack = ctx.track.trim(); val uiCar = ctx.car.trim()
        val track = TraceUtil.sanitize(uiTrack)
        val car = uiCar.ifEmpty { "unknown" }
        val carSafe = TraceUtil.sanitize(car)
        val ts = ts()
        val gt7Time = lapTimeMs != null && lapTimeMs > 0
        val newTime = if (gt7Time) lapTimeMs!! / 1000.0 else max(0.0, mono() - startT)
        log("Lap time: ${TraceUtil.msToLapTime((newTime * 1000).toInt())}  (${if (gt7Time) "GT7" else "elapsed"}, ${s.size} samples${if (incomplete) "  INCOMPLETE" else ""})")
        val lapNum = (s.lastOrNull()?.get("lap_number") as? Number)?.toInt() ?: 0
        val fuelUsed = if (s.isEmpty()) 0.0 else ((s.first()["fuel_remaining"] as? Number)?.toDouble() ?: 0.0) - ((s.last()["fuel_remaining"] as? Number)?.toDouble() ?: 0.0)
        if (!incomplete && fuelUsed > 0) fuelPerLap = fuelUsed
        val data = JSONObject().apply {
            put("recorded_at", ts); put("track", track); put("car", car)
            put("track_display", uiTrack.ifEmpty { track }); put("car_display", uiCar.ifEmpty { car })
            put("lap_time_s", round(newTime * 1000) / 1000); put("lap_distance_m", round(lapDistance(s) * 10) / 10)
            put("total_samples", s.size); put("incomplete", incomplete); put("physics_era", TraceUtil.PHYSICS_ERA)
            put("gear_ratios", toJson(ctx.gearRatios)); put("samples", toJson(s))
        }
        val lapPath = File(lapsDir, "$track/${carSafe}_$ts.json")
        val isPb = !incomplete && gt7Time && isNewPb(track, carSafe, newTime)
        val saved = saveJson(lapPath, data, "lap")
        val meta = JSONObject(data.toString()).also { it.remove("samples"); it.put("fuel_used", round(max(0.0, fuelUsed) * 100) / 100) }
        if (saved == null) { log("Lap NOT saved."); lapsSaved.add(meta); return }
        meta.put("file", saved.path); lapsSaved.add(meta)
        log("Saved: laps/${saved.relativeTo(lapsDir).invariantSeparatorsPath}")
        onLapSaved(LapMeta(lapNum, newTime, track, car, incomplete, isPb, saved))
        if (incomplete || !gt7Time) return
        if (isPb) {
            val pbs = loadPbs(track)
            pbs.put(carSafe, JSONObject().put("car_display", uiCar.ifEmpty { car }).put("lap_time_s", newTime).put("recorded_at", ts).put("physics_era", TraceUtil.PHYSICS_ERA))
            saveJson(pbFile(track), pbs, "personal best")
            log("New personal best for ${uiCar.ifEmpty { car }} @ $track!")
        }
        val ref = referenceFile(track, carSafe)
        var refTime = 0.0
        if (ref.exists()) try {
            val rd = JSONObject(ref.readText())
            if (TraceUtil.isCurrentEra(rd.optString("physics_era", null), rd.optString("recorded_at", null))) refTime = rd.optDouble("lap_time_s", 0.0)
        } catch (_: Exception) { refTime = 0.0 }
        if (refTime <= 0 || newTime < refTime) {
            if (saveJson(ref, data, "reference lap") != null)
                log("Reference updated -> ${ref.name}${if (refTime > 0) "  (new best by ${"%.3f".format(Locale.ROOT, refTime - newTime)}s)" else "  (first lap)"}")
            refCache = null
        }
    }

    fun saveRace(s: List<Map<String, Any?>>, ctx: Ctx) {
        val uiTrack = ctx.track.trim(); val uiCar = ctx.car.trim()
        val track = TraceUtil.sanitize(uiTrack); val car = uiCar.ifEmpty { "unknown" }; val carSafe = TraceUtil.sanitize(car)
        val ts = ts()
        val pausedFor = if (paused && pausedAt != null) max(0.0, mono() - pausedAt!!) else 0.0
        val dur = max(0.0, mono() - raceStartT - pausedFor)
        val data = JSONObject().apply {
            put("recorded_at", ts); put("track", track); put("car", car)
            put("track_display", uiTrack.ifEmpty { track }); put("car_display", uiCar.ifEmpty { car })
            put("race_duration_s", round(dur * 1000) / 1000); put("physics_era", TraceUtil.PHYSICS_ERA)
            put("total_samples", s.size); put("gear_ratios", toJson(ctx.gearRatios)); put("samples", toJson(s))
        }
        val saved = saveJson(File(lapsDir, "$track/races/race_${carSafe}_$ts.json"), data, "race")
        if (saved != null) log("Race saved: laps/${saved.relativeTo(lapsDir).invariantSeparatorsPath}  (${s.size} samples, ${"%.1f".format(Locale.ROOT, dur)}s)")
        else log("Race NOT saved (${s.size} samples).")
        appendSessionSummary(data, s)
    }

    private fun appendSessionSummary(race: JSONObject, s: List<Map<String, Any?>>) {
        if (s.isEmpty()) return
        val laps = s.map { (it["lap_number"] as? Number)?.toInt() ?: 0 }.toSet()
        val fs = (s.first()["fuel_remaining"] as? Number)?.toDouble() ?: 0.0
        val fe = (s.last()["fuel_remaining"] as? Number)?.toDouble() ?: 0.0
        val grid = s.map { (it["current_position"] as? Number)?.toInt() ?: 0 }.firstOrNull { it > 0 }
        val cap = (s.last()["fuel_capacity"] as? Number)?.toDouble()
        sessionSummary.add(JSONObject().apply {
            put("recorded_at", race.getString("recorded_at")); put("track", race.getString("track")); put("car", race.getString("car"))
            put("duration_s", race.getDouble("race_duration_s")); put("laps", laps.size)
            put("fuel_used", round((fs - fe) * 100) / 100); put("fuel_unit", if (cap == 100.0 || cap == 0.0) "%" else "")
            put("grid_position", grid ?: JSONObject.NULL)
        })
        val o = JSONObject().put("session_started", sessionStartTs).put("races", JSONArray(sessionSummary.map { it }))
        saveJson(File(lapsDir, "_session/session_$sessionStartTs.json"), o, "session summary")
        log("Session summary updated (${sessionSummary.size} race(s) this session)")
    }

    // ── live delta vs reference lap (dashboard._get_reference_samples + bisect) ──
    private class RefCache(val key: Pair<String, String>, val ts: List<Double>, val tt: List<Double?>, val mtime: Long, var checked: Double)
    private var refCache: RefCache? = null

    /** Returns (delta seconds) or null. */
    fun liveDelta(track: String, carSafe: String, curElapsed: Double, curTrackPos: Double): Double? {
        val key = track to carSafe
        val now = mono()
        var c = refCache
        if (c == null || c.key != key || now - c.checked >= 2.0) {
            val f = referenceFile(track, carSafe)
            val mt = if (f.exists()) f.lastModified() else -1L
            if (c != null && c.key == key && c.mtime == mt) c.checked = now
            else {
                var pos = emptyList<Double>(); var tt = emptyList<Double?>()
                if (mt >= 0) try {
                    val rd = JSONObject(f.readText())
                    if (TraceUtil.isCurrentEra(rd.optString("physics_era", null), rd.optString("recorded_at", null))) {
                        val arr = rd.optJSONArray("samples") ?: JSONArray()
                        val l = (0 until arr.length()).map { arr.getJSONObject(it) }.sortedBy { it.optDouble("track_position", 0.0) }
                        pos = l.map { it.optDouble("track_position", 0.0) }; tt = l.map { if (it.has("t")) it.getDouble("t") else null }
                    }
                } catch (_: Exception) {}
                c = RefCache(key, pos, tt, mt, now); refCache = c
            }
        }
        val cc = c ?: return null
        if (cc.ts.isEmpty()) return null
        var j = cc.ts.binarySearch(curTrackPos).let { if (it >= 0) it else -it - 1 }
        val cands = listOf(j - 1, j).filter { it in cc.ts.indices }
        val best = cands.minByOrNull { Math.abs(cc.ts[it] - curTrackPos) } ?: return null
        val rt = cc.tt[best] ?: return null
        return curElapsed - rt
    }

    companion object { const val RACE_SAMPLE_WARN = 150_000; const val RACE_SAMPLE_MAX = 400_000 }
}
