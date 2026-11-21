package app.trace.telemetry

import org.json.JSONArray
import org.json.JSONObject
import java.io.ByteArrayOutputStream
import java.io.File
import java.security.MessageDigest
import java.time.LocalDate
import java.time.temporal.IsoFields
import java.util.Base64
import java.util.Locale
import java.util.zip.Deflater
import java.util.zip.Inflater

/**
 * groups.py: friends groups, private leaderboards and weekly challenges with no server. A challenge is a plain
 * map (track, car, week, name, results); results are maps (player, time_ms, sectors_ms). Boards travel as pasteable
 * `TRC1-<crc>-<payload>` codes that decode on the desktop and on Android alike. The checksum guards against typos,
 * it is not anti-cheat.
 */
object Groups {
    const val PREFIX = "TRC1"
    const val MAX_NAME = 24
    const val MAX_SECTORS = 24
    const val MAX_ENTRIES = 500
    private const val MAX_INFLATED = 4_000_000   // safety cap while decoding a pasted code

    @Suppress("UNCHECKED_CAST")
    private fun results(ch: Map<String, Any?>) = ch["results"] as MutableList<MutableMap<String, Any?>>

    fun weekId(date: LocalDate = LocalDate.now()): String =
        "${date.get(IsoFields.WEEK_BASED_YEAR)}-W${"%02d".format(Locale.ROOT, date.get(IsoFields.WEEK_OF_WEEK_BASED_YEAR))}"

    private fun printable(cp: Int): Boolean {
        if (cp == ' '.code) return true
        return when (Character.getType(cp).toByte()) {
            Character.CONTROL, Character.FORMAT, Character.SURROGATE, Character.PRIVATE_USE, Character.UNASSIGNED,
            Character.LINE_SEPARATOR, Character.PARAGRAPH_SEPARATOR, Character.SPACE_SEPARATOR -> false
            else -> true
        }
    }

    /** _safe(): printable characters only, no < > & " ', cut to n characters and trimmed. */
    fun safe(s: Any?, n: Int = MAX_NAME): String {
        val sb = StringBuilder()
        var count = 0
        for (cp in s.toString().codePoints().toArray()) {
            if (!printable(cp) || cp.toChar() in "<>&\"'" && cp < 128) continue
            if (count >= n) break
            sb.appendCodePoint(cp); count++
        }
        return sb.toString().trim()
    }

    fun makeChallenge(track: String, car: String, week: String? = null, name: String = ""): MutableMap<String, Any?> =
        linkedMapOf("track" to safe(track, 60), "car" to safe(car, 60), "week" to (week ?: weekId()),
            "name" to safe(name.ifEmpty { "$track weekly" }), "results" to ArrayList<MutableMap<String, Any?>>())

    private fun key(ch: Map<String, Any?>) = "${ch["week"]}|${(ch["track"] as String).lowercase()}|${(ch["car"] as String).lowercase()}"

    private fun toLongOrNull(v: Any?): Long? = when (v) {
        is Boolean -> null
        is Number -> if (v is Double || v is Float) (if (v.toDouble().isFinite()) v.toDouble().toLong() else null) else v.toLong()
        is String -> v.trim().toLongOrNull()
        else -> null
    }

    /** Record a lap. Keeps each player's best; false for a nonsense time or a full board. */
    fun addResult(ch: MutableMap<String, Any?>, player: Any?, lapTimeMs: Any?, sectorsMs: List<*>? = null): Boolean {
        val t = toLongOrNull(lapTimeMs) ?: return false
        if (t !in 20_000L..1_800_000L) return false
        val secs = ArrayList<Long>()
        for (x in sectorsMs ?: emptyList<Any?>()) if (x is Number && x !is Boolean && x.toDouble() > 0) secs.add(x.toLong())
        val sectors = secs.take(MAX_SECTORS)
        val p = safe(player)
        if (p.isEmpty()) return false
        for (r in results(ch)) {
            if ((r["player"] as String).lowercase() == p.lowercase()) {
                if (t < (r["time_ms"] as Number).toLong()) { r["time_ms"] = t; r["sectors_ms"] = ArrayList(sectors) }
                return true
            }
        }
        if (results(ch).size >= MAX_ENTRIES) return false
        results(ch).add(linkedMapOf("player" to p, "time_ms" to t, "sectors_ms" to ArrayList(sectors)))
        return true
    }

    class Standing(val pos: Int, val player: String, val timeMs: Long, val gapMs: Long)

    fun standings(ch: Map<String, Any?>): List<Standing> {
        val rows = results(ch).sortedBy { (it["time_ms"] as Number).toLong() }
        val lead = rows.firstOrNull()?.let { (it["time_ms"] as Number).toLong() } ?: 0L
        return rows.mapIndexed { i, r -> val t = (r["time_ms"] as Number).toLong(); Standing(i + 1, r["player"] as String, t, t - lead) }
    }

