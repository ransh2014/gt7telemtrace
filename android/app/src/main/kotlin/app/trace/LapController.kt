package app.trace

import android.content.ContentResolver
import android.content.Context
import android.net.Uri
import androidx.compose.runtime.*
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.toArgb
import app.trace.charts.*
import app.trace.telemetry.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import org.json.JSONObject
import java.io.File

class Slot(val file: File?, val lap: LapFile, val tag: String? = null) {
    val meta: Meta get() = lap.meta
    val df: Frame get() = lap.frame
    val label: String get() = tag ?: LapMath.lapLabel(meta, true)
    val full: String get() = tag ?: LapMath.lapLabel(meta)
}

/** Lap Analyst state: lap A / B, overlay laps, the chart group and the saved-lap index. */
class LapController(val lapsDir: File) {
    var rows by mutableStateOf<List<LapIndex.Row>>(emptyList())
    var a by mutableStateOf<Slot?>(null)
    var b by mutableStateOf<Slot?>(null)
    val extras = mutableStateListOf<Pair<Slot, Int>>()
    var compare by mutableStateOf(true)
    var group by mutableStateOf("Inputs")
    var error by mutableStateOf<String?>(null)
    var busy by mutableStateOf(false)
    var epoch by mutableStateOf(0)
    var top10 by mutableStateOf<List<Leaderboard.Row>?>(null)
    var top10Busy by mutableStateOf(false)
    var lbMsg by mutableStateOf<String?>(null)
    var consensus by mutableStateOf<List<Map<String, Any?>>?>(null)
    private var top10Gen = 0

    private fun newA() { top10Gen++; top10 = null; consensus = null }

    suspend fun refresh() { rows = withContext(Dispatchers.IO) { LapIndex.scanPicker(lapsDir) } }

    private suspend fun loadFile(f: File): Slot? = withContext(Dispatchers.IO) {
        try { Slot(f, LapLoader.load(f)) } catch (e: Exception) { error = "${f.name}: ${e.message}"; null }
    }

    suspend fun setA(f: File) { busy = true; loadFile(f)?.let { a = it; newA(); epoch++ }; busy = false }
    suspend fun setB(f: File) { busy = true; loadFile(f)?.let { b = it; epoch++ }; busy = false }
    fun clearB() { b = null; epoch++ }

    suspend fun addExtra(f: File, colors: List<Color>) {
        if (extras.size >= 6) return
        busy = true
        loadFile(f)?.let { extras.add(it to colors[extras.size % colors.size].toArgbInt()); epoch++ }
        busy = false
    }
    fun clearExtras() { extras.clear(); epoch++ }

    private suspend fun fromUri(cr: ContentResolver, uri: Uri): Slot? = withContext(Dispatchers.IO) {
        try {
            val raw = cr.openInputStream(uri)!!.use { JsonColumns.read(it) }
            Slot(null, LapLoader.load(raw))
        } catch (e: Exception) { error = "Import failed: ${e.message}"; null }
    }
    suspend fun importA(cr: ContentResolver, uri: Uri) { busy = true; fromUri(cr, uri)?.let { a = it; newA(); epoch++ }; busy = false }
    suspend fun importB(cr: ContentResolver, uri: Uri) { busy = true; fromUri(cr, uri)?.let { b = it; epoch++ }; busy = false }

    /** Same car / track / era saved laps, fastest first (theory_candidates), loaded for the Theoretical group. */
    suspend fun theoryPool(): List<Pair<String, Frame>> = withContext(Dispatchers.IO) {
        val s = a ?: return@withContext emptyList()
        val all = LapIndex.scanAll(lapsDir)
        LapIndex.theoryCandidates(all, s.meta, s.file, 10).mapNotNull { r ->
            try { (r.recordedAt.take(8) + " " + LapMath.fmtTime(r.timeS)) to LapLoader.load(r.path).frame } catch (e: Exception) { null }
        }
    }

