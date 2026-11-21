package app.trace.telemetry

import org.json.JSONArray
import org.json.JSONObject
import org.junit.Assert.*
import org.junit.BeforeClass
import org.junit.Test
import java.io.File
import java.io.InputStream
import java.time.LocalDate
import java.util.zip.GZIPInputStream
import kotlin.math.abs

/**
 * The 1.2.0 tools ported to Kotlin (Coach, SetupAdvisor, Engineer, DriverDna, Planner, Validate, Groups, DynoCompare,
 * ReplayHtml and the report texts) against output of the REAL gt7telem Python code (android/tools/make_fixtures4.py ->
 * golden4.json.gz). Floats are compared to 1e-6, strings and codes exactly. Regenerate the golden file after changing
 * any of those Python modules:  python android/tools/make_fixtures4.py
 */
class Golden4Test {
    companion object {
        private fun res(name: String): InputStream = GZIPInputStream(Golden4Test::class.java.getResourceAsStream("/$name.json.gz")!!)
        lateinit var g: JSONObject
        lateinit var a: LapFile; lateinit var b: LapFile; lateinit var c: LapFile
        lateinit var race: RaceFile
        val syn = HashMap<String, Frame>()

        @BeforeClass @JvmStatic fun load() {
            g = JSONObject(res("golden4").readBytes().toString(Charsets.UTF_8))
            a = LapLoader.load(JsonColumns.read(res("lapA"))); b = LapLoader.load(JsonColumns.read(res("lapB"))); c = LapLoader.load(JsonColumns.read(res("lapC")))
            race = RaceLoader.load(JsonColumns.read(res("race")))
            val s = g.getJSONObject("synth")
            for (k in s.keys()) syn[k] = frameOf(s.getJSONObject(k).getJSONObject("cols"))
        }

        fun frameOf(cols: JSONObject): Frame {
            val keys = cols.keys().asSequence().toList()
            val n = cols.getJSONArray(keys[0]).length()
            val f = Frame(n)
            for (k in keys) { val arr = cols.getJSONArray(k); f[k] = DoubleArray(n) { if (arr.isNull(it)) Double.NaN else arr.getDouble(it) } }
            return f
        }
        fun lap(name: String): Frame = when (name) { "A" -> a.frame; "B" -> b.frame; "C" -> c.frame; else -> syn[name]!! }
    }

    private fun near(exp: Double, got: Double, what: String) {
        if (exp.isNaN()) { assertTrue("$what expected NaN, got $got", got.isNaN()); return }
        assertEquals(what, exp, got, 1e-6 + 1e-6 * abs(exp))
    }
    private fun optD(o: JSONObject, k: String): Double? = if (o.isNull(k)) null else o.getDouble(k)
    private fun optOf(a: JSONArray, i: Int): Double? = if (a.isNull(i)) null else a.getDouble(i)
    private fun nearOpt(exp: Double?, got: Double?, what: String) { if (exp == null) assertNull(what, got) else { assertNotNull(what, got); near(exp, got!!, what) } }
    private fun strings(j: JSONArray) = List(j.length()) { j.getString(it) }

    // ---------------------------------------------------------------- lap-sharing payloads
    @Test fun shareLapPayloads() {
        val sh = g.getJSONObject("share")
        var built = 0
        for (key in sh.keys()) {
            if (key == "ids") continue
            val c = sh.getJSONObject(key)
            val mj = c.getJSONObject("meta")
            val meta: Meta = mj.keys().asSequence().associateWith { k -> mj.get(k) }
            val df = frameOf(c.getJSONObject("cols"))
            val exp = if (c.isNull("problem")) null else c.getString("problem")
            assertEquals("$key problem", exp, ShareLap.problem(meta, df))
            assertEquals("$key key", c.getString("key"), ShareLap.lapKey(meta))
            if (exp != null) { assertThrows(IllegalArgumentException::class.java) { ShareLap.payloadJson(meta, df) }; continue }
            val json = ShareLap.payloadJson(meta, df)
            assertEquals("$key payload is byte-identical to the Python one", c.getString("json"), json)
            assertEquals("$key n", c.getInt("n"), JSONObject(json).getInt("n"))
            val blob = ShareLap.gzip(json)
            assertEquals("$key gzip round trip", json, ShareLap.gunzip(blob))
            assertTrue("$key gzip magic", (blob[0].toInt() and 0xff) == 0x1f && (blob[1].toInt() and 0xff) == 0x8b)
            assertTrue("$key base64 decodes back", java.util.Base64.getDecoder().decode(ShareLap.base64(blob)).contentEquals(blob))
            built++
        }
        assertTrue("built $built payloads", built >= 5)
        val ids = sh.getJSONObject("ids")
        assertEquals(ids.getString("link"), ShareLap.linkFor(ids.getString("good")))
        val links = ids.getJSONArray("links")
        for (i in 0 until links.length()) {
            val p = links.getJSONArray(i)
            assertEquals("idFrom ${p.getString(0)}", if (p.isNull(1)) null else p.getString(1), ShareLap.idFrom(p.getString(0)))
        }
        assertThrows(IllegalArgumentException::class.java) { ShareLap.linkFor("nope") }
    }

