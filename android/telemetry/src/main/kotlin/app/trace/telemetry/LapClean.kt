package app.trace.telemetry

import org.json.JSONObject
import java.io.File
import java.security.MessageDigest

/** lapclean.py: finds identical lap files and moves the extra copies into <root>/_duplicates. Nothing is ever deleted. */
object LapClean {
    const val DUP_FOLDER = "_duplicates"
    private val SKIP_PREFIXES = listOf("reference_", "personal_bests", "backup")

    class Group(val keep: File, val extras: List<File>)

    private fun isLapFile(p: File, root: File): Boolean {
        val n = p.name.lowercase()
        return p.isFile && p.extension.lowercase() == "json" && SKIP_PREFIXES.none { n.startsWith(it) } &&
            !p.relativeTo(root).path.split(File.separatorChar).contains(DUP_FOLDER)
    }

    private fun sha1(s: String) = MessageDigest.getInstance("SHA-1").digest(s.toByteArray()).joinToString("") { "%02x".format(it) }

    /** (signature, recorded_at) or null when the file isn't a lap. */
    private fun signature(p: File): Pair<String, String>? = try {
        val d = JSONObject(p.readText().removePrefix("﻿"))
        val samples = d.optJSONArray("samples")
        if (samples == null || samples.length() == 0 || !d.has("lap_time_s") || d.isNull("lap_time_s")) null
        else {
            val n = samples.length()
            val probe = listOf(samples.get(0), samples.get(n / 2), samples.get(n - 1)).joinToString("|") { it.toString() }
            val sig = listOf(d.optString("track"), d.optString("car"), "%.3f".format(java.util.Locale.ROOT, d.optDouble("lap_time_s")), n.toString(), sha1(probe)).joinToString("\u0001")
            sig to d.optString("recorded_at")
        }
    } catch (_: Exception) { null }

    /** Identical laps inside the same folder; the earliest recording is kept. */
    fun findDuplicates(root: File): List<Group> {
        if (!root.exists()) return emptyList()
        val bySize = HashMap<Pair<String, Long>, MutableList<File>>()
        root.walkTopDown().filter { isLapFile(it, root) }.forEach { bySize.getOrPut((it.parent ?: "") to it.length()) { ArrayList() }.add(it) }
        val groups = HashMap<Pair<String, String>, MutableList<Triple<String, String, File>>>()
        for (paths in bySize.values) {
            if (paths.size < 2) continue
            for (p in paths) signature(p)?.let { (sig, rec) -> groups.getOrPut((p.parent ?: "") to sig) { ArrayList() }.add(Triple(rec, p.name, p)) }
        }
        return groups.values.filter { it.size > 1 }.map { items ->
            val sorted = items.sortedWith(compareBy({ it.first }, { it.second }))
            Group(sorted[0].third, sorted.drop(1).map { it.third })
        }.sortedBy { it.keep.path }
    }

    /** Moves the extras; never overwrites. Returns (moved, failed). */
    fun moveDuplicates(groups: List<Group>, root: File): Pair<Int, Int> {
        var moved = 0; var failed = 0
        for (g in groups) for (src in g.extras) {
            try {
                val rel = src.parentFile.relativeTo(root).path
                val destDir = File(File(root, DUP_FOLDER), rel).apply { mkdirs() }
                var dst = File(destDir, src.name); var i = 1
                while (dst.exists()) { dst = File(destDir, "${src.nameWithoutExtension}_$i.${src.extension}"); i++ }
                if (src.renameTo(dst) || (src.copyTo(dst) .let { src.delete() })) moved++ else failed++
            } catch (_: Exception) { failed++ }
        }
        return moved to failed
    }
}
