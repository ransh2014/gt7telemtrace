package app.trace.telemetry

import java.io.File
import java.time.LocalDate

/**
 * analysis_tools.py: the text the Lap Analyst's and Race Analyst's ANALYSIS TOOLS buttons show. Pure functions over
 * the frames the analyst already holds, so they are unit-tested against the desktop's output (golden4.json.gz).
 * The window titles live in labels.py (REPORT_*), the screens read them from strings.xml.
 */
object AnalysisTools {
    /** Where Driver DNA keeps its history, and the date it files today's profile under. */
    class DnaStore(private val file: File, private val today: () -> String = { LocalDate.now().toString() }) {
        fun load(): List<DriverDna.Entry> = DriverDna.loadHistory(file)
        fun record(traits: DnaTraits) = DriverDna.record(traits, today(), file)
    }

    private fun need(df: Frame?, what: String): String? = if (df != null) null else "Load Lap A first ($what)."

    /** rewriter (TRACE AI only) rewords each tip; the guard keeps the rules text for any tip it can't reword safely. */
    fun coachReport(dfa: Frame?, dfb: Frame?, rewriter: LlmGuard.Rewriter? = null): String {
        if (dfa == null || dfb == null) return "The coach compares Lap A with a reference.\nLoad Lap A, then load your best lap (or a top lap) as Lap B."
        return LlmGuard.describeWithAi(Coach.analyse(dfa, dfb, maxTips = 5), rewriter).first
    }

    fun setupReport(dfa: Frame?): String = need(dfa, "setup advisor") ?: SetupAdvisor.describe(SetupAdvisor.diagnose(dfa))

    /** lapTimeS is the lap's recorded time (null or 0 = unknown). */
    fun validateReport(dfa: Frame?, lapTimeS: Double? = null, refLengthM: Double? = null): String {
        if (dfa == null) return "Load Lap A first (lap validation)."
        val lap = if (lapTimeS == null || lapTimeS == 0.0 || !lapTimeS.isFinite()) null else lapTimeS
        return Validate.describe(Validate.check(dfa, refLengthM, lap))
    }

    fun dnaReport(dfa: Frame?, others: List<Frame?> = emptyList(), store: DnaStore? = null, save: Boolean = false): String {
        if (dfa == null) return "Load Lap A first (driver DNA)."
        val p = DriverDna.profile(dfa, others) ?: return "This lap can't be read for driver DNA."
        val lines = arrayListOf(DriverDna.archetype(p), "")
        for (k in DriverDna.TRAITS) {
            val v = p[k]
            lines.add(DriverDna.LABELS[k]!!.padEnd(22) + (if (v == null) "needs 3+ laps loaded" else "${fixed(v, 0)} / 100"))
        }
        var hist = store?.load() ?: emptyList()
        if (save && store != null) { store.record(p); hist = store.load() }
        for (k in DriverDna.TRAITS) {
            val t = DriverDna.trend(hist, k)
            if (t != null && Math.abs(t) >= 0.5) lines.add("  ${DriverDna.LABELS[k]} is ${if (t > 0) "rising" else "falling"} (${signed(t, 1)} per day logged)")
        }
        lines.addAll(listOf("", "Share this code to compare with a friend:", DriverDna.exportCode(p)))
        return lines.joinToString("\n")
    }

    /** laps is (frame, lap time in seconds) for every lap currently loaded. */
    fun planReport(laps: List<Pair<Frame?, Double>>): String {
        val use = laps.filter { it.first != null && it.second != 0.0 }
        return Planner.plan(use.map { it.first }, use.map { it.second }).text
    }

    fun dynoCompareReport(dfa: Frame?, dfb: Frame?): String {
        if (dfa == null || dfb == null) return "Load Lap A and Lap B (two cars, or two tunes of one car) to compare power curves."
        val ra = Dyno.analyse(dfa); val rb = Dyno.analyse(dfb)
        val out = arrayListOf(DynoCompare.overlay(ra, rb, "Lap A", "Lap B").text)
        for ((name, df, res) in listOf(Triple("Lap A", dfa, ra), Triple("Lap B", dfb, rb)))
            out.add("$name: ${DynoCompare.finalDriveAdvice(res, df["speed_kmh"]).text}")
        return out.joinToString("\n")
    }

    /** Race Analyst: the same tools across the complete laps of one race (RaceLoader.raceLapFrames). */
    fun raceReport(kind: String, laps: List<RaceLoader.LapFrame>, rewriter: LlmGuard.Rewriter? = null): String {
        if (laps.size < 2) return "Needs at least two complete laps in this race."
        val best = laps.minByOrNull { it.timeS }!!
        when (kind) {
            "coach" -> {
                val worst = laps.maxByOrNull { it.timeS }!!
                val head = "Slowest lap (lap ${worst.lap}, +${fixed(worst.timeS - best.timeS, 2)}s) against your best (lap ${best.lap}):\n"
                return head + LlmGuard.describeWithAi(Coach.analyse(worst.frame, best.frame, maxTips = 5), rewriter).first
            }
            "plan" -> return Planner.plan(laps.map { it.frame }, laps.map { it.timeS }).text
            "dna" -> {
                val p = DriverDna.profile(best.frame, laps.filter { it.lap != best.lap }.map { it.frame })
                    ?: return "These laps can't be read for driver DNA."
                val lines = arrayListOf("Across ${laps.size} laps: ${DriverDna.archetype(p)}", "")
                for (k in DriverDna.TRAITS) lines.add(DriverDna.LABELS[k]!!.padEnd(22) + (p[k]?.let { "${fixed(it, 0)} / 100" } ?: "--"))
                return (lines + listOf("", "Share: " + DriverDna.exportCode(p))).joinToString("\n")
            }
            "validate" -> return laps.joinToString("\n") { l ->
                val r = Validate.check(l.frame, null, l.timeS)
                "Lap ${l.lap}: ${r.verdict}" + (if (r.verdict == "clean") "" else " - " + r.issues.joinToString(" ") { it.text })
            }
        }
        return "Unknown tool."
    }

    /** Report text for one Lap Analyst tool kind ("coach", "setup", "dna", "plan", "validate", "dyno"). */
    fun lapTool(kind: String, dfa: Frame?, dfb: Frame? = null, metaA: Meta? = null, metaB: Meta? = null, extra: List<Frame> = emptyList(),
                store: DnaStore? = null, rewriter: LlmGuard.Rewriter? = null): String = when (kind) {
        "coach" -> coachReport(dfa, dfb, rewriter)
        "setup" -> setupReport(dfa)
        "dna" -> dnaReport(dfa, listOf(dfb) + extra, store, save = true)
        "plan" -> {
            fun secs(d: Frame?, meta: Meta?): Double {
                val m = meta?.let { numOf(it["lap_time_s"]) } ?: 0.0
                if (m != 0.0) return m
                return try { val t = d!!["t"]; t[t.size - 1] - t[0] } catch (_: Exception) { 0.0 }
            }
            val laps = ArrayList<Pair<Frame?, Double>>()
            laps.add(dfa to secs(dfa, metaA))
            if (dfb != null) laps.add(dfb to secs(dfb, metaB))
            for (f in extra) laps.add(f to secs(f, null))
            planReport(laps)
        }
        "validate" -> validateReport(dfa, metaA?.let { numOf(it["lap_time_s"]) })
        "dyno" -> dynoCompareReport(dfa, dfb)
        else -> throw NoSuchElementException(kind)
    }
}
