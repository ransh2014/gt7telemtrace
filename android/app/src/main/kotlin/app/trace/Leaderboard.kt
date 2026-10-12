package app.trace

import android.content.Context
import app.trace.telemetry.*
import org.json.JSONArray
import org.json.JSONObject
import java.net.HttpURLConnection
import java.net.URL
import java.net.URLEncoder

/** Tiny Supabase REST client (leaderboard.py / auth.py use urllib the same way). HTTP errors come back as a Resp; only transport failures throw. */
object Supa {
    const val BASE = "https://hignsvyojdqsjoidgkud.supabase.co"
    const val ANON = "sb_publishable_OdzGvcypa0GI7TVxxUkusQ_KJ_Apa8i"
    class Resp(val code: Int, val body: String) { val ok get() = code in 200..299 }

    fun call(method: String, path: String, body: String? = null, bearer: String? = ANON, prefer: String? = null, timeoutMs: Int = 8000): Resp {
        val c = URL(BASE + path).openConnection() as HttpURLConnection
        try {
            c.requestMethod = method
            c.connectTimeout = timeoutMs; c.readTimeout = timeoutMs
            c.setRequestProperty("apikey", ANON)
            if (bearer != null) c.setRequestProperty("Authorization", "Bearer $bearer")
            c.setRequestProperty("Content-Type", "application/json")
            if (prefer != null) c.setRequestProperty("Prefer", prefer)
            if (body != null) { c.doOutput = true; c.outputStream.use { it.write(body.toByteArray(Charsets.UTF_8)) } }
            val code = c.responseCode
            val stream = if (code < 400) c.inputStream else c.errorStream
            return Resp(code, stream?.bufferedReader(Charsets.UTF_8)?.use { it.readText() } ?: "")
        } finally { c.disconnect() }
    }
}

class Session(val access: String, val refresh: String, val userId: String)

/** auth.py: anonymous sign-up (display name only, no email / password) + token refresh. */
object SupaAuth {
    @Volatile var lastRefreshError: String? = null

    private fun parse(body: String): Session {
        val j = JSONObject(body)
        return Session(j.optString("access_token"), j.optString("refresh_token"), j.optJSONObject("user")?.optString("id") ?: "")
    }

    fun signUpAnonymous(): Session? = try {
        val r = Supa.call("POST", "/auth/v1/signup", "{}")
        if (!r.ok) null else parse(r.body).takeIf { it.access.isNotEmpty() && it.userId.isNotEmpty() }
    } catch (e: Exception) { null }

    private fun rejected(r: Supa.Resp): Boolean {
        if (r.code != 400 && r.code != 401) return false
        val b = r.body.lowercase()
        return listOf("invalid_grant", "refresh_token_not_found", "refresh_token_already_used", "invalid refresh token",
            "refresh token not found", "already used", "session_not_found").any { it in b }
    }

    fun refresh(token: String): Session? {
        if (token.isEmpty()) { lastRefreshError = "invalid"; return null }
        return try {
            val r = Supa.call("POST", "/auth/v1/token?grant_type=refresh_token", JSONObject().put("refresh_token", token).toString(), bearer = null)
            if (r.ok) {
                val s = parse(r.body)
                if (s.access.isEmpty()) { lastRefreshError = "network"; null }
                else { lastRefreshError = null; if (s.refresh.isEmpty()) Session(s.access, token, s.userId) else s }
            } else { lastRefreshError = if (rejected(r)) "invalid" else "network"; null }
        } catch (e: Exception) { lastRefreshError = "network"; null }
    }

    fun setDisplayName(access: String, uid: String, name: String): Boolean {
        if (access.isEmpty() || uid.isEmpty()) return false
        return try {
            val r = Supa.call("PATCH", "/rest/v1/profiles?id=eq." + URLEncoder.encode(uid, "UTF-8"),
                JSONObject().put("display_name", name).toString(), bearer = access, prefer = "return=representation")
            if (!r.ok) false else {
                val a = JSONArray(r.body.ifBlank { "[]" })
                a.length() > 0 && a.getJSONObject(0).optString("display_name") == name
            }
        } catch (e: Exception) { false }
    }
}

