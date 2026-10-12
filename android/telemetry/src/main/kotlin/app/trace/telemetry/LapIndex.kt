package app.trace.telemetry

import java.io.File
import java.time.LocalDateTime
import java.time.format.DateTimeFormatter
import java.time.Instant
import java.time.ZoneId

/** records.py + lap_analyst._scan_laps: a cheap index of the lap files the Dashboard saved. */
object LapIndex {
    private const val HEAD_CHARS = 8192
    private val SKIP_DIRS = setOf("races", "_session")

    class Row(
        val path: File, val car: String, val track: String, val date: String, val lapTime: String, val timeS: Double,
        val recordedAt: String, val incomplete: Boolean, val currentEra: Boolean, val mtime: Long,
    )

    private val cache = HashMap<String, Pair<Long, Row?>>()
    private val inFmt = DateTimeFormatter.ofPattern("yyyyMMdd_HHmmss")
    private val outFmt = DateTimeFormatter.ofPattern("yyyy-MM-dd HH:mm")

    /** Summary fields of a lap file (everything except `samples`), or null if it isn't a TRACE lap. */
    class RaceRow(val path: File, val label: String, val date: String, val mtime: Long)

    /** Saved race files (<track>/races/race_*.json), newest first. */
    fun scanRaces(folder: File, limit: Int = 200): List<RaceRow> {
        if (!folder.isDirectory) return emptyList()
        val files = folder.walkTopDown().filter { it.isFile && it.extension == "json" && it.parentFile?.name == "races" && it.name.startsWith("race_") }
            .sortedByDescending { it.lastModified() }.take(limit).toList()
        return files.map { f ->
            val h = header(f, false)
            val stamp = f.nameWithoutExtension.takeLast(15)
            RaceRow(f, if (h != null) RaceLoader.raceLabel(h) else f.name, dateOf(stamp, f.lastModified(), true), f.lastModified())
        }
    }

    fun header(file: File, requireLap: Boolean = true): Meta? {
        try {
            val head = file.bufferedReader(Charsets.UTF_8).use { r -> val buf = CharArray(HEAD_CHARS); val n = r.read(buf); if (n <= 0) "" else String(buf, 0, n) }
            val text = head.removePrefix("﻿")
            val cut = text.indexOf("\"samples\"")
            if (cut > 0) {
                var body = text.substring(0, cut).trimEnd()
                if (body.endsWith(",")) body = body.dropLast(1)
                try {
                    val m = JsonColumns.parse("$body}").meta
                    if (!requireLap || m.containsKey("lap_time_s")) return m
                } catch (e: Exception) { /* fall back to a full read */ }
            }
            val raw = JsonColumns.read(file)
            if (!raw.hadSamples || (requireLap && !raw.meta.containsKey("lap_time_s"))) return null
            return raw.meta
        } catch (e: Exception) {
            return null
        }
    }

    private fun dateOf(stamp: String, mtime: Long, withTime: Boolean): String {
        return try {
            val d = LocalDateTime.parse(stamp.take(15), inFmt)
            if (withTime) d.format(outFmt) else d.toLocalDate().toString()
        } catch (e: Exception) {
            if (!withTime) ""
            else LocalDateTime.ofInstant(Instant.ofEpochMilli(mtime), ZoneId.systemDefault()).format(outFmt)
        }
    }

    private fun rowFor(f: File): Row? {
        val mt = f.lastModified()
        val key = f.path
        cache[key]?.let { if (it.first == mt) return it.second }
        val h = header(f)
        var row: Row? = null
        if (h != null) {
            val lt = numOf(h["lap_time_s"])
            val stamp = h["recorded_at"]?.toString() ?: ""
            row = Row(f,
                (h["car_display"]?.toString()?.takeIf { it.isNotEmpty() } ?: h["car"]?.toString()?.takeIf { it.isNotEmpty() } ?: "Unknown car"),
                (h["track_display"]?.toString()?.takeIf { it.isNotEmpty() } ?: h["track"]?.toString()?.takeIf { it.isNotEmpty() } ?: "Unknown track"),
                dateOf(stamp, mt, true), if (lt > 0) LapMath.fmtTime(lt) else "", lt, stamp, h.truthy("incomplete"), LapMath.isCurrentEra(h), mt)
        }
        cache[key] = mt to row
        return row
    }

