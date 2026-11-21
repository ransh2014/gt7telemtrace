package app.trace

import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.fillMaxWidth
import androidx.compose.foundation.layout.heightIn
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.AlertDialog
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.unit.dp
import app.trace.telemetry.AnalysisTools
import app.trace.telemetry.Frame
import app.trace.telemetry.RaceLoader
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

/** A text report in a scrollable dialog: what the desktop's report window shows for an ANALYSIS TOOLS button. */
@Composable
fun ReportDialog(a: AP, title: String, text: String, onClose: () -> Unit) {
    AlertDialog(onDismissRequest = onClose, containerColor = a.bg,
        title = { Lbl(a, title, a.grn, 14, true) },
        text = { Column(Modifier.heightIn(max = 420.dp).verticalScroll(rememberScrollState())) { Lbl(a, text, a.fg, 12) } },
        confirmButton = { TBtn(a, "Close", on = true) { onClose() } })
}

/** One tool button: its label from strings.xml, the report title from strings.xml, the text from [AnalysisTools]. */
private class Tool(val kind: String, val label: Int, val title: Int)

private val LAP_TOOLS = listOf(
    Tool("coach", R.string.tool_coach_ab, R.string.report_coach),
    Tool("setup", R.string.tool_setup_advisor, R.string.report_setup),
    Tool("dna", R.string.tool_driver_dna, R.string.report_dna),
    Tool("plan", R.string.tool_practice_plan_all, R.string.report_plan),
    Tool("validate", R.string.tool_validate_lap, R.string.report_validate),
    Tool("dyno", R.string.tool_compare_dyno, R.string.report_dyno),
)
private val RACE_TOOLS = listOf(
    Tool("coach", R.string.race_tool_coach, R.string.report_race),
    Tool("plan", R.string.race_tool_plan, R.string.report_race),
    Tool("dna", R.string.race_tool_dna, R.string.report_race),
    Tool("validate", R.string.race_tool_validate, R.string.report_race),
)

/** Lap Analyst ANALYSIS TOOLS (registry.LAP_TOOLS + the 3D replay export), same labels and order as the desktop. */
@Composable
fun LapToolsCard(ctl: LapController, a: AP, scope: CoroutineScope, onReplay: () -> Unit) {
    var report by remember { mutableStateOf<Pair<Int, String>?>(null) }
    val ctx = LocalContext.current
    val colors = mapOf("coach" to a.grn, "setup" to a.cyn, "dna" to a.prp, "plan" to a.yel, "validate" to a.acc, "dyno" to a.cyn)
    Card(a, stringResource(R.string.analysis_tools), accent = a.grn) {
        for (t in LAP_TOOLS) TBtn(a, stringResource(t.label), Modifier.fillMaxWidth(), color = colors[t.kind] ?: a.fg) {
            val dfa = ctl.a?.df; val dfb = ctl.b?.df
            val metaA = ctl.a?.meta; val metaB = ctl.b?.meta
            val extra = ctl.extras.map { it.first.df }
            val store = AnalysisTools.DnaStore(File(ctl.lapsDir.parentFile ?: ctl.lapsDir, "driver_dna.json"))
            scope.launch {
                val text = withContext(Dispatchers.Default) {
                    try { AnalysisTools.lapTool(t.kind, dfa, dfb, metaA, metaB, extra, store) } catch (e: Exception) { "${e::class.simpleName}: ${e.message}" }
                }
                report = t.title to text
                // TRACE AI: the rules text is already on screen; swap in the reworded tips when the model is done
                if (t.kind == "coach" && AiWording.ready(ctx)) {
                    report = t.title to (text + "\n\n(local AI wording in progress...)")
                    val ai = withContext(Dispatchers.Default) {
                        AiWording.withRewriter(ctx) { w -> AnalysisTools.lapTool(t.kind, dfa, dfb, metaA, metaB, extra, store, w) }
                    }
                    if (ai != null) report = t.title to ai else report = t.title to text
                }
            }
        }
        TBtn(a, stringResource(R.string.tool_export_replay3d), Modifier.fillMaxWidth(), color = a.grn) { onReplay() }
    }
    report?.let { (title, text) -> ReportDialog(a, stringResource(title), text) { report = null } }
}

/** Race Analyst ANALYSIS TOOLS (registry.RACE_TOOLS). */
@Composable
fun RaceToolsCard(df: Frame, a: AP, scope: CoroutineScope) {
    var report by remember { mutableStateOf<String?>(null) }
    val ctx = LocalContext.current
    val colors = mapOf("coach" to a.grn, "plan" to a.grn, "dna" to a.grn, "validate" to a.grn)
    Card(a, stringResource(R.string.analysis_tools), accent = a.grn) {
        for (t in RACE_TOOLS) TBtn(a, stringResource(t.label), Modifier.fillMaxWidth(), color = colors[t.kind] ?: a.fg) {
            scope.launch {
                report = withContext(Dispatchers.Default) {
                    try { AnalysisTools.raceReport(t.kind, RaceLoader.raceLapFrames(df)) } catch (e: Exception) { "${e::class.simpleName}: ${e.message}" }
                }
                if (t.kind == "coach" && AiWording.ready(ctx)) {
                    val rules = report ?: ""
                    report = rules + "\n\n(local AI wording in progress...)"
                    val ai = withContext(Dispatchers.Default) {
                        AiWording.withRewriter(ctx) { w -> try { AnalysisTools.raceReport(t.kind, RaceLoader.raceLapFrames(df), w) } catch (e: Exception) { rules } }
                    }
                    report = ai ?: rules
                }
            }
        }
    }
    report?.let { ReportDialog(a, stringResource(R.string.report_race), it, onClose = { report = null }) }
}