/** The signed-in leaderboard account lives in app-private preferences (runtime_config.SUPABASE_* on the PC). */
object Account {
    class State(val access: String, val refresh: String, val userId: String, val psn: String) { val signedIn get() = access.isNotEmpty() }
    private fun p(ctx: Context) = ctx.getSharedPreferences("trace_account", Context.MODE_PRIVATE)
    fun load(ctx: Context): State = p(ctx).let { State(it.getString("access", "") ?: "", it.getString("refresh", "") ?: "", it.getString("user_id", "") ?: "", it.getString("psn", "") ?: "") }
    fun psn(ctx: Context): String = load(ctx).psn
    fun setPsn(ctx: Context, name: String) { p(ctx).edit().putString("psn", name).apply() }
    fun saveSession(ctx: Context, s: Session) {
        val old = p(ctx).getString("user_id", "") ?: ""
        p(ctx).edit().putString("access", s.access).putString("refresh", s.refresh).putString("user_id", s.userId.ifEmpty { old }).apply()
    }
    fun clear(ctx: Context) { p(ctx).edit().remove("access").remove("refresh").remove("user_id").apply() }
}

/** leaderboard.py: top laps, ghost download, consensus line, lap submission. */
object Leaderboard {
    /** Bump with each GT7 physics change (same date the SQL functions and leaderboard.html use). */
    const val PHYSICS_EPOCH_ISO = "2026-08-20T00:00:00Z"

    class Row(val id: Long, val car: String, val track: String, val ms: Long, val psn: String, val createdAt: String)

    private fun eq(v: String) = "eq.\"" + v.replace("\\", "\\\\").replace("\"", "\\\"") + "\""
    private fun q(vararg kv: Pair<String, String>) = kv.joinToString("&") { URLEncoder.encode(it.first, "UTF-8") + "=" + URLEncoder.encode(it.second, "UTF-8") }

    /** lap_submission_error */
    fun submissionError(meta: Meta, df: Frame): String? {
        if (meta.truthy("incomplete"))
            return "This lap is incomplete -- it was stopped before the line, so its time only covers part of a lap. Only complete laps can be submitted."
        if (df.n == 0) return "This lap has no samples to submit."
        if (!LapMath.isCurrentEra(meta))
            return "This lap was recorded before GT7 update ${TraceUtil.PHYSICS_ERA}, which changed the tyre, suspension and engine physics -- its time isn't comparable with laps driven since, so it can't go on the leaderboard. Drive a fresh lap to submit."
        if (numOf(meta["lap_time_s"]) <= 0) return "This lap has no valid lap time to submit."
        return null
    }

    private fun rows(body: String): List<Row> {
        val a = JSONArray(body)
        return (0 until a.length()).mapNotNull { i ->
            a.optJSONObject(i)?.let { o -> Row(o.optLong("id", -1), o.optString("car_name"), o.optString("track_name"), o.optLong("lap_time_ms"), o.optString("psn_name"), o.optString("created_at")) }
        }
    }

    fun topLaps(car: String, track: String, n: Int = 10): List<Row> = try {
        val r = Supa.call("GET", "/rest/v1/laps?" + q("car_name" to eq(car), "track_name" to eq(track), "created_at" to "gte.$PHYSICS_EPOCH_ISO",
            "select" to "id,car_name,track_name,lap_time_ms,psn_name,created_at", "order" to "lap_time_ms.asc", "limit" to "$n"))
        if (r.ok) rows(r.body) else emptyList()
    } catch (e: Exception) { emptyList() }

    private fun ghostText(row: Row, samples: String) =
        "{\"car\":${JSONObject.quote(row.car)},\"track\":${JSONObject.quote(row.track)},\"lap_time_s\":${row.ms / 1000.0},\"samples\":$samples}"

