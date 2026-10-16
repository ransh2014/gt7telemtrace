package app.trace.telemetry

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.Test
import java.io.File
import java.nio.file.Files

class RecorderTest {
    private fun pkt(lap: Int, x: Double, speed: Double = 100.0, lastMs: Int = 0): Packet = mapOf(
        "lap_number" to lap, "world_x" to x, "world_z" to x * 0.5, "speed_kmh" to speed, "track_position" to x,
        "last_lap_ms" to lastMs, "fuel_remaining" to 50.0 - x * 0.001, "fuel_capacity" to 100.0,
        "wheel_steering_angle" to listOf(0.1, 0.1), "surface_type" to listOf("T", "T", "T", "C"), "car_category" to "GR3",
    )

    @Test fun sampleKeysMatchPython() {
        val keys = JSONArray(File("src/test/resources/sample_keys.json").readText()).let { a -> List(a.length()) { a.getString(it) } }
        val r = Recorder(Files.createTempDirectory("lp").toFile(), {})
        assertEquals(keys, r.sampleDict(pkt(1, 0.0), 0.0).keys.toList())
    }

    @Test fun recordsAndSavesLap() {
        val dir = Files.createTempDirectory("laps").toFile()
        var t = 0.0
        val logs = ArrayList<String>()
        val r = Recorder(dir, { logs.add(it) }, { t })
        r.recordRate = 60
        val ctx = Recorder.Ctx("SUGO International Racing Course", "Honda Civic SI '87", listOf(3.6, 2.2))
        r.startLap(1)
        // lap 1: waiting -> lap counter 2 starts recording
        for (i in 0 until 130) { t += 0.02; r.tick(pkt(2, i * 3.0), true, ctx) }
        t += 0.02; r.tick(pkt(3, 0.0, lastMs = 107120), true, ctx)   // lap complete
        val files = dir.walkTopDown().filter { it.isFile && it.name.startsWith("honda") }.toList()
        assertEquals(logs.joinToString("\n"), 1, files.size)
        val j = JSONObject(files[0].readText())
        assertEquals(107.12, j.getDouble("lap_time_s"), 1e-9)
        assertEquals("sugo_international_racing_course", j.getString("track"))
        assertTrue(j.getJSONArray("samples").length() >= 100)
        assertTrue(File(dir, "sugo_international_racing_course/reference_honda_civic_si_87.json").exists())
        assertTrue(File(dir, "sugo_international_racing_course/personal_bests.json").exists())
        // live delta against that reference
        val d = r.liveDelta("sugo_international_racing_course", "honda_civic_si_87", 10.0, 100.0)
        assertNotNull(d)
    }

    @Test fun sanitizeMatchesPython() {
        assertEquals("honda_civic_si_87", TraceUtil.sanitize("Honda Civic SI '87"))
        assertEquals("unknown", TraceUtil.sanitize("..."))
        assertEquals("con_", TraceUtil.sanitize("Con"))
        assertEquals("1:47.120", TraceUtil.msToLapTime(107120))
        assertEquals("--:--.---", TraceUtil.msToLapTime(-1))
    }
}
