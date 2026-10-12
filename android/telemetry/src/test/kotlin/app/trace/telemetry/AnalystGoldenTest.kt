package app.trace.telemetry

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.BeforeClass
import org.junit.Test
import java.io.InputStream
import java.util.zip.GZIPInputStream
import kotlin.math.abs

/** Everything here is compared with output of the real gt7telem Python code (tools/make_fixtures3.py). */
class AnalystGoldenTest {
    companion object {
        private fun res(name: String): InputStream = GZIPInputStream(AnalystGoldenTest::class.java.getResourceAsStream("/$name.json.gz")!!)
        lateinit var g: JSONObject
        lateinit var a: LapFile; lateinit var b: LapFile; lateinit var c: LapFile
        lateinit var race: RaceFile
        @BeforeClass @JvmStatic fun load() {
            g = JSONObject(res("golden3").readBytes().toString(Charsets.UTF_8))
            a = LapLoader.load(JsonColumns.read(res("lapA"))); b = LapLoader.load(JsonColumns.read(res("lapB"))); c = LapLoader.load(JsonColumns.read(res("lapC")))
            race = RaceLoader.load(JsonColumns.read(res("race")))
        }
    }

    private fun near(exp: Double, got: Double, what: String) {
        if (exp.isNaN()) { assertTrue("$what expected NaN, got $got", got.isNaN()); return }
        assertEquals(what, exp, got, 1e-9 + 1e-9 * abs(exp))
    }
    private fun arr(j: JSONArray) = DoubleArray(j.length()) { if (j.isNull(it)) Double.NaN else j.getDouble(it) }
    private fun cmp(j: JSONArray, got: DoubleArray, what: String) {
        assertEquals("$what length", j.length(), got.size)
        for (i in 0 until j.length()) near(if (j.isNull(i)) Double.NaN else j.getDouble(i), got[i], "$what[$i]")
    }
    private fun optD(o: JSONObject, k: String): Double? = if (o.isNull(k)) null else o.getDouble(k)
    private fun cmpOpt(e: Double?, got: Double?, what: String) { if (e == null) assertNull(what, got) else { assertNotNull(what, got); near(e, got!!, what) } }

    @Test fun lapDerivedColumns() {
        val ga = g.getJSONObject("lapA")
        assertEquals(ga.getInt("n"), a.frame.n)
        assertEquals(ga.getBoolean("compact"), a.frame.compact)
        val d = ga.getJSONObject("derived")
        for (k in d.keys()) cmp(d.getJSONArray(k), a.frame[k], "A.$k")
    }

    @Test fun labelsAndStats() {
        val ga = g.getJSONObject("lapA")
        assertEquals(ga.getString("label"), LapMath.lapLabel(a.meta))
        assertEquals(ga.getString("label_short"), LapMath.lapLabel(a.meta, true))
        assertEquals(g.getString("label_old"), LapMath.lapLabel(a.meta + ("physics_era" to "1.70")))
        val st = ga.getJSONObject("stats"); val got = LapMath.buildStats(a.meta, a.frame)
        assertEquals(st.length(), got.size)
        for ((k, v) in got) assertEquals(k, st.getString(k), v)
        val ss = ga.getJSONArray("sector_stats"); val gs = LapMath.buildSectorStats(a.frame)
        assertEquals(ss.length(), gs.size)
        for (i in gs.indices) for (q in 0 until 4) assertEquals("S$i.$q", ss.getJSONArray(i).getString(q), gs[i][q])
        assertEquals(if (g.isNull("lap_fuel_used")) null else g.getString("lap_fuel_used"), LapMath.lapFuelUsed(a.frame))
    }

