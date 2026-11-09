package app.trace.telemetry

import org.junit.Assert.*
import org.junit.Test
import kotlin.math.abs
import kotlin.math.max

/** dyno.py rebuilt from a simulated car whose true power curve and gearbox are known (same sim as tests/test_dyno.py). */
class DynoTest {
    private val MASS = 1400.0
    private val C0 = 0.12; private val C1 = 3e-4
    private val K = mapOf(1 to 300.0, 2 to 200.0, 3 to 150.0, 4 to 115.0, 5 to 92.0, 6 to 76.0)
    private val REV = 8000.0; private val PEAK_RPM = 6500.0; private val PEAK_W = 260.0

    private fun truePower(r: Double): Double {
        val x = (r - PEAK_RPM) / 2800.0
        return if (r < PEAK_RPM) PEAK_W * max(0.25, 1.0 - 0.9 * x * x) else PEAK_W * max(0.5, 1.0 - 0.7 * x * x)
    }

    private fun sim(throttle: Double = 1.0, shiftAt: Double = 7400.0, dt: Double = 1.0 / 60): Frame {
        class Row(val t: Double, val v: Double, val rpm: Double, val g: Int, val thr: Double)
        val rows = ArrayList<Row>(); var t = 0.0; var v = 6.0; var g = 1
        while (true) {
            val rpm = K[g]!! * v
            if (rpm >= shiftAt && g < 6) {
                repeat(15) { rows.add(Row(t, v, K[g + 1]!! * v, g + 1, throttle)); t += dt; v -= (C0 + C1 * v * v) * dt }
                g++; continue
            }
            rows.add(Row(t, v, rpm, g, throttle)); t += dt
            val f = truePower(rpm) / v - (C0 + C1 * v * v)
            v += f * dt
            if (rpm >= REV || f <= 0.02 || rows.size > 20000) break
        }
        repeat(600) {
            rows.add(Row(t, v, K[g]!! * v, g, 0.0)); t += dt
            v -= (C0 + C1 * v * v) * dt
            if (v < 15) return@repeat
        }
        val f = Frame(rows.size)
        f["t"] = DoubleArray(f.n) { rows[it].t }; f["speed_kmh"] = DoubleArray(f.n) { rows[it].v * 3.6 }
        f["rpm"] = DoubleArray(f.n) { rows[it].rpm }; f["gear"] = DoubleArray(f.n) { rows[it].g.toDouble() }
        f["throttle"] = DoubleArray(f.n) { rows[it].thr }; f["brake"] = DoubleArray(f.n)
        f["vel_x"] = DoubleArray(f.n) { rows[it].v }; f["vel_y"] = DoubleArray(f.n); f["vel_z"] = DoubleArray(f.n); f["ang_y"] = DoubleArray(f.n)
        for (s in listOf("fl", "fr", "rl", "rr")) f["tyre_slip_$s"] = DoubleArray(f.n) { 1.0 }
        f["rpm_limiter"] = DoubleArray(f.n) { REV }; f["boost"] = DoubleArray(f.n)
        return f
    }

    @Test fun rebuildsPowerCurveAndGearbox() {
        val res = Dyno.analyse(sim(), listOf(3.2, 2.1, 1.6, 1.25, 1.0, 0.82, 0.0, 0.0), MASS)
        assertTrue(res.msg, res.ok)
        assertEquals("fitted", res.resist.src)
        assertTrue(abs(res.resist.c1 - C1) / C1 < 0.15)
        assertTrue(abs(res.peakWkg - PEAK_W) / PEAK_W < 0.08)
        assertTrue(abs(res.peakRpm - PEAK_RPM) < 700)
        for (gd in res.gears) {
            assertTrue(abs(gd.k - K[gd.g]!!) / K[gd.g]!! < 0.02)
            assertTrue(abs(gd.topKmh - REV / K[gd.g]!! * 3.6) < 3.0)
        }
    }

    @Test fun withoutWeightReportsPowerToWeight() {
        val res = Dyno.analyse(sim())
        assertTrue(res.ok); assertNull(res.mass); assertEquals("hp/t", res.unitP)
    }

    @Test fun shiftPointIsAfterThePowerPeak() {
        val res = Dyno.analyse(sim())
        val opts = res.gears.mapNotNull { it.optShift }
        assertTrue(opts.isNotEmpty() && opts.all { res.peakRpm <= it && it <= res.revLim + 1 })
    }

    @Test fun unusableLapsSayWhy() {
        assertFalse(Dyno.analyse(null).ok)
        assertFalse(Dyno.analyse(sim().slice(0, 50)).ok)
        assertTrue(Dyno.analyse(sim(thrbl())).msg.contains("full-throttle"))
    }

    private fun thrbl() = 0.2
}
