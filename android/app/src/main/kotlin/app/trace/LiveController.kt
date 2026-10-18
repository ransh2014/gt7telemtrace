package app.trace

import android.content.Context
import android.content.Intent
import android.content.IntentFilter
import android.os.BatteryManager
import android.os.PowerManager
import androidx.compose.runtime.*
import app.trace.telemetry.*
import java.io.File
import java.text.SimpleDateFormat
import java.util.Date
import java.util.Locale
import kotlin.concurrent.thread
import kotlin.math.abs
import kotlin.math.sqrt

/** Holds everything the Live Dashboard (dashboard.App) keeps: telemetry, recorder, strategy, events, settings. */
class LiveController(private val appCtx: Context) {
    private val prefs = appCtx.getSharedPreferences("trace", Context.MODE_PRIVATE)
    private val lock = Any()
    val db = NameDb(read("car_ids.csv"), read("course_ids.csv"))
    val lapsDir = File(appCtx.getExternalFilesDir(null) ?: appCtx.filesDir, "laps")

    val log = mutableStateListOf<String>()
    var ip by mutableStateOf(prefs.getString("ip", "192.168.1.1") ?: "192.168.1.1")
    var track by mutableStateOf(prefs.getString("track", "") ?: "")
    var car by mutableStateOf("")
    var rate by mutableStateOf(prefs.getInt("rate", 10))
    var d by mutableStateOf<Packet>(emptyMap())
    var online by mutableStateOf(false)
    var strat by mutableStateOf(StintTracker().snapshot())
    var curElapsed by mutableStateOf(0.0)
    var delta by mutableStateOf<Double?>(null)
    var trackPts by mutableStateOf<List<Pair<Double, Double>>>(emptyList())
    var flashOn by mutableStateOf(true)
    var recLapState by mutableStateOf(0)       // 0 idle, 1 waiting for start line, 2 recording
    var raceRec by mutableStateOf(false)
    val history = mutableStateListOf<Recorder.LapMeta>()
    var summaryTick by mutableStateOf(0)
    var voiceOn by mutableStateOf(prefs.getBoolean("voice", false))
    var notifyOn by mutableStateOf(prefs.getBoolean("notify", true))
    var metricsOn by mutableStateOf(prefs.getBoolean("metrics", false))
    var remoteOn by mutableStateOf(prefs.getBoolean("remote", false))
    var updateOn by mutableStateOf(prefs.getBoolean("update", true))
    var analyticsOn by mutableStateOf(prefs.getBoolean("analytics", true))
    var debugOn by mutableStateOf(prefs.getBoolean("debug", false))
    var keepAwake by mutableStateOf(prefs.getBoolean("keep_awake", true))
    var battWarn by mutableStateOf<Int?>(null)       // charge % shown on the red low-battery bar; null = hidden
    val alerts = Alerts(appCtx)
    private val battery = BatteryGuard()
    private var battNext = 0.0
    private var wake: PowerManager.WakeLock? = null
    private val announcer = AlertAnnouncer()
    private val metrics = MetricsServer()
    private var lastDebug = 0.0
    private fun say(m: String) { log.add("[${SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(Date())}] $m") }
    private fun pref(k: String, v: Boolean) = prefs.edit().putBoolean(k, v).apply()
    fun onVoice(v: Boolean) {
        voiceOn = v; pref("voice", v)
        if (v) { alerts.prepareVoice(); announcer.reset(); say("Voice alerts on -- fuel and tyre alerts will be spoken") } else say("Voice alerts muted")
    }
    fun onKeepAwake(v: Boolean) {
        keepAwake = v; pref("keep_awake", v); applyWake()
        say(if (v) "Keep awake on -- the phone won't sleep while TRACE is open" else "Keep awake off -- the screen stays on only while telemetry is flowing")
    }
    /** CPU wake lock for as long as TRACE is open (the screen flag is set by MainActivity). Released on off and on stop. */
    private fun applyWake() {
        try {
            if (!keepAwake) { releaseWake(); return }
            if (wake == null) {
                val pm = appCtx.getSystemService(Context.POWER_SERVICE) as PowerManager
                wake = pm.newWakeLock(PowerManager.PARTIAL_WAKE_LOCK, "trace:telemetry").apply { setReferenceCounted(false); acquire() }
            }
        } catch (_: Exception) {}
    }
    private fun releaseWake() { try { wake?.let { if (it.isHeld) it.release() } } catch (_: Exception) {}; wake = null }