    @Test fun shareLapGunzipRefusesABomb() {
        val big = ByteArray(5_000_000) { 'a'.code.toByte() }
        val bo = java.io.ByteArrayOutputStream()
        java.util.zip.GZIPOutputStream(bo).use { it.write(big) }
        assertThrows(IllegalStateException::class.java) { ShareLap.gunzip(bo.toByteArray()) }
    }

    // ---------------------------------------------------------------- coach
    @Test fun coachTipsAndText() {
        val co = g.getJSONObject("coach")
        var withTips = 0
        for (key in co.keys()) {
            val (x, r) = key.split("_vs_")
            val exp = co.getJSONObject(key); val tips = Coach.analyse(lap(x), lap(r), maxTips = 5)
            val et = exp.getJSONArray("tips")
            assertEquals("$key tips", et.length(), tips.size)
            if (tips.isNotEmpty()) withTips++
            for (i in tips.indices) {
                val e = et.getJSONObject(i); val t = tips[i]
                assertEquals("$key[$i].corner", e.getInt("corner"), t.corner)
                near(e.getDouble("apex"), t.apex, "$key[$i].apex")
                nearOpt(optD(e, "brake_pos"), t.brakePos, "$key[$i].brake_pos")
                assertEquals(e.getString("kind"), t.kind); assertEquals(e.getString("text"), t.text); assertEquals(e.getString("cue"), t.cue)
                near(e.getDouble("time_s"), t.timeS, "$key[$i].time_s"); near(e.getDouble("est_s"), t.estS, "$key[$i].est_s")
            }
            assertEquals("$key describe", exp.getString("describe"), Coach.describe(tips))
        }
        assertTrue("the fixture pairs must exercise the tips", withTips >= 4)
    }

    @Test fun liveCoachCues() {
        val lc = g.getJSONObject("live_coach")
        val tips = List(lc.getJSONArray("tips").length()) { i ->
            val e = lc.getJSONArray("tips").getJSONObject(i)
            Coach.Tip(e.getInt("corner"), e.getDouble("apex"), optD(e, "brake_pos"), e.getString("kind"), e.getString("text"), e.getString("cue"), e.getDouble("time_s"), e.getDouble("est_s"))
        }
        val live = LiveCoach(tips)
        val seq = lc.getJSONArray("seq"); val cues = lc.getJSONArray("cues")
        var spoken = 0
        for (i in 0 until seq.length()) {
            val p = seq.getJSONArray(i)
            val got = live.update(p.getDouble(0), p.getDouble(1))
            val exp = if (cues.isNull(i)) null else cues.getString(i)
            assertEquals("step $i", exp, got)
            if (got != null) spoken++
        }
        assertTrue(spoken >= 4)
    }

    // ---------------------------------------------------------------- validate
    @Test fun validateRealLaps() {
        val v = g.getJSONObject("validate"); val lapS = numOf(a.meta["lap_time_s"])
        for ((k, f) in listOf("A" to a.frame, "B" to b.frame, "C" to c.frame)) {
            val e = v.getJSONObject(k); val r = Validate.check(f, null, lapS)
            assertEquals(k, e.getString("verdict"), r.verdict)
            assertEquals(k, e.getString("describe"), Validate.describe(r))
            assertEquals(k, e.getJSONArray("issues").length(), r.issues.size)
        }
    }

    @Test fun validateSyntheticCases() {
        val cases = g.getJSONArray("validate_cases")
        for (i in 0 until cases.length()) {
            val e = cases.getJSONObject(i)
            val f = frameOf(e.getJSONObject("cols"))
            val surf = e.getJSONArray("surface"); f.strs["surface_type"] = Array(f.n) { surf.getString(it) }
            val r = Validate.check(f, optD(e, "ref_len"), optD(e, "lap_s"))
            val name = e.getString("name")
            assertEquals(name, e.getString("verdict"), r.verdict)
            val ei = e.getJSONArray("issues")
            assertEquals("$name issues", ei.length(), r.issues.size)
            for (q in r.issues.indices) {
                val x = ei.getJSONArray(q)
                assertEquals(name, x.getString(0), r.issues[q].code); assertEquals(name, x.getString(1), r.issues[q].level); assertEquals(name, x.getString(2), r.issues[q].text)
            }
            assertEquals(name, e.getString("describe"), Validate.describe(r))
        }
    }