    class SectorBest(val sector: Int, val player: String, val timeMs: Long)

    private fun sectors(r: Map<String, Any?>) = (r["sectors_ms"] as List<*>).map { (it as Number).toLong() }

    fun sectorBoard(ch: Map<String, Any?>): List<SectorBest> {
        val rs = results(ch)
        val n = rs.maxOfOrNull { sectors(it).size } ?: 0
        val out = ArrayList<SectorBest>()
        for (i in 0 until n) {
            var best: Pair<Long, String>? = null
            for (r in rs) {
                val s = sectors(r)
                if (s.size > i) {
                    val cand = s[i] to (r["player"] as String)
                    val b = best
                    // Python's min() over (time, player) tuples: lowest time, then the alphabetically first player
                    if (b == null || cand.first < b.first || (cand.first == b.first && cand.second < b.second)) best = cand
                }
            }
            best?.let { out.add(SectorBest(i + 1, it.second, it.first)) }
        }
        return out
    }

    fun idealLap(ch: Map<String, Any?>): Long? = sectorBoard(ch).let { if (it.isEmpty()) null else it.sumOf { b -> b.timeMs } }

    fun fmt(ms: Long): String {
        val m = Math.floorDiv(ms, 60000L); val rest = Math.floorMod(ms, 60000L)
        return "$m:" + "%06.3f".format(Locale.ROOT, rest / 1000.0)
    }

    // ---- codes -------------------------------------------------------------------------------------------------
    /** json.dumps(sort_keys=True, separators=(",", ":")) with ensure_ascii, so codes read the same on both sides. */
    fun canonicalJson(v: Any?): String {
        val sb = StringBuilder(); writeJson(sb, v); return sb.toString()
    }

    private fun writeString(sb: StringBuilder, s: String) {
        sb.append('"')
        for (ch in s) when {
            ch == '"' -> sb.append("\\\""); ch == '\\' -> sb.append("\\\\"); ch == '\n' -> sb.append("\\n")
            ch == '\r' -> sb.append("\\r"); ch == '\t' -> sb.append("\\t"); ch == '\b' -> sb.append("\\b"); ch == '\u000c' -> sb.append("\\f")
            ch.code < 0x20 || ch.code > 0x7e -> sb.append("\\u").append("%04x".format(Locale.ROOT, ch.code))
            else -> sb.append(ch)
        }
        sb.append('"')
    }

    private fun writeJson(sb: StringBuilder, v: Any?) {
        when (v) {
            null, JSONObject.NULL -> sb.append("null")
            is Boolean -> sb.append(if (v) "true" else "false")
            is Int, is Long, is Short, is Byte -> sb.append(v.toString())
            is Number -> { val d = v.toDouble(); if (d == Math.rint(d) && Math.abs(d) < 1e15) sb.append(d.toLong()).append(".0") else sb.append(d.toString()) }
            is String -> writeString(sb, v)
            is Map<*, *> -> {
                sb.append('{')
                var first = true
                for (k in v.keys.map { it.toString() }.sorted()) {
                    if (!first) sb.append(','); first = false
                    writeString(sb, k); sb.append(':'); writeJson(sb, v[k])
                }
                sb.append('}')
            }
            is List<*> -> { sb.append('['); v.forEachIndexed { i, x -> if (i > 0) sb.append(','); writeJson(sb, x) }; sb.append(']') }
            else -> writeString(sb, v.toString())
        }
    }

    private fun sha6(b: String): String =
        MessageDigest.getInstance("SHA-256").digest(b.toByteArray(Charsets.US_ASCII)).joinToString("") { "%02x".format(Locale.ROOT, it) }.substring(0, 6)

    fun pack(payload: Map<String, Any?>): String {
        val raw = canonicalJson(payload).toByteArray(Charsets.UTF_8)
        val d = Deflater(9)   // zlib wrapper, same container as Python's zlib.compress
        d.setInput(raw); d.finish()
        val out = ByteArrayOutputStream(); val buf = ByteArray(4096)
        while (!d.finished()) out.write(buf, 0, d.deflate(buf))
        d.end()
        val b = Base64.getUrlEncoder().withoutPadding().encodeToString(out.toByteArray())
        return "$PREFIX-${sha6(b)}-$b"
    }

