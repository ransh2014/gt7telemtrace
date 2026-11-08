package app.trace.telemetry

import org.junit.Assert.*
import org.junit.Rule
import org.junit.Test
import org.junit.rules.TemporaryFolder
import kotlin.math.abs
import kotlin.math.max

/**
 * Dyno.kt against the real gt7telem Python dyno.py on the same simulated car (tests/test_dyno.py's sim():
 * a car whose true power curve and gearbox are known). The expected numbers below were printed by the
 * Python code, so the Android port has to agree with the PC app, not just with the physics.
 */
class DynoGoldenTest {
    @get:Rule val tmp = TemporaryFolder()

    private val MASS = 1400.0
    private val C0 = 0.12
    private val C1 = 3e-4
    private val K = mapOf(1 to 300.0, 2 to 200.0, 3 to 150.0, 4 to 115.0, 5 to 92.0, 6 to 76.0)
    private val REV = 8000.0
    private val PEAK_RPM = 6500.0
    private val PEAK_W = 260.0
    private val GR = listOf(3.2, 2.1, 1.6, 1.25, 1.0, 0.82, 0.0, 0.0)

    private fun truePower(r: Double): Double {
        val x = (r - PEAK_RPM) / 2800.0
        return if (r < PEAK_RPM) PEAK_W * max(0.25, 1.0 - 0.9 * x * x) else PEAK_W * max(0.5, 1.0 - 0.7 * x * x)
    }

    /** tests/test_dyno.py sim(), row for row. */
    private fun sim(shiftAt: Double = 7400.0, dt: Double = 1.0 / 60): Frame {
        class Row(val t: Double, val v: Double, val rpm: Double, val g: Int, val thr: Double)
        val rows = ArrayList<Row>()
        var t = 0.0; var v = 6.0; var g = 1
        while (true) {
            val rpm = K[g]!! * v
            if (rpm >= shiftAt && g < 6) {
                repeat(15) {
                    rows.add(Row(t, v, K[g + 1]!! * v, g + 1, 1.0)); t += dt
                    v -= (C0 + C1 * v * v) * dt
                }
                g += 1
                continue
            }
            rows.add(Row(t, v, rpm, g, 1.0)); t += dt
            val f = truePower(rpm) / v - (C0 + C1 * v * v)
            v += f * dt
            if (rpm >= REV || f <= 0.02 || rows.size > 20000) break
        }
        repeat(600) {
            rows.add(Row(t, v, K[g]!! * v, g, 0.0)); t += dt
            v -= (C0 + C1 * v * v) * dt
            if (v < 15) return@repeat
        }
        // python breaks out of the coast loop at v < 15; replicate by trimming after the first row below 15 m/s
        val n0 = rows.size
        var cut = n0
        var seenCoast = false
        for (i in 0 until n0) { if (rows[i].thr == 0.0) { seenCoast = true } }
        if (seenCoast) {
            // find the coast rows and stop at the first one whose *next* speed fell under 15
            val firstCoast = rows.indexOfFirst { it.thr == 0.0 }
            var vv = rows[firstCoast].v
            var i = firstCoast
            while (i < n0) {
                vv -= (C0 + C1 * vv * vv) * dt
                if (vv < 15) { cut = i + 1; break }
                i++
            }
        }
        val used = rows.subList(0, cut)
        val f = Frame(used.size)
        f["t"] = DoubleArray(used.size) { used[it].t }
        f["speed_kmh"] = DoubleArray(used.size) { used[it].v * 3.6 }
        f["rpm"] = DoubleArray(used.size) { used[it].rpm }
        f["gear"] = DoubleArray(used.size) { used[it].g.toDouble() }
        f["throttle"] = DoubleArray(used.size) { used[it].thr }
        f["brake"] = DoubleArray(used.size)
        f["vel_x"] = DoubleArray(used.size) { used[it].v }
        f["vel_y"] = DoubleArray(used.size)
        f["vel_z"] = DoubleArray(used.size)
        f["ang_y"] = DoubleArray(used.size)
        for (s in listOf("fl", "fr", "rl", "rr")) f["tyre_slip_$s"] = DoubleArray(used.size) { 1.0 }
        f["rpm_limiter"] = DoubleArray(used.size) { REV }
        f["boost"] = DoubleArray(used.size)
        return f
    }

    private fun near(exp: Double, got: Double?, what: String, rel: Double = 1e-6) {
        assertNotNull(what, got)
        assertEquals(what, exp, got!!, 1e-9 + rel * abs(exp))
    }