    // ---------------------------------------------------------------- setup advisor
    private fun checkFindings(exp: JSONArray, got: List<SetupAdvisor.Finding>, what: String) {
        assertEquals("$what findings", exp.length(), got.size)
        for (i in got.indices) {
            val e = exp.getJSONArray(i)
            assertEquals(e.getString(0), got[i].key); assertEquals(e.getString(1), got[i].phase); near(e.getDouble(2), got[i].share, "$what share $i")
            assertEquals(e.getString(3), got[i].severity); assertEquals(e.getString(4), got[i].text); assertEquals(strings(e.getJSONArray(5)), got[i].fixes)
        }
    }

    @Test fun setupAdvisorOnSavedLaps() {
        for ((k, f) in listOf("A" to a.frame, "B" to b.frame, "C" to c.frame)) {
            val e = g.getJSONObject("setup").getJSONObject(k); val r = SetupAdvisor.diagnose(f)
            assertEquals(k, e.getBoolean("ok"), r.ok); assertEquals(k, e.getString("describe"), SetupAdvisor.describe(r))
            checkFindings(e.getJSONArray("findings"), r.findings, k)
        }
        assertEquals("Needs a full recorded lap.", SetupAdvisor.describe(SetupAdvisor.diagnose(null)))
    }

    private fun variants(): List<Frame> { val v = g.getJSONArray("setup_variants"); return List(v.length()) { frameOf(v.getJSONObject(it).getJSONObject("cols")) } }

    @Test fun setupAdvisorBalanceAndFindings() {
        val vs = g.getJSONArray("setup_variants"); val fr = variants()
        for (i in fr.indices) {
            val e = vs.getJSONObject(i); val r = SetupAdvisor.diagnose(fr[i])
            assertEquals("variant $i", e.getString("describe"), SetupAdvisor.describe(r))
            checkFindings(e.getJSONArray("findings"), r.findings, "variant $i")
            val bal = e.getJSONObject("balance")
            for (ph in bal.keys()) {
                val eb = bal.getJSONObject(ph); val gb = r.balance[ph]!!
                near(eb.getDouble("under"), gb.under, "$i $ph under"); near(eb.getDouble("over"), gb.over, "$i $ph over"); assertEquals(eb.getInt("n"), gb.n)
            }
        }
    }

    @Test fun setupAdvisorReadsTheSavedLapColumns() {
        val e = g.getJSONObject("setup_split"); val r = SetupAdvisor.diagnose(frameOf(e.getJSONObject("cols")))
        assertEquals(e.getString("describe"), SetupAdvisor.describe(r))
        checkFindings(e.getJSONArray("findings"), r.findings, "split")
        assertTrue("balance must be measured from the _l/_r columns", r.balance.isNotEmpty())
    }

    @Test fun setupAdvisorAbCheck() {
        val fr = variants()
        val ab = SetupAdvisor.compareAb(fr.subList(0, 3), fr.subList(3, 6), listOf(90.0, 91.0, 90.5), listOf(90.2, 90.4, 90.1))
        val e = g.getJSONObject("setup_ab")
        assertEquals(e.getString("verdict"), ab.verdict); assertEquals(e.getString("text"), ab.text); nearOpt(optD(e, "lap_delta_s"), ab.lapDeltaS, "delta")
        assertEquals(e.getJSONArray("better").length(), ab.better.size); assertEquals(e.getJSONArray("worse").length(), ab.worse.size)
        val e2 = g.getJSONObject("setup_ab2")
        val ab2 = SetupAdvisor.compareAb(fr.subList(2, 4), fr.subList(0, 2))
        assertEquals(e2.getString("verdict"), ab2.verdict); assertEquals(e2.getString("text"), ab2.text); assertNull(ab2.lapDeltaS)
        val e3 = g.getJSONObject("setup_ab3"); val ab3 = SetupAdvisor.compareAb(fr.subList(0, 1), fr.subList(1, 2))
        assertEquals(e3.getString("text"), ab3.text); assertEquals(e3.getString("verdict"), ab3.verdict)
    }

    // ---------------------------------------------------------------- engineer
    private fun snapOf(o: JSONObject) = Engineer.Snap(optD(o, "laps_of_fuel"), optD(o, "pace_loss_s"), if (o.isNull("stop_needed")) null else o.getBoolean("stop_needed"))