    @Test fun sectorsAndDeltas() {
        val s = g.getJSONObject("sectors10"); val got = LapMath.lapSectors(a.frame, 10)
        for (i in 0 until 10) assertEquals(s.getJSONArray("counts").getInt(i), got.segs[i].size)
        cmp(s.getJSONArray("times"), got.times, "sector times")
        for (i in 0 until 10) near(s.getJSONArray("avg").getDouble(i), got.segs[i].let { seg -> seg.sumOf { a.frame["speed_kmh"][it] } / seg.size }, "avg $i")
        cmp(g.getJSONArray("sector_deltas"), LapMath.computeSectorDeltas(a.frame, b.frame, 25.0), "sector deltas")
    }

    @Test fun ghostLap() {
        val raw = JsonColumns.read(res("ghost")); val gh = LapLoader.load(raw)
        val j = g.getJSONObject("ghost")
        assertEquals(j.getInt("n"), gh.frame.n); assertTrue(gh.frame.compact); assertEquals(j.getBoolean("t_derived"), gh.frame.tDerived)
        cmp(j.getJSONArray("t"), gh.frame["t"], "ghost t"); cmp(j.getJSONArray("track_position"), gh.frame["track_position"], "ghost pos")
        cmp(j.getJSONArray("long_g"), gh.frame["long_g"], "ghost long_g")
    }

    private fun cornerEq(j: JSONObject, c: Corners.Corner, what: String) {
        assertEquals(j.getInt("number"), c.number)
        near(j.getDouble("start"), c.start, "$what.start"); near(j.getDouble("end"), c.end, "$what.end"); near(j.getDouble("apex"), c.apex, "$what.apex")
        cmpOpt(optD(j, "brake_pos"), c.brakePos, "$what.brake"); cmpOpt(optD(j, "throttle_pos"), c.throttlePos, "$what.thr")
        near(j.getDouble("min_speed"), c.minSpeed, "$what.min"); near(j.getDouble("brake_peak"), c.brakePeak, "$what.peak"); assertEquals(j.getBoolean("trail"), c.trail)
    }

    @Test fun corners() {
        val ca = Corners.detectCorners(a.frame); val j = g.getJSONArray("corners_A")
        assertEquals(j.length(), ca.size); assertTrue(ca.size >= 4)
        for (i in ca.indices) cornerEq(j.getJSONObject(i), ca[i], "A$i")
        val m = Corners.matchCorners(a.frame, b.frame); val gm = g.getJSONObject("match_A_B")
        assertEquals(gm.getJSONArray("a").length(), m.a.size); assertEquals(gm.getJSONArray("b").length(), m.b.size)
        for (i in m.b.indices) cornerEq(gm.getJSONArray("b").getJSONObject(i), m.b[i], "B$i")
        val dj = gm.getJSONArray("deltas"); assertEquals(dj.length(), m.deltas.size)
        for (i in m.deltas.indices) {
            val e = dj.getJSONObject(i); val d = m.deltas[i]
            assertEquals(e.getInt("number"), d.number); assertEquals(e.getInt("ref_number"), d.refNumber)
            cmpOpt(optD(e, "brake_m"), d.brakeM, "d$i.brake"); near(e.getDouble("min_speed_kmh"), d.minSpeedKmh, "d$i.min")
            cmpOpt(optD(e, "throttle_m"), d.throttleM, "d$i.thr"); cmpOpt(optD(e, "time_s"), d.timeS, "d$i.time")
        }
        assertEquals(gm.getString("label"), Corners.sessionLabel(a.frame, m.a, m.deltas))
        assertEquals(gm.getString("loss"), Corners.describeTimeLoss(m.deltas))
        assertEquals(g.getString("label_noref"), Corners.sessionLabel(a.frame, ca, emptyList()))
    }