    /** The decoded payload, or null for a bad checksum / damaged / edited code. */
    @Suppress("UNCHECKED_CAST")
    fun unpack(code: String): Map<String, Any?>? = try {
        val parts = code.trim().split("-", limit = 3)
        if (parts.size != 3 || parts[0] != PREFIX || sha6(parts[2]) != parts[1]) null
        else {
            val inf = Inflater()
            inf.setInput(Base64.getUrlDecoder().decode(parts[2]))
            val out = ByteArrayOutputStream(); val buf = ByteArray(4096)
            while (!inf.finished()) {
                val n = inf.inflate(buf)
                if (n == 0 && (inf.needsInput() || inf.needsDictionary())) throw IllegalArgumentException("truncated")
                out.write(buf, 0, n)
                if (out.size() > MAX_INFLATED) throw IllegalArgumentException("too large")
            }
            inf.end()
            val o = JSONObject(out.toString("UTF-8"))
            fromJson(o) as Map<String, Any?>
        }
    } catch (_: Exception) { null }

    /** org.json -> plain maps / lists / numbers so the rest of this file never sees JSONObject. */
    fun fromJson(v: Any?): Any? = when (v) {
        JSONObject.NULL, null -> null
        is JSONObject -> LinkedHashMap<String, Any?>().also { m -> for (k in v.keys()) m[k] = fromJson(v.get(k)) }
        is JSONArray -> ArrayList<Any?>().also { l -> for (i in 0 until v.length()) l.add(fromJson(v.get(i))) }
        else -> v
    }

    fun exportChallenge(ch: Map<String, Any?>): String = pack(linkedMapOf("v" to 1, "ch" to ch))

    fun exportResult(ch: Map<String, Any?>, player: String): String? {
        for (r in results(ch)) if ((r["player"] as String).lowercase() == player.lowercase())
            return pack(linkedMapOf("v" to 1, "key" to key(ch), "r" to r, "track" to ch["track"], "car" to ch["car"], "week" to ch["week"], "name" to ch["name"]))
        return null
    }

    /** Merge a pasted code. With no ch a challenge code creates one. Returns (challenge, message), the messages are the desktop's. */
    fun importCode(code: String, ch: MutableMap<String, Any?>? = null): Pair<MutableMap<String, Any?>?, String> {
        val d = unpack(code)
        if (d == null || (d["v"] as? Number)?.toInt() != 1) return ch to "That code isn't valid (a character is missing or it was edited)."
        if ("ch" in d) {
            @Suppress("UNCHECKED_CAST") val src = d["ch"] as? Map<String, Any?>
            if (src == null || listOf("track", "car", "week", "results").any { it !in src }) return ch to "That challenge code is damaged."
            val base = ch ?: makeChallenge(src["track"].toString(), src["car"].toString(), src["week"].toString(), (src["name"] ?: "").toString())
            if (key(base) != key(src.mapValues { (k, v) -> if (k == "track" || k == "car") v.toString() else v })) return ch to "That code is for a different car, track or week."
            var n = 0
            for (r in (src["results"] as? List<*>) ?: emptyList<Any?>()) {
                @Suppress("UNCHECKED_CAST") val m = r as? Map<String, Any?> ?: continue
                if (addResult(base, m["player"] ?: "", m["time_ms"] ?: 0, m["sectors_ms"] as? List<*>)) n++
            }
            return base to "Merged $n result(s)."
        }
        @Suppress("UNCHECKED_CAST") val r = d["r"] as? Map<String, Any?> ?: return ch to "That code has no result in it."
        val base = ch ?: makeChallenge((d["track"] ?: "").toString(), (d["car"] ?: "").toString(), d["week"]?.toString(), (d["name"] ?: "").toString())
        if (d["key"] != key(base)) return ch to "That result is for a different car, track or week."
        val ok = addResult(base, r["player"] ?: "", r["time_ms"] ?: 0, r["sectors_ms"] as? List<*>)
        return base to (if (ok) "Added the result." else "That result was rejected (bad time or name).")
    }

    // ---- storage -----------------------------------------------------------------------------------------------
    fun load(file: File): MutableList<MutableMap<String, Any?>> = try {
        val a = JSONArray(file.readText())
        val out = ArrayList<MutableMap<String, Any?>>()
        for (i in 0 until a.length()) {
            @Suppress("UNCHECKED_CAST") val m = fromJson(a.opt(i)) as? MutableMap<String, Any?> ?: continue
            if ("results" in m && "week" in m) out.add(m)
        }
        out
    } catch (_: Exception) { ArrayList() }

    fun save(challenges: List<Map<String, Any?>>, file: File): Boolean = try {
        file.parentFile?.mkdirs()
        val tmp = File(file.parentFile, file.name + ".tmp")
        tmp.writeText(JSONArray(challenges.takeLast(100).map { JSONObject(canonicalJson(it)) }).toString(1))
        if (!tmp.renameTo(file)) { file.delete(); tmp.renameTo(file) }
        true
    } catch (_: Exception) { false }
}