    @Test fun engineerPitWindowAndCalls() {
        val arr = g.getJSONArray("engineer_window")
        assertTrue(arr.length() > 100)
        for (i in 0 until arr.length()) {
            val e = arr.getJSONObject(i); val s = snapOf(e.getJSONObject("snap")); val lap = e.getInt("lap"); val tot = e.getInt("total")
            val w = Engineer.pitWindow(lap, tot, s)
            assertEquals("#$i earliest", if (e.isNull("earliest")) null else e.getInt("earliest"), w.earliest)
            assertEquals("#$i latest", if (e.isNull("latest")) null else e.getInt("latest"), w.latest)
            assertEquals("#$i reason", e.getString("reason"), w.reason)
            val call = Engineer.pitCall(lap, tot, s)
            if (e.isNull("call")) assertNull("#$i call", call) else { assertEquals(e.getJSONArray("call").getString(0), call!!.first); assertEquals(e.getJSONArray("call").getString(1), call.second) }
            assertNull("#$i same lap", Engineer.pitCall(lap, tot, s, lastCalled = lap))
        }
        val f = g.getJSONArray("engineer_fresh").getJSONObject(0)
        val w = Engineer.pitWindow(f.getInt("lap"), f.getInt("total"), snapOf(f.getJSONObject("snap")), f.getDouble("pit_loss"), f.getDouble("gain"))
        val ew = f.getJSONArray("w")
        assertEquals(if (ew.isNull(0)) null else ew.getInt(0), w.earliest); assertEquals(if (ew.isNull(1)) null else ew.getInt(1), w.latest); assertEquals(ew.getString(2), w.reason)
    }

    @Test fun engineerUndercutOvercutAndFinish() {
        for ((key, over) in listOf("engineer_under" to false, "engineer_over" to true)) {
            val arr = g.getJSONArray(key)
            for (i in 0 until arr.length()) {
                val e = arr.getJSONObject(i); val r = e.getJSONArray("r")
                val gap = optD(e, "gap"); val laps = e.getInt("laps")
                val got = if (over) Engineer.overcut(gap, e.getDouble("loss"), laps) else Engineer.undercut(gap, e.getDouble("gain"), laps)
                assertEquals("$key#$i works", if (r.isNull(0)) null else r.getBoolean(0), got.works)
                nearOpt(optOf(r, 1), got.marginS, "$key#$i margin"); assertEquals("$key#$i text", r.getString(2), got.text)
            }
        }
        val sims = g.getJSONArray("engineer_sim")
        for (i in 0 until sims.length()) {
            val e = sims.getJSONObject(i)
            val gaps = if (e.isNull("gaps")) null else List(e.getJSONArray("gaps").length()) { e.getJSONArray("gaps").getDouble(it) }
            val r = Engineer.simulateFinish(4, 8, 5, 12, 91.4, e.getInt("pits"), 24.0, gaps)
            near(e.getDouble("cost"), r.costS, "sim#$i cost"); assertEquals("sim#$i finish", if (e.isNull("finish")) null else e.getInt("finish"), r.finish)
            assertEquals("sim#$i", e.getString("text"), r.text)
        }
    }

    // ---------------------------------------------------------------- driver DNA
    private fun checkTraits(exp: JSONObject, got: DnaTraits, what: String) {
        for (k in DriverDna.TRAITS) nearOpt(optD(exp, k), got[k], "$what.$k")
    }

    @Test fun driverDnaProfiles() {
        val d = g.getJSONObject("dna")
        val p3 = DriverDna.profile(a.frame, listOf(b.frame, c.frame))!!
        checkTraits(d.getJSONObject("p3"), p3, "p3")
        assertNotNull(p3["precision"])
        val p1 = DriverDna.profile(a.frame)!!
        checkTraits(d.getJSONObject("p1"), p1, "p1"); assertNull(p1["precision"])
        val pb = DriverDna.profile(b.frame, listOf(a.frame, c.frame))!!
        checkTraits(d.getJSONObject("pB"), pb, "pB")
        assertEquals(d.getString("archetype"), DriverDna.archetype(p3))
        assertEquals(strings(d.getJSONArray("compare")), DriverDna.compare(p3, pb))
        assertEquals(d.getString("export"), DriverDna.exportCode(p3, "Test Driver! 1"))
        assertEquals(d.getString("export_blank"), DriverDna.exportCode(p1))
    }

    @Test fun driverDnaCodesAndTrend() {
        val d = g.getJSONObject("dna"); val imports = d.getJSONObject("imports")
        for (code in imports.keys()) {
            val got = DriverDna.importCode(code); val e = imports.get(code)
            if (e == JSONObject.NULL) assertNull(code, got)
            else { val ea = e as JSONArray; assertEquals(ea.getString(0), got!!.first); checkTraits(ea.getJSONObject(1), got.second, code) }
        }
        val hist = d.getJSONArray("history")
        val entries = List(hist.length()) { i -> val o = hist.getJSONObject(i); DriverDna.Entry(o.getString("date"), DriverDna.TRAITS.associateWith { k -> optD(o.getJSONObject("traits"), k) }) }
        val tr = d.getJSONObject("trend")
        for (k in DriverDna.TRAITS) nearOpt(optD(tr, k), DriverDna.trend(entries, k), "trend $k")
        assertNull(DriverDna.trend(entries.take(2), "smoothness"))
    }