    @Test fun theoreticalBest() {
        val r = Theoretical.theoreticalBest(listOf("Lap A" to a.frame, "Lap B" to b.frame, "Lap C" to c.frame))!!
        val t = g.getJSONObject("theory")
        assertEquals(t.getJSONArray("labels").toList(), r.labels)
        cmp(t.getJSONArray("totals"), r.totals, "totals"); assertEquals(t.getInt("best_idx"), r.bestIdx)
        near(t.getDouble("actual"), r.actual, "actual"); near(t.getDouble("theoretical"), r.theoretical, "theo"); near(t.getDouble("gap"), r.gap, "gap")
        cmp(t.getJSONArray("centers"), r.centers, "centers"); cmp(t.getJSONArray("loss"), r.loss, "loss"); cmp(t.getJSONArray("cum_gap"), r.cumGap, "cum")
        for (i in r.donor.indices) assertEquals("donor $i", t.getJSONArray("donor").getInt(i), r.donor[i])
        assertEquals(t.getInt("skipped"), r.skipped)
        val z = t.getJSONArray("zones"); assertEquals(z.length(), r.zones.size)
        for (i in r.zones.indices) { val e = z.getJSONObject(i); near(e.getDouble("start"), r.zones[i].start, "z$i.s"); near(e.getDouble("end"), r.zones[i].end, "z$i.e"); near(e.getDouble("loss"), r.zones[i].loss, "z$i.l"); assertEquals(e.getInt("donor"), r.zones[i].donor) }
        assertNull(Theoretical.theoreticalBest(listOf("Lap A" to a.frame)))
    }

    @Test fun ratingsStyleSectors() {
        val sa = Scores.lapScores(a.frame); val ea = g.getJSONArray("scores_A")
        for (i in 0 until 7) near(ea.getDouble(i), sa[i], "score A $i")
        val sb = Scores.lapScores(b.frame); val eb = g.getJSONArray("scores_B")
        for (i in 0 until 7) near(eb.getDouble(i), sb[i], "score B $i")
        val st = Scores.styleMetrics(a.frame); val es = g.getJSONArray("style_A")
        near(es.getDouble(0), st.aggression, "aggr"); near(es.getDouble(1), st.balance, "bal"); assertEquals(es.getInt(2), st.cornerRows.size)
        val zs = Scores.brakeZoneDurations(a.frame); val ez = g.getJSONArray("brake_zones_A")
        assertEquals(ez.length(), zs.size); for (i in zs.indices) near(ez.getDouble(i), zs[i], "zone $i")
        val ss = Scores.sectorStats(a.frame); val e = g.getJSONObject("sector_stats_A")
        cmp(e.getJSONArray("times"), ss.times, "s.times"); cmp(e.getJSONArray("avg"), ss.avg, "s.avg"); cmp(e.getJSONArray("top"), ss.top, "s.top")
        cmp(e.getJSONArray("brk"), ss.brk, "s.brk"); cmp(e.getJSONArray("thr"), ss.thr, "s.thr"); cmp(e.getJSONArray("latg"), ss.latg, "s.latg")
    }

    @Test fun teleDiff() {
        val ax = TeleDiff.byPosition(a.frame, b.frame); val e = g.getJSONObject("telediff")
        val ta = e.getJSONArray("ta_s"); for (i in 0 until ta.length()) near(ta.getDouble(i), ax.x[i], "ta_s $i")
        cmp(e.getJSONArray("d_speed"), TeleDiff.diff(ax, "speed_kmh"), "d_speed"); cmp(e.getJSONArray("d_t"), TeleDiff.diff(ax, "t"), "d_t")
    }

