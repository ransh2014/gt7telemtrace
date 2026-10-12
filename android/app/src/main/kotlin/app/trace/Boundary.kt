package app.trace

import app.trace.telemetry.Frame
import org.json.JSONArray
import org.json.JSONObject
import java.io.File
import kotlin.math.hypot

/** Port of tracks.extract_boundary / save_boundary / load_boundary. */
object Boundary {
    class Bin(val distance: Double, val cx: Double, val cz: Double, val lx: Double, val lz: Double, val rx: Double, val rz: Double)

    private fun r(x: Double, d: Int): Double { val m = Math.pow(10.0, d.toDouble()); return Math.rint(x * m) / m }

    private fun gradient(a: DoubleArray): DoubleArray {
        val n = a.size; val g = DoubleArray(n)
        if (n < 2) return g
        g[0] = a[1] - a[0]; g[n - 1] = a[n - 1] - a[n - 2]
        for (i in 1 until n - 1) g[i] = (a[i + 1] - a[i - 1]) / 2.0
        return g
    }

    private fun smooth(a: DoubleArray, k: Int): DoubleArray {
        val pad = k / 2; val n = a.size
        return DoubleArray(n) { i -> var s = 0.0; for (j in i - pad..i + pad) s += a[j.coerceIn(0, n - 1)]; s / k }
    }

    fun extract(df: Frame, binSize: Double = 25.0, smoothWindow: Int = 15): List<Bin> {
        if (!df.has("world_x") || !df.has("world_z") || !df.has("track_position")) return emptyList()
        val wx = df["world_x"]; val wz = df["world_z"]; val tpAll = df["track_position"]
        val idx = (0 until df.n).filter { wx[it] != 0.0 || wz[it] != 0.0 }
        if (idx.size < smoothWindow * 2) return emptyList()
        val xs = DoubleArray(idx.size) { wx[idx[it]] }; val zs = DoubleArray(idx.size) { wz[idx[it]] }; val tp = DoubleArray(idx.size) { tpAll[idx[it]] }
        val k = maxOf(3, smoothWindow or 1)
        val cx = smooth(xs, k); val cz = smooth(zs, k)
        val dx = gradient(cx); val dz = gradient(cz)
        val lat = DoubleArray(xs.size) { i ->
            var sl = hypot(dx[i], dz[i]); if (sl == 0.0) sl = 1.0
            (xs[i] - cx[i]) * (dz[i] / sl) + (zs[i] - cz[i]) * (-dx[i] / sl)
        }
        class E(var sx: Double = 0.0, var sz: Double = 0.0, var n: Int = 0, var mn: Double, var mni: Int, var mx: Double, var mxi: Int)
        val bins = java.util.TreeMap<Int, E>()
        for (i in xs.indices) {
            val b = Math.floor(tp[i] / binSize).toInt()
            val e = bins.getOrPut(b) { E(mn = lat[i], mni = i, mx = lat[i], mxi = i) }
            e.sx += cx[i]; e.sz += cz[i]; e.n++
            if (lat[i] < e.mn) { e.mn = lat[i]; e.mni = i }
            if (lat[i] > e.mx) { e.mx = lat[i]; e.mxi = i }
        }
        return bins.map { (b, e) -> Bin(r(b * binSize, 1), r(e.sx / e.n, 3), r(e.sz / e.n, 3), r(xs[e.mni], 3), r(zs[e.mni], 3), r(xs[e.mxi], 3), r(zs[e.mxi], 3)) }
    }

    private fun file(lapsDir: File) = File(lapsDir.parentFile ?: lapsDir, "track_boundaries.json")

    private fun readAll(lapsDir: File): JSONObject = try { JSONObject(file(lapsDir).readText().removePrefix("﻿")) } catch (e: Exception) { JSONObject() }

    fun save(lapsDir: File, track: String, bins: List<Bin>) {
        val all = readAll(lapsDir)
        val arr = JSONArray()
        for (b in bins) arr.put(JSONObject().put("distance", b.distance).put("center_x", b.cx).put("center_z", b.cz)
            .put("left_x", b.lx).put("left_z", b.lz).put("right_x", b.rx).put("right_z", b.rz))
        all.put(track, JSONObject().put("bins", arr))
        val f = file(lapsDir); f.parentFile?.mkdirs()
        val tmp = File(f.parentFile, f.name + ".tmp")
        tmp.writeText(all.toString()); if (!tmp.renameTo(f)) { f.delete(); tmp.renameTo(f) }
    }

    fun load(lapsDir: File, track: String): List<Bin>? {
        val arr = readAll(lapsDir).optJSONObject(track)?.optJSONArray("bins") ?: return null
        val out = ArrayList<Bin>()
        for (i in 0 until arr.length()) {
            val o = arr.getJSONObject(i)
            out.add(Bin(o.optDouble("distance"), o.optDouble("center_x"), o.optDouble("center_z"), o.optDouble("left_x"), o.optDouble("left_z"), o.optDouble("right_x"), o.optDouble("right_z")))
        }
        return out.ifEmpty { null }
    }
}