    @Test fun driverDnaHistoryFile() {
        val f = File.createTempFile("dna", ".json"); f.delete()
        assertTrue(DriverDna.record(mapOf("smoothness" to 50.0, "precision" to null), "2026-01-02", f))
        assertTrue(DriverDna.record(mapOf("smoothness" to 60.0), "2026-01-02", f))   // same day replaces
        assertTrue(DriverDna.record(mapOf("smoothness" to 70.0), "2026-01-01", f))
        val h = DriverDna.loadHistory(f)
        assertEquals(listOf("2026-01-01", "2026-01-02"), h.map { it.date }); assertEquals(60.0, h[1].traits["smoothness"]!!, 0.0)
        f.delete()
    }

    // ---------------------------------------------------------------- planner
    @Test fun plannerDrillsAndText() {
        val p = g.getJSONObject("planner"); val times = List(p.getJSONArray("times").length()) { p.getJSONArray("times").getDouble(it) }
        val r = Planner.plan(listOf(a.frame, b.frame, c.frame, syn["S1"], syn["S2"], syn["S3"]), times)
        assertEquals(p.getBoolean("ok"), r.ok); assertEquals(p.getString("text"), r.text); assertEquals(p.getInt("best_lap"), r.bestLap)
        near(p.getDouble("total_gain_s"), r.totalGainS, "gain")
        val ed = p.getJSONArray("drills"); assertEquals(ed.length(), r.drills.size); assertTrue(r.drills.isNotEmpty())
        for (i in r.drills.indices) {
            val e = ed.getJSONObject(i); val d = r.drills[i]
            assertEquals(e.getInt("corner"), d.corner); near(e.getDouble("apex"), d.apex, "apex"); near(e.getDouble("mean_loss_s"), d.meanLossS, "loss")
            near(e.getDouble("spread_s"), d.spreadS, "spread"); assertEquals(e.getString("target"), d.target)
        }
        val s = g.getJSONObject("planner_short")
        assertEquals(s.getString("text"), Planner.plan(listOf(a.frame, b.frame), times.take(2)).text)
    }

    @Test fun plannerProgress() {
        val e = g.getJSONArray("planner_progress")
        fun s(date: String, best: Double, med: Double) = Planner.Session("k", date, best, med)
        assertEquals(e.getString(0), Planner.progress(emptyList(), "k"))
        assertEquals(e.getString(1), Planner.progress(listOf(s("2026-01-01", 90.0, 92.0)), "k"))
        assertEquals(e.getString(2), Planner.progress(listOf(s("2026-01-01", 90.0, 92.0), s("2026-02-01", 89.4, 92.6)), "k"))
        assertEquals(e.getString(3), Planner.progress(listOf(s("2026-01-01", 90.0, 92.0), s("2026-02-01", 90.4, 91.6)), "k"))
    }

    // ---------------------------------------------------------------- dyno compare
    @Test fun dynoCompareOverlayAndAdvice() {
        val d = g.getJSONObject("dyno_compare")
        val ra = Dyno.analyse(a.frame); val rb = Dyno.analyse(b.frame)
        val ov = DynoCompare.overlay(ra, rb, "Lap A", "Lap B")
        assertEquals(d.getBoolean("ok"), ov.ok); assertEquals(d.getString("text"), ov.text)
        for ((key, arr) in listOf("rpm" to ov.rpm, "a" to ov.a, "b" to ov.b)) {
            val e = d.getJSONArray(key); assertEquals(key, e.length(), arr.size)
            for (i in arr.indices) near(e.getDouble(i), arr[i], "$key[$i]")
        }
        val adv = d.getJSONArray("advice")
        for ((i, p) in listOf(ra to a.frame, rb to b.frame).withIndex()) {
            val r = DynoCompare.finalDriveAdvice(p.first, p.second["speed_kmh"]); val e = adv.getJSONObject(i)
            assertEquals(e.getString("action"), r.action); assertEquals(e.getString("text"), r.text)
            near(e.getDouble("limiter_share"), r.limiterShare, "share")
        }
        assertEquals(d.getString("short"), DynoCompare.finalDriveAdvice(ra, DoubleArray(5) { 100.0 }).text)
        val top = adv.getJSONObject(0).getDouble("top_gear_kmh"); val sc = d.getJSONArray("scales")
        for (i in 0 until sc.length()) {
            val e = sc.getJSONObject(i); val r = DynoCompare.finalDriveAdvice(ra, DoubleArray(a.frame.n) { top * e.getDouble("scale") })
            assertEquals(e.getString("action"), r.action); assertEquals(e.getString("text"), r.text)
        }
        assertEquals(d.getString("ghost_overlay"), DynoCompare.overlay(Dyno.Result(), rb).text)
    }