    @Test fun normalLapMatchesPython() {
        val df = sim()
        assertEquals("rows", 4385, df.n)
        val r = Dyno.analyse(df, GR, MASS, 0.33)
        assertTrue(r.msg, r.ok)
        assertEquals(3630, r.nSamples)
        near(8000.0, r.revLim, "revLim")
        assertEquals("fitted", r.resist.src)
        near(0.16474821352764227, r.resist.c0, "c0", 1e-5)
        near(0.0002913412514973429, r.resist.c1, "c1", 1e-5)
        near(6500.0, r.peakRpm, "peakRpm")
        near(258.3961867672967, r.peakWkg, "peakWkg")
        near(5800.0, r.peakTqRpm, "peakTqRpm")
        near(0.406737673518216, r.peakTqNmkg, "peakTq")
        near(3100.0, r.coverLo, "coverLo"); near(7500.0, r.coverHi, "coverHi")
        near(346.5149346483796, r.hpPerTonne, "hp/t")
        assertEquals(45, r.rpm.size)
        near(65.8656560471525, r.pWkg[0], "p[0]")
        near(216.41792193496065, r.pWkg[r.rpm.size / 2], "p[mid]")
        near(485.12090850773143, r.peakPDisp, "peakPDisp")
        near(569.4327429255023, r.peakTDisp, "peakTDisp")
        assertEquals("hp", r.unitP); assertEquals("Nm", r.unitT)
        assertEquals(6, r.nGears)
        val top = r.top!!
        near(334.9932707520304, top.kmh, "top.kmh"); assertEquals("drag", top.by); assertEquals(6, top.gear)

        val expShift = mapOf(1 to (8000.0 to 5333.333333333333), 2 to (8000.0 to 6000.0), 3 to (7475.0 to 5730.833333333334),
            4 to (7275.0 to 5820.0), 5 to (7150.0 to 5906.521739130435))
        val expYours = mapOf(1 to 7358.285949085205, 2 to 7383.400847831568, 3 to 7399.659163626034, 4 to 7396.071833475293, 5 to 7398.700532975472)
        val expShare = mapOf(1 to 0.03010262257696693, 2 to 0.026225769669327253, 3 to 0.036716077537058155,
            4 to 0.06590649942987457, 5 to 0.13432155074116306, 6 to 0.70672748004561)
        val expFd = mapOf(1 to 3.239767424014474, 2 to 3.2911923037607353, 3 to 3.239767424014474, 4 to 3.179291765432871,
            5 to 3.1792917654328705, 6 to 3.202892022440326)
        assertEquals(6, r.gears.size)
        for (gd in r.gears) {
            near(K[gd.g]!!, gd.k, "k${gd.g}"); assertEquals("measured", gd.src)
            near(GR[gd.g - 1], gd.ratio, "ratio${gd.g}")
            near(3.6 * 1000.0 / K[gd.g]!!, gd.kmhPer1000, "kmh/1000 ${gd.g}")
            near(REV / K[gd.g]!! * 3.6, gd.topKmh, "top${gd.g}")
            near(expShare[gd.g]!!, gd.share, "share${gd.g}")
            near(expFd[gd.g]!!, gd.finalDrive, "finalDrive${gd.g}", 1e-5)
            val es = expShift[gd.g]
            if (es == null) { assertNull(gd.optShift); assertNull(gd.landRpm); assertNull(gd.yourShift) }
            else {
                near(es.first, gd.optShift, "opt${gd.g}"); near(es.second, gd.landRpm, "land${gd.g}")
                near(expYours[gd.g]!!, gd.yourShift, "yours${gd.g}")
            }
        }
        val c1 = r.curves[1]!!
        near(37.2, c1.first.first(), "curve1 x0"); near(0.649754918093642, c1.second.first(), "curve1 y0")
        near(96.0, c1.first.last(), "curve1 x-1"); near(0.9277656110241034, c1.second.last(), "curve1 y-1")
        assertTrue(Dyno.advise(df, r, GR).isEmpty())
    }

    @Test fun withoutWeightReportsPowerToWeight() {
        val r = Dyno.analyse(sim())
        assertTrue(r.ok); assertNull(r.mass); assertEquals("hp/t", r.unitP)
        near(PEAK_W * 1000 / Dyno.HP_W, r.peakPDisp, "peak hp/t", 0.08)
    }

    @Test fun earlyShiftingHintsMatchPython() {
        val df = sim(shiftAt = 5200.0)
        val r = Dyno.analyse(df, GR, MASS, 0.33)
        assertTrue(r.msg, r.ok)
        assertEquals(4347, r.nSamples)
        near(3400.0, r.coverLo, "coverLo"); near(7200.0, r.coverHi, "coverHi")
        assertEquals(39, r.rpm.size)
        val h = Dyno.advise(df, r, GR)
        assertEquals(3, h.size)
        assertEquals(listOf(2 to "Gearing", 1 to "Dyno", 1 to "Shifting"), h.map { it.sev to it.area })
        assertEquals("34% of your full-throttle time was below 85% of peak power.", h[0].finding)
        assertEquals("The curve stops at 7200 rpm: you never held full throttle higher (limiter ~8000).", h[1].finding)
        assertEquals("You upshift about 2817 rpm earlier than the curve says is best.", h[2].finding)
    }

    @Test fun unusableLapsSayWhy() {
        assertFalse(Dyno.analyse(null).ok)
        val s = sim()
        assertFalse(Dyno.analyse(s.slice(0, 50)).ok)
        s["throttle"] = DoubleArray(s.n) { 0.2 }
        assertTrue(Dyno.analyse(s).msg.contains("full-throttle"))
    }

    @Test fun understeerOnEntryIsFlagged() {
        val df = sim()
        val n = df.n
        df["wheel_base"] = DoubleArray(n) { 2.6 }
        df["ang_y"] = DoubleArray(n) { 0.25 }
        val neutral = DoubleArray(n) { 2.6 * 0.25 / max(df["speed_kmh"][it] / 3.6, 1.0) }
        df["wheel_steering_angle_l"] = DoubleArray(n) { neutral[it] * 2.0 }
        df["wheel_steering_angle_r"] = DoubleArray(n) { neutral[it] * 2.0 }
        df["throttle"] = DoubleArray(n)
        df["brake"] = DoubleArray(n) { 0.4 }
        val hints = Dyno.advise(df, null)
        assertTrue(hints.any { it.area == "Balance" && it.finding.contains("entry") })
    }

    @Test fun weightRoundTrip() {
        val f = tmp.newFile("car_weights.json").also { it.delete() }
        assertNull(Dyno.loadWeight(f, "Some Car"))
        assertTrue(Dyno.saveWeight(f, "Some Car", 1234.5))
        assertEquals(1234.5, Dyno.loadWeight(f, "some car")!!, 1e-9)
        assertFalse(Dyno.saveWeight(f, "Some Car", 12.0))
    }
}
