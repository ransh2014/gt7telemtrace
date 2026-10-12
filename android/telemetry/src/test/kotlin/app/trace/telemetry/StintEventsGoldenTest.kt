package app.trace.telemetry

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File

class StintEventsGoldenTest {
    private val root = JSONObject(File("src/test/resources/fixtures2.json").readText())

    private fun near(path: String, exp: Any?, act: Double?) {
        if (exp == null || exp == JSONObject.NULL) assertNull("$path expected null", act)
        else { assertNotNull("$path expected ${exp}", act); assertEquals(path, (exp as Number).toDouble(), act!!, 1e-9 * maxOf(1.0, Math.abs(exp.toDouble()))) }
    }

    private fun check(tag: String, st: StintTracker, snap: JSONObject) {
        val s = st.snapshot()
        near("$tag.fuel_per_lap", snap.get("fuel_per_lap"), s.fuelPerLap)
        assertEquals("$tag.basis", snap.getInt("fuel_basis_laps"), s.fuelBasisLaps)
        near("$tag.laps_of_fuel", snap.get("laps_of_fuel"), s.lapsOfFuel)
        near("$tag.spare", snap.get("spare_laps"), s.spareLaps)
        val stop = snap.get("stop_needed")
        assertEquals("$tag.stop", if (stop == JSONObject.NULL) null else stop as Boolean, s.stopNeeded)
        near("$tag.pit_by", snap.get("pit_by_lap"), s.pitByLap?.toDouble())
        assertEquals("$tag.age", snap.getInt("stint_age"), s.stintAge)
        assertEquals("$tag.clean", snap.getInt("stint_clean_laps"), s.stintCleanLaps)
        near("$tag.loss", snap.get("pace_loss_s"), s.paceLossS)
        near("$tag.trend", snap.get("trend_s_per_lap"), s.trendSPerLap)
        val ts = snap.get("tyre_state")
        assertEquals("$tag.state", if (ts == JSONObject.NULL) null else ts as String, s.tyreState)
        val h = snap.get("heat")
        if (h == JSONObject.NULL) assertNull("$tag.heat", s.heat)
        else { h as JSONObject; near("$tag.heat.f", h.get("front_avg"), s.heat?.frontAvg); near("$tag.heat.r", h.get("rear_avg"), s.heat?.rearAvg)
            near("$tag.heat.hot", h.get("pct_hot"), s.heat?.pctHot); near("$tag.heat.cold", h.get("pct_cold"), s.heat?.pctCold) }
    }

    private fun run(tag: String, arr: JSONArray) {
        val st = StintTracker()
        for (i in 0 until arr.length()) {
            val step = arr.getJSONObject(i)
            if (step.has("reset_stint")) st.resetStint()
            else {
                val a = step.getJSONArray("args")
                val temps = if (a.isNull(4)) null else List(4) { a.getDouble(it).let { _ -> a.getJSONArray(4).getDouble(it) } }
                st.update(a.getInt(0), a.getInt(1), a.getDouble(2), a.getInt(3), temps, a.getDouble(5))
            }
            check("$tag[$i]", st, step.getJSONObject("snap"))
        }
    }

    @Test fun stintMatchesPython() { run("stint", root.getJSONObject("stint").getJSONArray("steps")) }
    @Test fun freeRunMatchesPython() { run("free", root.getJSONObject("stint").getJSONArray("free_run")) }

    @Test fun eventsMatchPython() {
        var now = 0.0
        val ev = EventDetector { now }
        val fired = ArrayList<String>()
        for (n in listOf("pause", "resume", "race_start", "race_end")) ev.register(n) { fired.add(n) }
        val seq = root.getJSONArray("events")
        for (i in 0 until seq.length()) {
            val s = seq.getJSONObject(i)
            now = s.getDouble("t")
            val pj = s.getJSONObject("packet")
            val p = HashMap<String, Any?>()
            for (k in pj.keySet()) p[k] = pj.get(k)
            fired.clear()
            ev.check(p)
            val exp = s.getJSONArray("fired").let { a -> List(a.length()) { a.getString(it) } }
            assertEquals("event step $i", exp, fired.toList())
        }
    }
}
