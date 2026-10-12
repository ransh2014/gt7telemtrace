package app.trace.telemetry

/** cars.py / tracks.py: ID -> name lookup from the bundled CSVs. */
class NameDb(carCsv: String, courseCsv: String) {
    private val cars = HashMap<Int, String>()
    private val tracks = HashMap<Int, String>()

    init { load(carCsv, "ShortName", cars); load(courseCsv, "Name", tracks) }

    fun carName(id: Int): String = cars[id] ?: ""
    fun trackName(id: Int): String = tracks[id] ?: ""
    fun allTrackNames(): List<String> = tracks.values.filter { it.isNotEmpty() }.sorted()
    fun allCarNames(): List<String> = cars.values.filter { it.isNotEmpty() }.sorted()

    private fun load(csv: String, nameCol: String, into: MutableMap<Int, String>) {
        val rows = parse(csv.removePrefix("﻿"))
        if (rows.isEmpty()) return
        val head = rows[0]; val idI = head.indexOf("ID"); val nI = head.indexOf(nameCol)
        if (idI < 0 || nI < 0) return
        for (r in rows.drop(1)) {
            val id = r.getOrNull(idI)?.trim()?.toIntOrNull() ?: continue
            val n = r.getOrNull(nI)?.trim() ?: ""
            if (n.isNotEmpty()) into[id] = n
        }
    }

    companion object {
        /** Minimal RFC-4180 CSV parser (quotes, embedded commas/newlines). */
        fun parse(s: String): List<List<String>> {
            val rows = ArrayList<List<String>>(); var row = ArrayList<String>(); val f = StringBuilder(); var q = false; var i = 0
            while (i < s.length) {
                val c = s[i]
                if (q) { if (c == '"') { if (i + 1 < s.length && s[i + 1] == '"') { f.append('"'); i++ } else q = false } else f.append(c) }
                else when (c) {
                    '"' -> q = true
                    ',' -> { row.add(f.toString()); f.setLength(0) }
                    '\r' -> {}
                    '\n' -> { row.add(f.toString()); f.setLength(0); rows.add(row); row = ArrayList() }
                    else -> f.append(c)
                }
                i++
            }
            if (f.isNotEmpty() || row.isNotEmpty()) { row.add(f.toString()); rows.add(row) }
            return rows
        }
    }
}
