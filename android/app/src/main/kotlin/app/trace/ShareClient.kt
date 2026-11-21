package app.trace

import android.content.Context
import app.trace.telemetry.*
import org.json.JSONObject

/**
 * sharelink.py's network half: upload one lap through the share_lap RPC, delete it through delete_shared_lap. The payload
 * itself comes from the shared ShareLap (golden-tested against the PC). NOTHING here is called without the confirmation
 * dialog having been answered Yes (LeaderboardCard), and share() refuses to run unless the caller says so.
 */
object ShareClient {
    class Result(val ok: Boolean, val reason: String = "", val id: String = "") {
        val link: String get() = if (ok && id.isNotEmpty()) ShareLap.linkFor(id) else ""
    }

    private fun prefs(ctx: Context) = ctx.getSharedPreferences("trace_shared_laps", Context.MODE_PRIVATE)
    fun idFor(ctx: Context, meta: Meta): String? = prefs(ctx).getString(ShareLap.lapKey(meta), null)?.takeIf { ShareLap.validId(it) }
    private fun remember(ctx: Context, meta: Meta, id: String) { prefs(ctx).edit().putString(ShareLap.lapKey(meta), id).apply() }
    private fun forget(ctx: Context, meta: Meta) { prefs(ctx).edit().remove(ShareLap.lapKey(meta)).apply() }

    /** sharelink._reason_for */
    fun reasonFor(code: Int, text: String): String {
        val t = text.lowercase()
        if (code == 401 || code == 403 || "28000" in t || "sign-in required" in t || "jwt" in t) return "auth"
        if ("rate limit" in t || "54000" in t || code == 429) return "limit"
        if ("too large" in t) return "toobig"
        if (code == 400 && ("22023" in t || "out of range" in t || "not gzip" in t || "not base64" in t || "bad car" in t)) return "data"
        return "server"
    }

    /** The wording for a failed share/delete, from the shared labels (same text as the PC). */
    fun reasonText(ctx: Context, reason: String): String = ctx.getString(when (reason) {
        "unconfirmed" -> R.string.share_fail_unconfirmed
        "data" -> R.string.share_fail_data
        "toobig" -> R.string.share_too_big
        "network" -> R.string.share_fail_network
        "auth", "session" -> R.string.share_fail_auth
        "limit" -> R.string.share_fail_limit
        else -> R.string.share_fail_server
    })

    /** Why the lap can't be shared, in the shared wording, or null. */
    fun problemText(ctx: Context, meta: Meta, df: Frame): String? = when (ShareLap.problem(meta, df)) {
        "incomplete" -> ctx.getString(R.string.share_incomplete)
        "samples" -> ctx.getString(R.string.share_no_samples)
        "time" -> ctx.getString(R.string.share_bad_time)
        else -> null
    }

    /** Blocking (call from Dispatchers.IO). [confirmed] must be true: sharing.share() sends nothing otherwise. */
    fun share(meta: Meta, df: Frame, access: String, confirmed: Boolean): Result {
        if (!confirmed) return Result(false, "unconfirmed")
        val (payload, blob) = try {
            val p = ShareLap.payloadJson(meta, df)
            p to ShareLap.gzip(p)
        } catch (e: ShareLap.TooBig) { return Result(false, "toobig") } catch (e: Exception) { return Result(false, "data") }
        val j = JSONObject(payload)
        val body = JSONObject().put("p_car", j.getString("car")).put("p_track", j.getString("track"))
            .put("p_lap_time_ms", j.getLong("lap_time_ms")).put("p_samples", j.getInt("n")).put("p_payload", ShareLap.base64(blob)).toString()
        return try {
            val r = Supa.call("POST", "/rest/v1/rpc/share_lap", body, bearer = access.ifEmpty { Supa.ANON }, timeoutMs = 20000)
            if (!r.ok) return Result(false, reasonFor(r.code, r.body))
            val id = try { org.json.JSONTokener(r.body).nextValue() as? String } catch (e: Exception) { null }
            if (id != null && ShareLap.validId(id)) Result(true, "", id) else Result(false, "server")
        } catch (e: Exception) { Result(false, "network") }
    }

    fun delete(id: String, access: String): Result {
        if (!ShareLap.validId(id)) return Result(false, "data")
        return try {
            val r = Supa.call("POST", "/rest/v1/rpc/delete_shared_lap", JSONObject().put("p_id", id).toString(), bearer = access.ifEmpty { Supa.ANON })
            if (r.ok) Result(true, "", id) else Result(false, reasonFor(r.code, r.body))
        } catch (e: Exception) { Result(false, "network") }
    }

    /** Runs [call] with the stored token, refreshing an expired session once (same flow as the leaderboard submit). Blocking. */
    private fun withSession(ctx: Context, call: (String) -> Result): Result {
        val acc = Account.load(ctx)
        if (!acc.signedIn) return Result(false, "auth")
        var res = call(acc.access)
        if (!res.ok && res.reason == "auth") {
            val sess = SupaAuth.refresh(acc.refresh)
            res = if (sess != null) { Account.saveSession(ctx, sess); call(sess.access) }
            else if (SupaAuth.lastRefreshError == "invalid") Result(false, "auth") else Result(false, "network")
        }
        return res
    }

    fun shareLap(ctx: Context, meta: Meta, df: Frame, confirmed: Boolean): Result {
        if (!confirmed) return Result(false, "unconfirmed")
        val res = withSession(ctx) { share(meta, df, it, true) }
        if (res.ok) remember(ctx, meta, res.id)
        return res
    }

    fun deleteShared(ctx: Context, meta: Meta): Result {
        val id = idFor(ctx, meta) ?: return Result(false, "data")
        val res = withSession(ctx) { delete(id, it) }
        if (res.ok) forget(ctx, meta)
        return res
    }
}