    // ── leaderboard ──
    fun carTrack(s: Slot): Pair<String, String> {
        fun pick(vararg k: String): String? { for (key in k) { val v = s.meta[key]?.toString(); if (!v.isNullOrEmpty()) return v }; return null }
        return (pick("car_display", "car") ?: "?") to (pick("track_display", "track") ?: "?")
    }

    suspend fun refreshTop10() {
        val s = a ?: return
        val (car, track) = carTrack(s)
        val gen = ++top10Gen
        top10Busy = true
        val rows = withContext(Dispatchers.IO) { Leaderboard.topLaps(car, track) }
        if (gen == top10Gen) { top10 = rows; top10Busy = false }
    }

    suspend fun loadConsensus() {
        val s = a ?: run { lbMsg = "Load Lap A first."; return }
        val (car, track) = carTrack(s)
        busy = true
        val rows = withContext(Dispatchers.IO) { Leaderboard.consensus(car, track) }
        busy = false
        if (a !== s) return
        if (rows.isEmpty()) lbMsg = "No community data yet for this car+track (or couldn't reach the server)."
        else { consensus = rows; epoch++; lbMsg = "Community line loaded -- see the Consensus chart." }
    }

    suspend fun downloadGhost(row: Leaderboard.Row) {
        busy = true
        val lap = withContext(Dispatchers.IO) { try { Leaderboard.ghost(row) } catch (e: Exception) { null } }
        busy = false
        if (lap == null) { lbMsg = "Couldn't download that lap -- check your internet connection and try again."; return }
        b = Slot(null, lap, "${row.psn.ifEmpty { "?" }} (ghost) -- " + LapMath.lapLabel(lap.meta, true))
        epoch++
        lbMsg = "Ghost loaded as Lap B."
    }

    /** submit_leaderboard: refresh the token once on 401/403 and retry; returns the message to show. */
    suspend fun submit(ctx: Context, psn: String): String {
        val s = a ?: return "Load Lap A first."
        val (car, track) = carTrack(s)
        val ms = Math.rint(numOf(s.meta["lap_time_s"]) * 1000).toLong()
        val name = psn.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ").take(32)
        if (name.isEmpty()) return "Enter a name first."
        Account.setPsn(ctx, name)
        val acc = Account.load(ctx)
        busy = true
        val (ok, reason) = withContext(Dispatchers.IO) {
            var res = Leaderboard.submit(car, track, ms, name, s.df, acc.access, acc.userId)
            if (!res.first && res.second == "auth") {
                val sess = SupaAuth.refresh(acc.refresh)
                res = if (sess != null) {
                    Account.saveSession(ctx, sess)
                    Leaderboard.submit(car, track, ms, name, s.df, sess.access, sess.userId.ifEmpty { acc.userId })
                } else false to (if (SupaAuth.lastRefreshError == "invalid") "session" else "network")
                if (res.second == "auth") res = false to "server"
            }
            res
        }
        busy = false
        return when {
            ok -> { refreshTop10(); "Submitted! If it's a new top time it'll appear on the leaderboard shortly." }
            reason == "session" -> { Account.clear(ctx); "Your leaderboard account on this phone is no longer valid. Create a new free account (just a display name) and submit again." }
            reason == "data" -> "This lap's data couldn't be prepared for upload (is the file damaged?)."
            reason == "server" -> "The server rejected the submission -- this is a server-side issue, not your connection. Try again later."
            else -> "Couldn't reach the leaderboard -- check your internet connection and try again."
        }
    }

    fun extrasFor(): List<Extra> = extras.map { Extra(it.first.label, it.first.df, Color(it.second)) }

    /** Lap notes live in the lap file itself ("notes"); written atomically. */
    suspend fun saveNotes(text: String): Boolean = withContext(Dispatchers.IO) {
        val f = a?.file ?: return@withContext false
        try {
            val j = JSONObject(f.readText())
            j.put("notes", text)
            val tmp = File(f.parentFile, f.name + ".tmp")
            tmp.writeText(j.toString())
            if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
            true
        } catch (e: Exception) { error = "Notes: ${e.message}"; false }
    }
}

private fun Color.toArgbInt(): Int = this.toArgb()