    private fun readBattery(): Pair<Int, Boolean>? = try {
        val i = appCtx.registerReceiver(null, IntentFilter(Intent.ACTION_BATTERY_CHANGED))
        val level = i?.getIntExtra(BatteryManager.EXTRA_LEVEL, -1) ?: -1
        val scale = i?.getIntExtra(BatteryManager.EXTRA_SCALE, -1) ?: -1
        if (i == null || level < 0 || scale <= 0) null else (level * 100 / scale) to (i.getIntExtra(BatteryManager.EXTRA_PLUGGED, 0) != 0)
    } catch (_: Exception) { null }

    /** dashboard._battery_step: warn (bar, then siren if ignored) while a lap or race is recording on a low battery. */
    private fun batteryStep(now: Double) {
        if (now < battNext) return
        battNext = now + BatteryGuard.CHECK_S
        val b = readBattery()
        val active = recorder.recording || recorder.waiting || recorder.raceRecording
        val st = battery.step(b?.first, b?.second ?: true, active, now)
        battWarn = if (st.show) b?.first else null
        val pct = b?.first ?: 0
        for (e in st.events) when (e) {
            BatteryGuard.Event.WARN -> { say("WARNING: battery at $pct% and not charging -- plug in so the recording isn't lost"); alerts.notify("Low battery", "TRACE is recording and the battery is at $pct% -- plug in") }
            BatteryGuard.Event.CRITICAL -> say("CRITICAL: battery at $pct% -- charge NOW")
            BatteryGuard.Event.ALARM -> alerts.siren()
        }
    }
    fun onBatteryAck() { battery.ack(); battWarn = null }

    fun onNotify(v: Boolean) { notifyOn = v; pref("notify", v); say(if (v) "Notifications on" else "Notifications off") }
    private fun startMetrics() {
        if (!metrics.start(9109, remoteOn)) { say("Metrics export: failed to bind port 9109 (already in use?)"); metricsOn = false; pref("metrics", false) }
        else say("Metrics export on -- http://${if (remoteOn) "<phone-ip>" else "127.0.0.1"}:9109/metrics")
    }
    fun onMetrics(v: Boolean) { metricsOn = v; pref("metrics", v); if (v) startMetrics() else { metrics.stop(); say("Metrics export off") } }
    fun onRemote(v: Boolean) { remoteOn = v; pref("remote", v); if (metricsOn) startMetrics() }
    fun onUpdate(v: Boolean) { updateOn = v; pref("update", v); say("Update check ${if (v) "on" else "off"} (it only asks GitHub for the latest release number)"); if (v) Updates.check(versionName()) }
    fun onAnalytics(v: Boolean) { analyticsOn = v; pref("analytics", v); say("Anonymous usage analytics ${if (v) "on" else "off"} (5 fields on tool launch, no telemetry content)") }
    fun onDebug(v: Boolean) { debugOn = v; pref("debug", v); say("Debug log ${if (v) "enabled" else "disabled"}") }
    fun versionName(): String = try { appCtx.packageManager.getPackageInfo(appCtx.packageName, 0).versionName ?: "0" } catch (_: Exception) { "0" }
    var ghostEnabled by mutableStateOf(prefs.getBoolean("ghost", true))
    var ghostGap by mutableStateOf<Double?>(null)
    private val ghosts = HashMap<Pair<String, String>, Ghost?>()
    private val ghostFetching = HashSet<Pair<String, String>>()
    private var ghostKey: Pair<String, String>? = null
    private var ghostSince = 0.0
    fun onGhostToggle(v: Boolean) {
        ghostEnabled = v; prefs.edit().putBoolean("ghost", v).apply(); ghostGap = null
        log.add(if (v) "Top-lap ghost on -- fetches the leaderboard's fastest lap for your car and track" else "Top-lap ghost off -- no leaderboard requests from the Dashboard")
    }

    private val stint = StintTracker()
    private val events = EventDetector()
    private var stratCarId: Int? = null
    private var lastAutoCarId: Int? = null
    private var fuelPerLapFallback = 0.0
    private var deltaPrevLap = -1
    private var deltaLapStartT: Double? = null
    private var flashTick = 0
    private var wasConnected = false
    private var running = false

    val client = TelemetryClient(ip) { p -> synchronized(lock) { events.check(p) } }
    val recorder = Recorder(lapsDir, { msg -> post { log.add("[${SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(Date())}] $msg"); if (log.size > 500) log.removeAt(0) } },
        onLapSaved = { m ->
            post { history.add(0, m) }
            if (m.isPb && !m.incomplete && notifyOn) {
                val mm = (m.lapTimeS / 60).toInt(); val ss = m.lapTimeS - mm * 60
                alerts.notify("New Personal Best", "${m.car} @ ${m.track}\n$mm:${"%06.3f".format(Locale.ROOT, ss)}")
            }
        })