    /** Lap files under [folder], newest first. reference_* and personal_bests are included, as in the PC picker. */
    fun scanPicker(folder: File, limit: Int = 300): List<Row> {
        if (!folder.isDirectory) return emptyList()
        val files = folder.walkTopDown().filter { it.isFile && it.extension == "json" }.sortedByDescending { it.lastModified() }.take(limit * 2).toList()
        val out = ArrayList<Row>()
        for (f in files) { if (out.size >= limit) break; rowFor(f)?.let { out.add(it) } }
        return out
    }

    /** records.scan_laps: every readable lap under [folder] with a lap time; reference copies and index files are skipped. */
    fun scanAll(folder: File): List<Row> {
        if (!folder.isDirectory) return emptyList()
        val out = ArrayList<Row>()
        for (f in folder.walkTopDown()) {
            if (!f.isFile || f.extension != "json") continue
            val rel = f.relativeTo(folder).path.split(File.separatorChar)
            if (rel.dropLast(1).any { it in SKIP_DIRS } || f.name == "personal_bests.json" || f.name.startsWith("reference_")) continue
            val r = rowFor(f) ?: continue
            if (r.timeS > 0) out.add(r)
        }
        return out
    }

    class Record(val car: String, val track: String, val timeS: Double, val time: String, val laps: Int, val date: String, val path: File)

    /** One record per (car, track): the fastest complete lap. Laps from before the current physics era are left out unless [includeOld]. */
    fun bestByCarTrack(rows: List<Row>, includeOld: Boolean = false): List<Record> {
        class G(val car: String, val track: String) { var laps = 0; var best: Row? = null }
        val groups = LinkedHashMap<Pair<String, String>, G>()
        for (r in rows) {
            if (r.incomplete || r.timeS <= 0) continue
            if (!includeOld && !r.currentEra) continue
            val g = groups.getOrPut(r.track.lowercase() to r.car.lowercase()) { G(r.car, r.track) }
            g.laps++
            if (g.best == null || r.timeS < g.best!!.timeS) g.best = r
        }
        return groups.values.map { g ->
            val b = g.best!!
            Record(g.car, g.track, b.timeS, LapMath.fmtTime(b.timeS), g.laps, dateOf(b.recordedAt, b.mtime, false), b.path)
        }.sortedWith(compareBy({ it.track.lowercase() }, { it.car.lowercase() }))
    }

    /** The saved laps worth stitching into the theoretical best: same car and track as Lap A, complete, same era, fastest first, not Lap A itself. */
    fun theoryCandidates(rows: List<Row>, lapMeta: Meta, pathA: File?, limit: Int): List<Row> {
        val car = (lapMeta["car_display"]?.toString()?.takeIf { it.isNotEmpty() } ?: lapMeta["car"]?.toString() ?: "").lowercase()
        val track = (lapMeta["track_display"]?.toString()?.takeIf { it.isNotEmpty() } ?: lapMeta["track"]?.toString() ?: "").lowercase()
        val current = LapMath.isCurrentEra(lapMeta)
        val aPath = pathA?.canonicalPath
        return rows.filter { it.car.lowercase() == car && it.track.lowercase() == track && !it.incomplete && it.currentEra == current &&
            it.path.canonicalPath != aPath }.sortedBy { it.timeS }.take(limit)
    }

    fun filter(rows: List<Row>, car: String, track: String, date: String): List<Row> {
        val cq = car.trim().lowercase(); val tq = track.trim().lowercase(); val dq = date.trim().lowercase()
        return rows.filter { (cq.isEmpty() || cq in it.car.lowercase()) && (tq.isEmpty() || tq in it.track.lowercase()) && (dq.isEmpty() || dq in it.date.lowercase()) }
    }
}