    @Test fun csvAndFormats() {
        val lines = LapMath.exportCsv(a.frame).split("\r\n")
        assertEquals(g.getInt("lap_csv_n"), lines.size)
        val e = g.getJSONArray("lap_csv_lines"); for (i in 0 until e.length()) assertEquals("lap csv $i", e.getString(i), lines[i])
        val rl = RaceLoader.exportCsv(race.frame).split("\n")
        assertEquals(g.getInt("race_csv_n"), rl.size)
        val re = g.getJSONArray("race_csv_lines"); for (i in 0 until re.length()) assertEquals("race csv $i", re.getString(i), rl[i])
        val f = g.getJSONObject("fmt")
        val lt = listOf<Any?>(0, 59.9996, 61.0004, 125.1234, null, Double.NaN, 3599.9999).map { LapMath.fmtLapTime(it) }
        assertEquals(f.getJSONArray("laptime").toList(), lt)
        val du = listOf<Any?>(0, 59.9996, 61.0004, 1253.4567, null, -1, "x").map { RaceLoader.fmtDur(it) }
        assertEquals(f.getJSONArray("dur").toList(), du)
        assertEquals("1e-05", LapMath.pyRepr(0.00001)); assertEquals("0.0001", LapMath.pyRepr(0.0001)); assertEquals("12.5", LapMath.pyRepr(12.5))
        assertEquals("1.5e+16", LapMath.pyRepr(1.5e16)); assertEquals("-0.5", LapMath.pyRepr(-0.5)); assertEquals("100.0", LapMath.pyRepr(100.0))
    }

    @Test fun raceLoadAndStats() {
        val r = g.getJSONObject("race"); val f = race.frame
        assertEquals(r.getInt("n"), f.n)
        val d = r.getJSONObject("derived"); for (k in d.keys()) cmp(d.getJSONArray(k), f[k], "race.$k")
        assertEquals(r.getString("label"), RaceLoader.raceLabel(race.meta)); assertEquals(r.getString("label_short"), RaceLoader.raceLabel(race.meta, true))
        val st = r.getJSONObject("stats"); val got = RaceLoader.buildStats(race.meta, f)
        assertEquals(st.length(), got.size); for ((k, v) in got) assertEquals(k, st.getString(k), v)
        val sp = r.getJSONArray("splits"); val gs = RaceLoader.lapSplitStats(f); assertEquals(sp.length(), gs.size)
        for (i in gs.indices) { val e = sp.getJSONObject(i)
            assertEquals(e.getInt("lap"), gs[i].lap); near(e.getDouble("time_s"), gs[i].timeS, "split $i time"); assertEquals(e.getBoolean("complete"), gs[i].complete)
            near(e.getDouble("avg_speed"), gs[i].avgSpeed, "avg"); near(e.getDouble("top_speed"), gs[i].topSpeed, "top"); near(e.getDouble("brake_pct"), gs[i].brakePct, "brk")
            near(e.getDouble("throttle_pct"), gs[i].throttlePct, "thr"); near(e.getDouble("max_lat_g"), gs[i].maxLatG, "lat") }
        assertEquals(r.getInt("pit_stops"), RaceLoader.countPitStops(f))
        val fl = RaceLoader.fuelPerLapStats(f); val fe = r.getJSONArray("fuel_lap")
        near(fe.getDouble(0), fl.perLap!!, "fuel/lap"); near(fe.getDouble(1), fl.lapsPerTank!!, "laps/tank"); assertEquals(fe.getInt(2), fl.lapsUsed)
        val rs = RaceLoader.refuelSpans(f["fuel_remaining"]); val re = r.getJSONArray("refuel_spans")
        assertEquals(re.length(), rs.size); for (i in rs.indices) { assertEquals(re.getJSONArray(i).getInt(0), rs[i].first); assertEquals(re.getJSONArray(i).getInt(1), rs[i].second) }
        val segs = RaceLoader.lapSegments(f); val se = r.getJSONArray("segments"); assertEquals(se.length(), segs.size)
        for (i in segs.indices) { assertEquals(se.getJSONArray(i).getInt(0), segs[i].first); assertEquals(se.getJSONArray(i).getInt(1), segs[i].last) }
        val lf = RaceLoader.raceLapFrames(f); val le = r.getJSONArray("lap_frames"); assertEquals(le.length(), lf.size)
        for (i in lf.indices) { assertEquals(le.getJSONArray(i).getInt(0), lf[i].lap); near(le.getJSONArray(i).getDouble(1), lf[i].timeS, "lf t"); assertEquals(le.getJSONArray(i).getInt(2), lf[i].frame.n) }
        val bl = RaceLoader.bestLapBaseline(f); val be = g.getJSONObject("race_baseline")
        assertEquals(be.getString("label"), bl.label); assertEquals(be.getInt("ref_len"), bl.frame!!.n)
        val rsc = Scores.raceScores(f); val rse = g.getJSONArray("race_scores"); for (i in 0 until 7) near(rse.getDouble(i), rsc[i], "race score $i")
    }