    private fun post(f: () -> Unit) = f()
    private fun read(n: String) = appCtx.assets.open(n).bufferedReader().readText()
    private fun ctx() = Recorder.Ctx(track, car, ((d["gear_ratios"] as? List<*>)?.map { (it as? Number)?.toDouble() ?: 0.0 }) ?: emptyList())

    init {
        recorder.recordRate = rate
        if (metricsOn) startMetrics()
        if (voiceOn) alerts.prepareVoice()
        if (updateOn) Updates.check(versionName())
        events.register("race_start") { recorder.onRaceStart(); raceRec = recorder.raceRecording }
        events.register("race_end") { recorder.onRaceEnd(ctx()); raceRec = recorder.raceRecording; summaryTick++; if (notifyOn) alerts.notify("Race Ended", "Recording saved.") }
        events.register("pause") { recorder.onPause() }
        events.register("resume") { recorder.onResume { gap -> deltaLapStartT = deltaLapStartT?.plus(gap) } }
    }

    fun start() {
        if (running) return
        running = true
        applyWake()
        client.start()
        log.add("[${SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(Date())}] Heartbeat -> $ip:${TelemetryClient.SEND_PORT}  |  Listening on :${TelemetryClient.RECV_PORT}")
        thread(isDaemon = true, name = "trace-poll") { while (running) { try { poll() } catch (_: Exception) {}; Thread.sleep(50) } }
        thread(isDaemon = true, name = "trace-rec") { while (running) { try { synchronized(lock) { recorder.tick(client.latest, client.isConnected(), ctx()) }; recLapState = if (recorder.recording) 2 else if (recorder.waiting) 1 else 0; raceRec = recorder.raceRecording } catch (_: Exception) {}; Thread.sleep(16) } }
    }

    fun stop() { running = false; releaseWake(); client.stop() }

    fun onIpChange(force: Boolean = false) { val v = ip.trim(); client.ip = v; prefs.edit().putString("ip", v).apply() }
    fun onRate(r: Int) { rate = r; recorder.recordRate = r; prefs.edit().putInt("rate", r).apply() }
    fun onTrack(t: String) { track = t; client.decoder.track = t.ifBlank { "unknown_track" }; prefs.edit().putString("track", t).apply() }
    fun onCar(c: String) { car = c; client.decoder.car = c.ifBlank { "unknown_car" } }
    var promptTick by mutableStateOf(0)
    fun resolveIncomplete(save: Boolean) { synchronized(lock) { recorder.resolvePending(save) }; promptTick++ }
    fun toggleLap() { synchronized(lock) { recorder.toggleLap((d["lap_number"] as? Number)?.toInt() ?: 0, ctx()) }; recLapState = if (recorder.recording) 2 else if (recorder.waiting) 1 else 0; promptTick++ }
    fun toggleRace() { synchronized(lock) { if (recorder.raceRecording) { recorder.stopRace(false, ctx()); summaryTick++; if (notifyOn) alerts.notify("Race Ended", "Recording saved.") } else recorder.startRace() }; raceRec = recorder.raceRecording }
    fun newTyres() { stint.resetStint(); log.add("Strategy: new tyre stint started") }
    fun discover(done: (String?) -> Unit) { thread { val f = TelemetryClient.discoverPsIp(); if (f != null) { ip = f; onIpChange() }; done(f) } }

