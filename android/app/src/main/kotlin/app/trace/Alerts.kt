package app.trace

import android.Manifest
import android.app.NotificationChannel
import android.app.NotificationManager
import android.content.Context
import android.content.pm.PackageManager
import android.media.AudioManager
import android.media.ToneGenerator
import android.os.Build
import android.speech.tts.TextToSpeech
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.setValue
import androidx.core.app.NotificationCompat
import androidx.core.app.NotificationManagerCompat
import androidx.core.content.ContextCompat
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.InetAddress
import java.net.ServerSocket
import java.net.URL
import java.util.Locale
import kotlin.concurrent.thread

/** voice.AlertAnnouncer: turns per-frame alert flags into a few spoken lines. Pure -- feed it the time. */
class AlertAnnouncer(private val repeatS: Double = 60.0, private val clearS: Double = 5.0) {
    private val spokenAt = HashMap<String, Double>()
    private val offSince = HashMap<String, Double>()
    private val repeats = mapOf("fuel" to true, "hot" to true, "cold" to false)
    fun reset() { spokenAt.clear(); offSince.clear() }
    fun update(active: Set<String>, now: Double, fuelLaps: Double? = null): List<String> {
        val out = ArrayList<String>()
        for (kind in listOf("fuel", "hot", "cold")) {
            if (kind in active) {
                offSince.remove(kind)
                val last = spokenAt[kind]
                if (last == null || (repeats[kind] == true && now - last >= repeatS)) { spokenAt[kind] = now; out.add(message(kind, fuelLaps)) }
            } else if (kind in spokenAt) {
                val since = offSince.getOrPut(kind) { now }
                if (now - since >= clearS) { spokenAt.remove(kind); offSince.remove(kind) }
            }
        }
        return out
    }
    private fun message(kind: String, fuelLaps: Double?) = when {
        kind == "fuel" && fuelLaps != null && fuelLaps > 0 && fuelLaps < 99 -> "Fuel low. About ${"%.1f".format(Locale.ROOT, fuelLaps)} laps left"
        kind == "fuel" -> "Fuel low"
        kind == "hot" -> "Tyres overheating"
        else -> "Tyres cold"
    }
}

/** voice.py + notify.py: spoken alerts (Android TextToSpeech, alert sound if no voice) and phone notifications. */
class Alerts(private val ctx: Context) {
    private var tts: TextToSpeech? = null
    private var ttsReady = false
    private val chan = "trace_alerts"
    private var nid = 100

    init {
        if (Build.VERSION.SDK_INT >= 26) {
            val nm = ctx.getSystemService(NotificationManager::class.java)
            nm.createNotificationChannel(NotificationChannel(chan, "TRACE alerts", NotificationManager.IMPORTANCE_DEFAULT))
        }
    }

    fun voiceAvailable(): Boolean = ttsReady

    fun prepareVoice() {
        if (tts != null) return
        tts = TextToSpeech(ctx.applicationContext) { st ->
            ttsReady = st == TextToSpeech.SUCCESS
            if (ttsReady) tts?.language = Locale.getDefault()
        }
    }

    fun speak(text: String) {
        try {
            prepareVoice()
            if (ttsReady) tts?.speak(text, TextToSpeech.QUEUE_ADD, null, "trace-${System.nanoTime()}") else beep()
        } catch (_: Exception) { beep() }
    }

    fun beep() {
        try {
            val tg = ToneGenerator(AudioManager.STREAM_NOTIFICATION, 80)
            tg.startTone(ToneGenerator.TONE_PROP_BEEP2, 250)
            thread(isDaemon = true) { Thread.sleep(500); tg.release() }
        } catch (_: Exception) {}
    }

    /** Loud repeating siren on the alarm stream, for a low-battery warning nobody has acknowledged. */
    fun siren() {
        thread(isDaemon = true, name = "trace-siren") {
            try {
                val tg = ToneGenerator(AudioManager.STREAM_ALARM, 100)
                repeat(3) { tg.startTone(ToneGenerator.TONE_CDMA_ALERT_CALL_GUARD, 300); Thread.sleep(450) }
                tg.release()
            } catch (_: Exception) {}
        }
    }

    /** notify.notify: a phone notification plus the alert sound. Never throws. */
    fun notify(title: String, message: String) {
        beep()
        try {
            if (Build.VERSION.SDK_INT >= 33 && ContextCompat.checkSelfPermission(ctx, Manifest.permission.POST_NOTIFICATIONS) != PackageManager.PERMISSION_GRANTED) return
            val n = NotificationCompat.Builder(ctx, chan).setSmallIcon(android.R.drawable.ic_dialog_info)
                .setContentTitle(title).setContentText(message).setStyle(NotificationCompat.BigTextStyle().bigText(message))
                .setAutoCancel(true).build()
            NotificationManagerCompat.from(ctx).notify(nid++, n)
        } catch (_: Exception) {}
    }

    fun shutdown() { try { tts?.shutdown() } catch (_: Exception) {}; tts = null; ttsReady = false }
}