    // ---------------------------------------------------------------- replay html
    @Test fun replayHtmlMatchesTheDesktopFile() {
        val rp = g.getJSONObject("replay"); val tpl = rp.getString("template")
        assertEquals(rp.getString("html"), ReplayHtml.build(a.frame, "TRACE replay <A&B>", b.frame, tpl))
        assertEquals(rp.getString("html_no_ghost"), ReplayHtml.build(a.frame, "", null, tpl))
        assertEquals(rp.getBoolean("usable_a"), ReplayHtml.usable(a.frame))
        assertEquals(rp.getInt("n_compact"), ReplayHtml.compact(a.frame).size)
        assertFalse(ReplayHtml.usable(a.frame.slice(0, 10)))
    }

    // ---------------------------------------------------------------- groups
    private fun canon(o: Any?) = Groups.canonicalJson(Groups.fromJson(o))

    private fun makeBoard(): MutableMap<String, Any?> {
        val ch = Groups.makeChallenge("Red Bull Ring", "Mazda MX-5", "2026-W46", "Weekly <1>")
        val adds = listOf(
            Triple("Alice", 91234 as Any?, listOf(30000, 31000, 30234) as List<*>?), Triple("bob", 90111, listOf(29000, 31111)),
            Triple("Cleo", 90111, listOf(29500, 30500, 30111)), Triple("Dan", 19999, null), Triple("Eve", "95000", listOf(31000)),
            Triple("Fay", 120000.7, listOf(-5, 0, 40000)))
        val exp = g.getJSONObject("groups").getJSONArray("adds")
        for ((i, t) in adds.withIndex()) assertEquals("add $i", exp.getJSONArray(i).getBoolean(3), Groups.addResult(ch, t.first, t.second, t.third))
        Groups.addResult(ch, "ALICE", 90500, listOf(30100, 30200, 30200))
        Groups.addResult(ch, "alice", 99999, listOf(1))
        return ch
    }

    @Test fun groupsBoardsAndStandings() {
        val gk = g.getJSONObject("groups"); val ch = makeBoard()
        assertEquals(canon(gk.getJSONObject("challenge")), Groups.canonicalJson(ch))
        val es = gk.getJSONArray("standings"); val st = Groups.standings(ch)
        assertEquals(es.length(), st.size)
        for (i in st.indices) { val e = es.getJSONObject(i); assertEquals(e.getInt("pos"), st[i].pos); assertEquals(e.getString("player"), st[i].player); assertEquals(e.getLong("time_ms"), st[i].timeMs); assertEquals(e.getLong("gap_ms"), st[i].gapMs) }
        val eb = gk.getJSONArray("sector_board"); val sb = Groups.sectorBoard(ch)
        assertEquals(eb.length(), sb.size)
        for (i in sb.indices) { val e = eb.getJSONObject(i); assertEquals(e.getInt("sector"), sb[i].sector); assertEquals(e.getString("player"), sb[i].player); assertEquals(e.getLong("time_ms"), sb[i].timeMs) }
        assertEquals(gk.getLong("ideal"), Groups.idealLap(ch))
        val fm = gk.getJSONObject("fmt"); for (k in fm.keys()) assertEquals(k, fm.getString(k), Groups.fmt(k.toLong()))
        val wk = gk.getJSONObject("week_ids"); for (k in wk.keys()) assertEquals(k, wk.getString(k), Groups.weekId(LocalDate.parse(k)))
    }

    @Test fun groupsCodesCrossDecode() {
        val gk = g.getJSONObject("groups"); val ch = makeBoard()
        // codes made by the desktop decode here, to the same payload
        val codes = gk.getJSONObject("codes_to_decode"); val un = gk.getJSONObject("unpacked")
        for (k in codes.keys()) assertEquals(k, canon(un.getJSONObject(k)), Groups.canonicalJson(Groups.unpack(codes.getString(k))))
        // and a code made here decodes to what was put in (the desktop reads the same TRC1 format)
        val mine = Groups.exportChallenge(ch)
        assertTrue(mine.startsWith("TRC1-"))
        assertEquals(Groups.canonicalJson(linkedMapOf("v" to 1, "ch" to ch)), Groups.canonicalJson(Groups.unpack(mine)))
        val result = Groups.exportResult(ch, "bob")!!
        assertEquals(Groups.canonicalJson(Groups.unpack(result)), Groups.canonicalJson(linkedMapOf<String, Any?>(
            "v" to 1, "key" to "2026-W46|red bull ring|mazda mx-5", "r" to (ch["results"] as List<*>).first { (it as Map<*, *>)["player"] == "bob" },
            "track" to ch["track"], "car" to ch["car"], "week" to ch["week"], "name" to ch["name"])))
        assertNull(Groups.exportResult(ch, "nobody"))
        // a flipped character fails the checksum
        assertNull(Groups.unpack(mine.dropLast(2) + (if (mine.last() == 'A') "BB" else "AA")))
    }

