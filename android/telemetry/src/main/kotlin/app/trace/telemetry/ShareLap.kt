package app.trace.telemetry

import java.io.ByteArrayInputStream
import java.io.ByteArrayOutputStream
import java.util.Base64
import java.util.zip.GZIPInputStream
import java.util.zip.GZIPOutputStream
import kotlin.math.floor

/**
 * sharelink.py: the lap-sharing payload (format v1), byte-for-byte the same JSON the PC builds. Pure JVM, no Android
 * classes, so the golden test (Golden4Test.shareLapPayloads) can compare it with the real Python output.
 *
 *   {"v":1,"car":..,"track":..,"lap_time_ms":N,"n":M,"t":[..],"x":[..],"z":[..],"s":[..],"th":[..],"br":[..]}
 *   t = ms since the first sample, x/z = world position in decimetres (empty when the lap has none),
 *   s = 0.1 km/h, th/br = 0..1000. At most MAX_POINTS evenly spaced samples. Whole numbers only, ASCII only.
 *
 * Nothing here talks to the network; the HTTP call lives in the app module (ShareClient).
 */
object ShareLap {
    const val SITE_URL = "https://gt7trace.netlify.app/lap"
    const val MAX_POINTS = 3000
    const val MAX_GZIP_BYTES = 200 * 1024
    private const val MIN_LAP_MS = 10_000L
    private const val MAX_LAP_MS = 3_600_000L

    /** Why a lap can't be shared: "incomplete" | "samples" | "time" (the app maps these to the shared wording), or null. */
    fun problem(meta: Meta, df: Frame): String? {
        if (meta.truthy("incomplete")) return "incomplete"
        if (df.n < 2) return "samples"
        val ms = q(numOf(meta["lap_time_s"]), 1000.0)
        if (ms < MIN_LAP_MS || ms > MAX_LAP_MS) return "time"
        return null
    }

    /** floor(v*scale + 0.5), the same quantiser sharelink._q uses. */
    private fun q(v: Double, scale: Double): Long = floor(v * scale + 0.5).toLong()
    private fun clamp(v: Long, lo: Long, hi: Long) = if (v < lo) lo else if (v > hi) hi else v
    private fun fin(v: Double) = if (v.isFinite()) v else 0.0

    fun pickIndices(n: Int, m: Int = MAX_POINTS): IntArray =
        if (n <= m) IntArray(n) { it } else IntArray(m) { ((it.toLong() * (n - 1)) / (m - 1)).toInt() }

    private fun col(df: Frame, name: String): DoubleArray? = if (df.has(name)) df[name] else null

    private fun firstNonEmpty(meta: Meta, vararg keys: String): String {
        for (k in keys) { val v = meta[k]; if (v is String && v.isNotEmpty()) return v }
        return "?"
    }

    /** Python's str[:120] counts code points, not UTF-16 units. */
    private fun cap120(s: String): String =
        if (s.codePointCount(0, s.length) <= 120) s else s.substring(0, s.offsetByCodePoints(0, 120))

    /** json.dumps(s, ensure_ascii=True) */
    fun jsonString(s: String): String {
        val sb = StringBuilder(s.length + 2).append('"')
        for (ch in s) {
            when {
                ch == '"' -> sb.append("\\\"")
                ch == '\\' -> sb.append("\\\\")
                ch == '\n' -> sb.append("\\n")
                ch == '\r' -> sb.append("\\r")
                ch == '\t' -> sb.append("\\t")
                ch == '\b' -> sb.append("\\b")
                ch == '\u000c' -> sb.append("\\f")
                ch.code < 0x20 || ch.code > 0x7e ->sb.append("\\u").append(String.format("%04x", ch.code))
                else -> sb.append(ch)
            }
        }
        return sb.append('"').toString()
    }

    private fun ints(sb: StringBuilder, key: String, v: LongArray) {
        sb.append(",\"").append(key).append("\":[")
        for (i in v.indices) { if (i > 0) sb.append(','); sb.append(v[i]) }
        sb.append(']')
    }