    /** One leaderboard lap's compact samples, loaded like any lap (no GPS map / replay for a ghost). Null on any failure. */
    fun ghost(row: Row): LapFile? {
        return try {
            val r = Supa.call("GET", "/rest/v1/laps?" + q("id" to "eq.${row.id}", "select" to "samples", "limit" to "1"))
            if (!r.ok) return null
            val s = JSONArray(r.body).optJSONObject(0)?.optJSONArray("samples") ?: return null
            if (s.length() == 0) null else LapLoader.loadText(ghostText(row, s.toString()))
        } catch (e: Exception) { null }
    }

    /** The single fastest public lap with samples (the Dashboard's live "vs top lap" gap). */
    fun topGhost(car: String, track: String): Pair<Row, LapFile>? {
        return try {
            val r = Supa.call("GET", "/rest/v1/laps?" + q("car_name" to eq(car), "track_name" to eq(track), "created_at" to "gte.$PHYSICS_EPOCH_ISO",
                "select" to "id,car_name,track_name,lap_time_ms,psn_name,created_at,samples", "order" to "lap_time_ms.asc", "limit" to "1"), timeoutMs = 15000)
            if (!r.ok) return null
            val o = JSONArray(r.body).optJSONObject(0) ?: return null
            val s = o.optJSONArray("samples") ?: return null
            if (s.length() == 0) return null
            val row = Row(o.optLong("id", -1), o.optString("car_name"), o.optString("track_name"), o.optLong("lap_time_ms"), o.optString("psn_name"), o.optString("created_at"))
            row to LapLoader.loadText(ghostText(row, s.toString()))
        } catch (e: Exception) { null }
    }

    fun consensus(car: String, track: String, n: Int = 10): List<Map<String, Any?>> = try {
        val body = JSONObject().put("p_car_name", car).put("p_track_name", track).put("p_n", n).toString()
        val r = Supa.call("POST", "/rest/v1/rpc/get_consensus_line", body)
        if (!r.ok) emptyList() else {
            val a = JSONArray(r.body)
            (0 until a.length()).mapNotNull { a.optJSONObject(it) }.map { o ->
                o.keys().asSequence().associateWith { k -> o.opt(k).let { v -> if (v is Number) v.toDouble() else v } }
            }
        }
    } catch (e: Exception) { emptyList() }

    private fun num(d: Double) = if (d.isFinite()) d.toString() else "0"

    /** submit_lap -> (ok, reason): reason is "ok" | "network" | "auth" | "server" | "data". */
    fun submit(car: String, track: String, ms: Long, psn: String, df: Frame, access: String, userId: String): Pair<Boolean, String> {
        val body = try {
            val tp = df["track_position"]; val sp = df["speed_kmh"]; val th = df["throttle"]; val br = df["brake"]; val st = df["steering"]; val gr = df["gear"]
            val sb = StringBuilder(df.n * 120 + 256)
            sb.append("{\"car_name\":").append(JSONObject.quote(car)).append(",\"track_name\":").append(JSONObject.quote(track))
                .append(",\"lap_time_ms\":").append(ms).append(",\"psn_name\":").append(JSONObject.quote(psn)).append(",\"samples\":[")
            for (i in 0 until df.n) {
                if (i > 0) sb.append(',')
                sb.append("{\"track_position\":").append(num(tp[i])).append(",\"speed_kmh\":").append(num(sp[i]))
                    .append(",\"throttle\":").append(num(th[i])).append(",\"brake\":").append(num(br[i]))
                    .append(",\"steering\":").append(num(st[i])).append(",\"gear\":").append(num(gr[i])).append('}')
            }
            sb.append(']')
            if (userId.isNotEmpty()) sb.append(",\"user_id\":").append(JSONObject.quote(userId))
            sb.append('}').toString()
        } catch (e: Exception) { return false to "data" }
        return try {
            val r = Supa.call("POST", "/rest/v1/laps", body, bearer = access.ifEmpty { Supa.ANON }, prefer = "return=minimal", timeoutMs = 20000)
            if (r.ok) true to "ok" else false to (if (r.code == 401 || r.code == 403) "auth" else "server")
        } catch (e: Exception) { false to "network" }
    }
}