    @Test fun groupsImportMessagesAndMerging() {
        val gk = g.getJSONObject("groups"); val imp = gk.getJSONObject("imports")
        val (fresh, m1) = Groups.importCode(gk.getString("export_challenge"))
        val e1 = imp.getJSONArray("new_from_challenge")
        assertEquals(e1.getString(0), m1); assertEquals(canon(e1.getJSONObject(1)), Groups.canonicalJson(fresh))
        val (o2, m2) = Groups.importCode(gk.getString("export_result"), Groups.makeChallenge("Red Bull Ring", "Mazda MX-5", "2026-W46"))
        assertEquals(imp.getJSONArray("result_into_matching").getString(0), m2); assertEquals(1, Groups.standings(o2!!).size)
        val (o3, m3) = Groups.importCode(gk.getString("export_result"), Groups.makeChallenge("Red Bull Ring", "Mazda MX-5", "2026-W47"))
        assertEquals(imp.getJSONArray("result_wrong_week").getString(0), m3); assertEquals(0, Groups.standings(o3!!).size)
        assertEquals(imp.getJSONArray("challenge_wrong_week").getString(0), Groups.importCode(gk.getString("export_challenge"), Groups.makeChallenge("Red Bull Ring", "Mazda MX-5", "2026-W47")).second)
        val (o5, m5) = Groups.importCode("TRC1-abcdef-xyz")
        assertEquals(imp.getJSONArray("bad_checksum").getString(0), m5); assertNull(o5)
        assertEquals(imp.getJSONArray("garbage").getString(0), Groups.importCode("garbage").second)
        val bad = gk.getString("export_challenge").dropLast(3) + "AAA"
        assertEquals(imp.getJSONArray("edited").getString(0), Groups.importCode(bad).second)
    }

    // ---------------------------------------------------------------- report texts
    @Test fun reportTextsMatchTheDesktop() {
        val r = g.getJSONObject("reports")
        val lapS = numOf(a.meta["lap_time_s"])
        assertEquals(r.getString("coach"), AnalysisTools.coachReport(b.frame, a.frame))
        assertEquals(r.getString("coach_missing"), AnalysisTools.coachReport(a.frame, null))
        assertEquals(r.getString("setup"), AnalysisTools.setupReport(a.frame)); assertEquals(r.getString("setup_missing"), AnalysisTools.setupReport(null))
        assertEquals(r.getString("validate"), AnalysisTools.validateReport(a.frame, lapS)); assertEquals(r.getString("validate_missing"), AnalysisTools.validateReport(null))
        assertEquals(r.getString("plan_tool"), AnalysisTools.lapTool("plan", a.frame, b.frame, a.meta, b.meta, listOf(c.frame)))
        val store = AnalysisTools.DnaStore(File.createTempFile("dna", ".json").also { it.delete() }) { "2026-01-02" }
        assertEquals(r.getString("dna_tool"), AnalysisTools.lapTool("dna", a.frame, b.frame, a.meta, b.meta, listOf(c.frame), store))
        assertEquals(1, store.load().size)   // the tool files today's profile, like the desktop
        assertEquals(r.getString("coach_tool"), AnalysisTools.lapTool("coach", b.frame, a.frame, b.meta, a.meta))
        assertEquals(r.getString("validate_tool"), AnalysisTools.lapTool("validate", b.frame, a.frame, b.meta, a.meta))
        assertEquals(r.getString("dyno_tool"), AnalysisTools.lapTool("dyno", a.frame, b.frame, a.meta, b.meta))
        assertEquals(r.getString("setup_tool"), AnalysisTools.lapTool("setup", a.frame))
        assertEquals(g.getString("dna_report"), AnalysisTools.dnaReport(a.frame, listOf(b.frame, c.frame)))
        assertEquals(g.getString("dna_report_one"), AnalysisTools.dnaReport(a.frame, emptyList()))
        assertEquals(g.getJSONObject("dyno_compare").getString("report"), AnalysisTools.dynoCompareReport(a.frame, b.frame))
        assertEquals(g.getJSONObject("dyno_compare").getString("report_missing"), AnalysisTools.dynoCompareReport(a.frame, null))
    }

    @Test fun raceReportsMatchTheDesktop() {
        val r = g.getJSONObject("reports"); val laps = RaceLoader.raceLapFrames(race.frame)
        assertEquals(r.getInt("n_race_laps"), laps.size)
        val rr = r.getJSONObject("race")
        for (k in rr.keys()) assertEquals(k, rr.getString(k), AnalysisTools.raceReport(k, laps))
        assertEquals(r.getString("race_short"), AnalysisTools.raceReport("coach", laps.take(1)))
    }