    @Test fun raceCornersAndConsistency() {
        val f = race.frame; val bl = RaceLoader.bestLapBaseline(f)
        val laps = RaceLoader.raceLapFrames(f)
        val sc = Corners.scoreLaps(laps.map { it.lap to it.frame }, bl.frame!!)
        val e = g.getJSONObject("race_corners")
        assertEquals(e.getJSONArray("ref").length(), sc.refCorners.size)
        for (i in sc.refCorners.indices) cornerEq(e.getJSONArray("ref").getJSONObject(i), sc.refCorners[i], "ref$i")
        val se = e.getJSONArray("scored"); assertEquals(se.length(), sc.perLap.size)
        for (i in sc.perLap.indices) {
            val row = se.getJSONArray(i); assertEquals(row.getInt(0), sc.perLap[i].first)
            val dm = row.getJSONObject(1); assertEquals(dm.length(), sc.perLap[i].second.size)
            for ((k, d) in sc.perLap[i].second) near(dm.getJSONObject(k.toString()).getDouble("time_s"), d.timeS!!, "scored $i.$k")
        }
        val sp = Corners.cornerSpreads(sc.perLap); val spe = e.getJSONArray("spreads"); assertEquals(spe.length(), sp.size)
        for (i in sp.indices) { val s = spe.getJSONObject(i); assertEquals(s.getInt("ref_number"), sp[i].refNumber); assertEquals(s.getInt("n"), sp[i].n)
            near(s.getDouble("mean"), sp[i].meanTimeS, "mean"); near(s.getDouble("std"), sp[i].stdTimeS, "std"); cmpOpt(optD(s, "std_brake"), sp[i].stdBrakeM, "stdb") }
        val st = Consistency.lapTimeStats(laps.map { it.timeS })!!; val ce = g.getJSONObject("consistency").getJSONObject("stats")
        assertEquals(ce.getInt("n"), st.n); assertEquals(ce.getInt("n_clean"), st.nClean); near(ce.getDouble("best"), st.best, "best"); near(ce.getDouble("avg"), st.avg, "avg")
        near(ce.getDouble("std"), st.std, "std"); near(ce.getDouble("spread"), st.spread, "spread"); cmpOpt(optD(ce, "trend"), st.trend, "trend")
        if (ce.isNull("score")) assertNull(st.score) else assertEquals(ce.getInt("score"), st.score)
        val cs = g.getJSONObject("consistency")
        val inputs = listOf(listOf(9.0, 100.0, 100.5, 101.0, 100.2, 160.0), listOf(100.0, 101.0), listOf(100.0))
        for ((i, inp) in inputs.withIndex()) {
            val got = Consistency.lapTimeStats(inp); val ex = cs.getJSONArray("samples")
            if (ex.isNull(i)) assertNull(got) else {
                val o = ex.getJSONObject(i)
                assertEquals(o.getInt("n"), got!!.n); assertEquals(o.getInt("n_clean"), got.nClean); near(o.getDouble("std"), got.std, "s$i.std"); cmpOpt(optD(o, "trend"), got.trend, "s$i.trend")
                if (o.isNull("score")) assertNull(got.score) else assertEquals(o.getInt("score"), got.score)
            }
        }
        val labels = listOf(null, 0.01, 0.2, -0.4).map { Consistency.trendLabel(it) }
        assertEquals(cs.getJSONArray("trend_labels").toList(), labels)
    }
}