    /** The canonical v1 JSON (what gets gzipped). Throws IllegalArgumentException for a lap [problem] rules out. */
    fun payloadJson(meta: Meta, df: Frame): String {
        require(problem(meta, df) == null) { "lap can't be shared" }
        val idx = pickIndices(df.n)
        val tCol = col(df, "t"); val xCol = col(df, "world_x"); val zCol = col(df, "world_z")
        val spCol = col(df, "speed_kmh"); val thCol = col(df, "throttle"); val brCol = col(df, "brake")
        val t0 = if (tCol != null) fin(tCol[0]) else 0.0
        var hasXz = false
        if (xCol != null || zCol != null) for (i in 0 until df.n) {
            if ((xCol != null && fin(xCol[i]) != 0.0) || (zCol != null && fin(zCol[i]) != 0.0)) { hasXz = true; break }
        }
        val m = idx.size
        val t = LongArray(m); val sp = LongArray(m); val th = LongArray(m); val br = LongArray(m)
        val x = if (hasXz) LongArray(m) else LongArray(0)
        val z = if (hasXz) LongArray(m) else LongArray(0)
        for (k in 0 until m) {
            val i = idx[k]
            t[k] = maxOf(0L, q((if (tCol != null) fin(tCol[i]) else 0.0) - t0, 1000.0))
            if (hasXz) {
                x[k] = q(if (xCol != null) fin(xCol[i]) else 0.0, 10.0)
                z[k] = q(if (zCol != null) fin(zCol[i]) else 0.0, 10.0)
            }
            sp[k] = clamp(q(if (spCol != null) fin(spCol[i]) else 0.0, 10.0), 0, 20000)
            th[k] = clamp(q(if (thCol != null) fin(thCol[i]) else 0.0, 1000.0), 0, 1000)
            br[k] = clamp(q(if (brCol != null) fin(brCol[i]) else 0.0, 1000.0), 0, 1000)
        }
        val sb = StringBuilder(m * 28 + 256)
        sb.append("{\"v\":1,\"car\":").append(jsonString(cap120(firstNonEmpty(meta, "car_display", "car"))))
            .append(",\"track\":").append(jsonString(cap120(firstNonEmpty(meta, "track_display", "track"))))
            .append(",\"lap_time_ms\":").append(q(numOf(meta["lap_time_s"]), 1000.0))
            .append(",\"n\":").append(m)
        ints(sb, "t", t); ints(sb, "x", x); ints(sb, "z", z); ints(sb, "s", sp); ints(sb, "th", th); ints(sb, "br", br)
        return sb.append('}').toString()
    }

    class TooBig : Exception("payload over the size cap")

    fun gzip(json: String): ByteArray {
        val bo = ByteArrayOutputStream()
        GZIPOutputStream(bo).use { it.write(json.toByteArray(Charsets.US_ASCII)) }
        val out = bo.toByteArray()
        if (out.size > MAX_GZIP_BYTES) throw TooBig()
        return out
    }

    fun gunzip(blob: ByteArray, limit: Int = 4_000_000): String {
        GZIPInputStream(ByteArrayInputStream(blob)).use { s ->
            val bo = ByteArrayOutputStream()
            val buf = ByteArray(8192)
            while (true) {
                val r = s.read(buf)
                if (r < 0) break
                bo.write(buf, 0, r)
                if (bo.size() > limit) throw IllegalStateException("payload too large once decompressed")
            }
            return bo.toString(Charsets.US_ASCII.name())
        }
    }

    fun base64(blob: ByteArray): String = Base64.getEncoder().encodeToString(blob)

    private val idRe = Regex("^[0-9a-f]{20}$")
    fun validId(v: String?): Boolean = v != null && idRe.matches(v)
    fun linkFor(id: String): String { require(validId(id)) { "not a shared-lap id" }; return "$SITE_URL?id=$id" }

    /** The id out of a pasted link (or a bare id), or null. */
    fun idFrom(text: String?): String? {
        val s = (text ?: "").trim()
        val m = Regex("[?&]id=([0-9a-fA-F]{20})(?![0-9a-fA-F])").find(s)
        val cand = (m?.groupValues?.get(1) ?: s).lowercase()
        return if (validId(cand)) cand else null
    }

    /** sharelink.lap_key: when it was recorded, its time, track and car (so "Delete shared lap" finds the right link). */
    fun lapKey(meta: Meta): String {
        val rec = (meta["recorded_at"] as? String) ?: ""
        return listOf(rec, q(numOf(meta["lap_time_s"]), 1000.0).toString(), firstNonEmptyOrBlank(meta, "track_display", "track"),
            firstNonEmptyOrBlank(meta, "car_display", "car")).joinToString("|")
    }
    private fun firstNonEmptyOrBlank(meta: Meta, vararg keys: String): String {
        for (k in keys) { val v = meta[k]; if (v is String && v.isNotEmpty()) return v }
        return ""
    }
}