    // ---------------------------------------------------------------- local AI wording (TRACE AI)
    @Test fun llmGuardMatchesTheDesktop() {
        val l = g.getJSONObject("llm")
        assertEquals(l.getString("system_prompt"), LlmGuard.SYSTEM_PROMPT)
        assertEquals(l.getInt("max_chars"), LlmGuard.MAX_CHARS)
        assertEquals(strings(l.getJSONArray("direction_words")), LlmGuard.DIRECTION_WORDS)
        val cases = l.getJSONArray("guard"); var accepted = 0; var rejected = 0
        for (i in 0 until cases.length()) {
            val c = cases.getJSONArray(i)
            val (ok, why) = LlmGuard.guard(c.getString(0), if (c.isNull(1)) null else c.getString(1))
            assertEquals("case $i accepted", if (c.isNull(2)) null else c.getString(2), ok)
            assertEquals("case $i reason", c.getString(3), why)
            if (ok != null) accepted++ else rejected++
        }
        assertTrue(accepted >= 6 && rejected >= 10)
    }

    @Test fun llmFactsAndEnglishMatchTheDesktop() {
        val l = g.getJSONObject("llm"); val f = l.getJSONArray("facts")
        for (i in 0 until f.length()) {
            val e = f.getJSONArray(i); val got = LlmGuard.facts(e.getString(0))
            assertEquals(strings(e.getJSONArray(1)).toSet(), got.numbers); assertEquals(strings(e.getJSONArray(2)).toSet(), got.corners)
            assertEquals(strings(e.getJSONArray(3)).toSet(), got.directions)
        }
        val en = l.getJSONObject("english"); for (k in en.keys()) assertEquals(k, en.getBoolean(k), LlmGuard.looksEnglish(k))
        val th = l.getJSONObject("n_threads"); for (k in th.keys()) assertEquals(k, th.getInt(k), LlmGuard.nThreads(k.toInt()))
        assertEquals(l.getJSONArray("messages").toString(), org.json.JSONArray(LlmGuard.messagesJson("Brake 14 m later at T4.")).toString())
    }

    @Test fun phoneMemoryBandsMatchTheDesktop() {
        val l = g.getJSONObject("llm"); val p = l.getJSONArray("phone")
        for (i in 0 until p.length()) {
            val e = p.getJSONArray(i); val r = LlmGuard.ratePhone(e.getDouble(0))
            assertEquals(e.getString(1), r.level); assertEquals(e.getString(2), r.text)
        }
        assertEquals(l.getString("recommended_phones"), LlmGuard.RECOMMENDED_PHONES)
        assertEquals("red", LlmGuard.ratePhone(4.99).level); assertEquals("amber", LlmGuard.ratePhone(5.0).level)
        assertEquals("amber", LlmGuard.ratePhone(6.99).level); assertEquals("green", LlmGuard.ratePhone(7.0).level)
    }

    @Test fun rewordedCoachTipsFallBackToTheRulesText() {
        val d = g.getJSONObject("llm").getJSONObject("describe")
        val tips = List(d.getJSONArray("tips").length()) { i ->
            val e = d.getJSONArray("tips").getJSONObject(i)
            Coach.Tip(e.getInt("corner"), e.getDouble("apex"), optD(e, "brake_pos"), e.getString("kind"), e.getString("text"), e.getString("cue"), e.getDouble("time_s"), e.getDouble("est_s"))
        }
        assertEquals(d.getString("plain"), LlmGuard.describeWithAi(tips, null).first)
        for (k in listOf("mixed", "all_good", "all_bad")) {
            val e = d.getJSONObject(k); val answers = ArrayDeque(strings(e.getJSONArray("answers")))
            val got = LlmGuard.describeWithAi(tips) { answers.removeFirst() }
            assertEquals(k, e.getJSONArray("result").getString(0), got.first); assertEquals(k, e.getJSONArray("result").getBoolean(1), got.second)
        }
        assertTrue(d.getJSONObject("all_good").getJSONArray("result").getString(0).startsWith(LlmGuard.HEADER_AI))
        assertFalse(d.getJSONObject("all_bad").getJSONArray("result").getBoolean(1))
    }

    @Test fun coachingFrameFromSamplesMatchesTheLoader() {
        // frameFromSamples must derive lat_g the way the analysts (and the Python frame_from_samples) do
        val samples = List(40) { i -> mapOf<String, Any?>("track_position" to i * 10.0, "speed_kmh" to 100.0 + i, "throttle" to 1.0, "brake" to 0.0, "steering" to 0.1, "t" to i * 0.1, "ang_y" to 0.3) }
        val f = Corners.frameFromSamples(samples)!!
        assertEquals(40, f.n)
        near(0.3 * 110.0 / 3.6 / 9.81, f["lat_g"][10], "lat_g")
        assertEquals(0.0, Corners.frameFromSamples(listOf(mapOf("track_position" to null)))!!["speed_kmh"][0], 0.0)
    }
}