    private fun poll() {
        batteryStep(System.nanoTime() / 1e9)
        val p = client.latest; val ok = client.isConnected()
        online = ok
        if (!ok) {
            if (wasConnected) log.add("[${SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(Date())}] Connection dropped: no packets received")
            wasConnected = false; return
        }
        if (!wasConnected) log.add("[${SimpleDateFormat("HH:mm:ss", Locale.ROOT).format(Date())}] Connected")
        wasConnected = true
        d = p
        fun n(k: String) = (p[k] as? Number)?.toDouble() ?: 0.0
        val curLap = n("lap_number").toInt(); val totLap = n("total_laps").toInt()
        val fuelR = n("fuel_remaining"); val lastMs = (p["last_lap_ms"] as? Number)?.toInt() ?: -1

        // car change -> reset strategy; autofill CAR box
        val carId = n("car_id").toInt()
        if (carId != 0 && carId != stratCarId) { if (stratCarId != null) stint.resetAll(); stratCarId = carId }
        if (carId > 0 && !(carId == lastAutoCarId && car.isNotBlank())) {
            car = db.carName(carId).ifEmpty { "Unknown car (ID $carId)" }; lastAutoCarId = carId; client.decoder.car = car
        }
        stint.update(curLap, totLap, fuelR, lastMs, listOf(n("tyre_temp_fl"), n("tyre_temp_fr"), n("tyre_temp_rl"), n("tyre_temp_rr")), n("speed_kmh"))
        strat = stint.snapshot()
        fuelPerLapFallback = recorder.fuelPerLap

        flashTick = (flashTick + 1) % 4; flashOn = flashTick < 2

        // track map
        val wx = n("world_x"); val wz = n("world_z")
        if (wx != 0.0 || wz != 0.0) {
            var pts = trackPts
            if (pts.isNotEmpty()) { val (lx, lz) = pts.last(); if (sqrt((wx - lx) * (wx - lx) + (wz - lz) * (wz - lz)) > 500) { pts = emptyList(); log.add("Track map reset (position jump)") } }
            if (pts.isEmpty() || abs(wx - pts.last().first) > 0.5 || abs(wz - pts.last().second) > 0.5) trackPts = pts + (wx to wz)
            else trackPts = pts
        }

        // live delta
        val now = System.nanoTime() / 1e9
        if (curLap != deltaPrevLap) { deltaLapStartT = now; deltaPrevLap = curLap }
        val trackPos = n("track_position")
        val curLapMs = (p["current_lap_ms"] as? Number)?.toInt()
        val inLap = TraceUtil.lapClockRunning(curLap, totLap, recorder.paused, p["car_on_track"] as? Boolean ?: true, p["loading"] as? Boolean)
        curElapsed = when {
            !inLap -> { deltaLapStartT = null; 0.0 }
            curLapMs != null && curLapMs > 0 -> curLapMs / 1000.0
            deltaLapStartT != null -> now - deltaLapStartT!!
            else -> 0.0
        }
        delta = if (track.isNotBlank() && curElapsed > 0.5 && trackPos > 0)
            recorder.liveDelta(TraceUtil.sanitize(track.trim()), TraceUtil.sanitize(car.trim().ifEmpty { "unknown" }), curElapsed, trackPos) else null

        // metrics, spoken alerts, debug log
        metrics.update(p)
        if (voiceOn && !recorder.paused) {
            val fpl = recorder.fuelPerLap
            val fuelLaps = strat.lapsOfFuel ?: if (fuelR > 0 && fpl > 0) fuelR / fpl else null
            val temps = listOf("tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr").map { n(it) }.filter { it > 0 }
            val active = HashSet<String>()
            if (fuelLaps != null && fuelLaps < 2) active.add("fuel")
            if (temps.any { it > 100 }) active.add("hot")
            if (temps.any { it < 60 }) active.add("cold")
            for (line in announcer.update(active, now, fuelLaps)) alerts.speak(line)
        }
        if (debugOn && now - lastDebug >= 2.0) {
            lastDebug = now
            say("[DEBUG] on_track=${p["car_on_track"]} paused=${recorder.paused} speed=${n("speed_kmh")} lap=$curLap/$totLap")
        }

        // live gap to the leaderboard's fastest lap
        val key = if (car.trim().isNotEmpty() && track.trim().isNotEmpty()) car.trim() to track.trim() else null
        if (key != null && ghostEnabled && key !in ghostFetching) {
            if (ghostKey != key) { ghostKey = key; ghostSince = now }
            else if (now - ghostSince >= 3.0) {
                ghostFetching.add(key)
                thread(isDaemon = true, name = "trace-ghost") {
                    var g: Ghost? = null
                    try {
                        Leaderboard.topGhost(key.first, key.second)?.let { (row, lap) ->
                            g = Ghost.build(lap.frame, row.ms.toInt())
                            if (g != null) log.add("Top-lap ghost loaded: ${TraceUtil.msToLapTime(row.ms.toInt())}" + (if (row.psn.isNotBlank()) " by ${row.psn}" else ""))
                        }
                    } catch (_: Exception) { g = null }
                    synchronized(ghosts) { ghosts[key] = g }
                }
            }
        }
        val gh = if (ghostEnabled && key != null) synchronized(ghosts) { ghosts[key] } else null
        ghostGap = if (gh != null && curElapsed > 0.5) gh.gap(trackPos, curElapsed) else null
    }

    fun exportSession(): File? {
        val laps = org.json.JSONArray()
        for (m in recorder.lapsSaved) {
            val f = m.optString("file", "")
            laps.put(try { org.json.JSONObject(File(f).readText()) } catch (_: Exception) { m })
        }
        if (laps.length() == 0) { log.add("No laps recorded this session."); return null }
        val out = File(lapsDir.parentFile, "gt7_session_${SimpleDateFormat("yyyyMMdd_HHmmss", Locale.ROOT).format(Date())}.json")
        out.writeText(org.json.JSONObject().put("exported_at", SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss", Locale.ROOT).format(Date())).put("laps", laps).toString())
        log.add("Session exported: ${out.name}")
        return out
    }
}