/** metrics_server.py: Prometheus text endpoint (gt7_* gauges) on a plain ServerSocket. Off by default. */
class MetricsServer {
    @Volatile private var server: ServerSocket? = null
    @Volatile private var snap: Map<String, Double> = emptyMap()
    @Volatile private var connected = 0.0
    val running: Boolean get() = server != null

    fun start(port: Int, bindAll: Boolean): Boolean {
        stop()
        return try {
            val ss = ServerSocket(port, 8, if (bindAll) InetAddress.getByName("0.0.0.0") else InetAddress.getByName("127.0.0.1"))
            server = ss
            thread(isDaemon = true, name = "trace-metrics") {
                while (!ss.isClosed) {
                    try {
                        ss.accept().use { s ->
                            s.soTimeout = 2000
                            val rd = s.getInputStream().bufferedReader()
                            while (true) { val l = rd.readLine() ?: break; if (l.isEmpty()) break }
                            val body = render().toByteArray()
                            val head = "HTTP/1.0 200 OK\r\nContent-Type: text/plain; version=0.0.4; charset=utf-8\r\nContent-Length: ${body.size}\r\n\r\n"
                            s.getOutputStream().apply { write(head.toByteArray()); write(body); flush() }
                        }
                    } catch (_: Exception) { if (ss.isClosed) break }
                }
            }
            true
        } catch (_: Exception) { server = null; false }
    }

    fun stop() {
        try { server?.close() } catch (_: Exception) {}
        server = null; snap = emptyMap(); connected = 0.0
    }

    fun update(d: Map<String, Any?>) {
        if (server == null) return
        snap = KEYS.associate { (k, _, _) -> k to ((d[k] as? Number)?.toDouble() ?: 0.0) }
        connected = 1.0
    }

    private fun render(): String {
        val sb = StringBuilder()
        for ((k, name, help) in KEYS) {
            sb.append("# HELP $name $help\n# TYPE $name gauge\n$name ${snap[k] ?: 0.0}\n")
        }
        sb.append("# HELP gt7_connected 1 while telemetry packets are arriving, else 0\n# TYPE gt7_connected gauge\ngt7_connected $connected\n")
        return sb.toString()
    }

    private companion object {
        val KEYS = listOf(
            Triple("speed_kmh", "gt7_speed_kmh", "Vehicle speed, km/h"),
            Triple("rpm", "gt7_rpm", "Engine RPM"),
            Triple("throttle", "gt7_throttle", "Throttle input, 0-1"),
            Triple("brake", "gt7_brake", "Brake input, 0-1"),
            Triple("fuel_remaining", "gt7_fuel_remaining", "Fuel remaining (percent or litres, depending on car)"),
            Triple("current_lap_ms", "gt7_current_lap_ms", "Current in-progress lap time, milliseconds"),
        )
    }
}

/** analytics.py: anonymous launch ping (event, tool, version, os, created_at) to the same app_events table the PC apps use. Never raises. */
object Analytics {
    fun track(tool: String, version: String) {
        thread(isDaemon = true, name = "trace-analytics") {
            try {
                val iso = java.text.SimpleDateFormat("yyyy-MM-dd'T'HH:mm:ss.SSS'Z'", Locale.ROOT).apply { timeZone = java.util.TimeZone.getTimeZone("UTC") }.format(java.util.Date())
                val body = JSONObject().put("event", "app_launch").put("tool", tool).put("version", version).put("os", "android").put("created_at", iso).toString()
                val r = Supa.call("POST", "/rest/v1/app_events", body, prefer = "return=minimal", timeoutMs = 4000)
                if (!r.ok) android.util.Log.d("TRACE", "analytics ${r.code}")
            } catch (_: Exception) {}
        }
    }
}

/** launcher._check_for_update: asks GitHub for the latest release tag, silently. */
object Updates {
    var latest by mutableStateOf<String?>(null)
    var url = "https://github.com/ransh2014/gt7telemtrace/releases/latest"
    private var done = false

    private fun ver(s: String) = s.trim().trimStart('v', 'V').split('.').map { it.takeWhile { c -> c.isDigit() }.toIntOrNull() ?: 0 }

    private fun newer(a: String, b: String): Boolean {
        val x = ver(a); val y = ver(b)
        for (i in 0 until maxOf(x.size, y.size)) { val p = x.getOrElse(i) { 0 }; val q = y.getOrElse(i) { 0 }; if (p != q) return p > q }
        return false
    }

    fun check(current: String) {
        if (done) return
        done = true
        thread(isDaemon = true, name = "trace-update") {
            try {
                val c = URL("https://api.github.com/repos/ransh2014/gt7telemtrace/releases/latest").openConnection() as HttpURLConnection
                c.connectTimeout = 6000; c.readTimeout = 6000
                c.setRequestProperty("Accept", "application/vnd.github+json"); c.setRequestProperty("User-Agent", "TRACE-updater")
                if (c.responseCode != 200) return@thread
                val o = JSONObject(c.inputStream.bufferedReader().readText())
                val tag = o.optString("tag_name").trimStart('v', 'V')
                if (tag.isNotEmpty() && newer(tag, current)) { url = o.optString("html_url").ifEmpty { url }; latest = tag }
            } catch (_: Exception) {}
        }
    }
}
