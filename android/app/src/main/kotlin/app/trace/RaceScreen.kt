package app.trace

import android.graphics.Typeface
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.res.ResourcesCompat
import androidx.compose.ui.window.Dialog
import app.trace.charts.*
import app.trace.telemetry.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import java.io.File

class RaceSlot(val file: File?, val race: RaceFile) {
    val meta: Meta get() = race.meta
    val df: Frame get() = race.frame
    val label: String get() = RaceLoader.raceLabel(meta)
}

class RaceController(val lapsDir: File) {
    var rows by mutableStateOf<List<LapIndex.RaceRow>>(emptyList())
    var a by mutableStateOf<RaceSlot?>(null)
    var b by mutableStateOf<RaceSlot?>(null)
    var compare by mutableStateOf(true)
    var group by mutableStateOf("Race")
    var pbBaseline by mutableStateOf(false)
    var error by mutableStateOf<String?>(null)
    var busy by mutableStateOf(false)
    var epoch by mutableStateOf(0)
    var notes by mutableStateOf("")

    suspend fun refresh() { rows = withContext(Dispatchers.IO) { LapIndex.scanRaces(lapsDir) } }

    private suspend fun open(f: File?, raw: (() -> JsonColumns.Raw)? = null): RaceSlot? = withContext(Dispatchers.IO) {
        try { RaceSlot(f, if (raw != null) RaceLoader.load(raw()) else RaceLoader.load(f!!)) } catch (e: Exception) { error = "Race: ${e.message}"; null }
    }
    suspend fun setA(f: File) { busy = true; open(f)?.let { a = it; epoch++ }; busy = false }
    suspend fun setB(f: File) { busy = true; open(f)?.let { b = it; epoch++ }; busy = false }
    fun clearB() { b = null; epoch++ }
    suspend fun importA(cr: android.content.ContentResolver, u: Uri) { busy = true; open(null) { cr.openInputStream(u)!!.use { JsonColumns.read(it) } }?.let { a = it; epoch++ }; busy = false }
    suspend fun importB(cr: android.content.ContentResolver, u: Uri) { busy = true; open(null) { cr.openInputStream(u)!!.use { JsonColumns.read(it) } }?.let { b = it; epoch++ }; busy = false }

    fun baseline(s: RaceSlot): RaceLoader.Baseline =
        if (pbBaseline) RaceLoader.pbFileBaseline(s.meta, lapsDir) else RaceLoader.bestLapBaseline(s.df)
}

private enum class RTab(val title: String) { SETUP("Races"), CHARTS("Charts"), REPLAY("Replay") }
private val RACE_SPEEDS = listOf(0.25, 0.5, 1.0, 2.0, 4.0, 8.0, 16.0, 32.0)

@Composable
fun RaceScreen(ctl: RaceController, a: AP, onBack: () -> Unit) {
    var tab by remember { mutableStateOf(RTab.SETUP) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { ctl.refresh() }
    Column(Modifier.fillMaxSize().background(a.bg).statusBarsPadding()) {
        Row(Modifier.fillMaxWidth().background(a.pnl2).padding(12.dp, 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("‹", Modifier.clickable(onClick = onBack).padding(end = 6.dp), color = a.cyn, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 24.sp)
            Lbl(a, "GT7", a.acc, 16, true); Lbl(a, "RACE ANALYST", a.cyn, 16, true)
            Spacer(Modifier.weight(1f))
            if (ctl.busy) Lbl(a, "loading…", a.dim, 11)
        }
        Row(Modifier.fillMaxWidth().padding(6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            for (t in RTab.values()) TBtn(a, t.title, Modifier.weight(1f), on = tab == t) { tab = t }
        }
        ctl.error?.let { e -> Row(Modifier.fillMaxWidth().background(a.pnl).clickable { ctl.error = null }.padding(8.dp)) { Lbl(a, e, a.org, 11) } }
        Box(Modifier.weight(1f)) {
            when (tab) {
                RTab.SETUP -> RaceSetup(ctl, a, scope) { if (ctl.a != null) tab = RTab.CHARTS }
                RTab.CHARTS -> RaceChartsTab(ctl, a)
                RTab.REPLAY -> {
                    val s = ctl.a
                    if (s == null) Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Lbl(a, "Load Race A first", a.dim, 13) }
                    else ReplayView(a, s.df, ctl.b?.df, RACE_SPEEDS) { f ->
                        val dt = RaceLoader.dtSeries(f)
                        Np.cumsum(DoubleArray(f.n) { f["speed_kmh"][it] / 3.6 * dt[it] }) to Np.cummax(Np.nanToNum(f["t"]))
                    }
                }
            }
        }
    }
}

@Composable
private fun RaceSetup(ctl: RaceController, a: AP, scope: kotlinx.coroutines.CoroutineScope, onLoaded: () -> Unit) {
    val ctx = LocalContext.current
    var pick by remember { mutableStateOf<String?>(null) }
    val impA = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { u: Uri? -> if (u != null) scope.launch { ctl.importA(ctx.contentResolver, u); onLoaded() } }
    val impB = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { u: Uri? -> if (u != null) scope.launch { ctl.importB(ctx.contentResolver, u) } }
    var csv by remember { mutableStateOf("") }
    val saveCsv = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/csv")) { u: Uri? ->
        if (u != null) scope.launch(Dispatchers.IO) { ctx.contentResolver.openOutputStream(u)?.use { it.write(csv.toByteArray()) } }
    }
    var png by remember { mutableStateOf<ByteArray?>(null) }
    val saveCard = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("image/png")) { u: Uri? ->
        val bytes = png
        if (u != null && bytes != null) scope.launch(Dispatchers.IO) { ctx.contentResolver.openOutputStream(u)?.use { it.write(bytes) } }
    }
    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(6.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Card(a, "RACE A") {
            Lbl(a, ctl.a?.label ?: "No race loaded", if (ctl.a != null) a.fg else a.dim, 12)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TBtn(a, "Browse", Modifier.weight(1f)) { pick = "A" }
                TBtn(a, "Import", Modifier.weight(1f)) { impA.launch(arrayOf("*/*")) }
            }
        }
        Card(a, "RACE B", accent = a.yel) {
            Lbl(a, ctl.b?.label ?: "No race loaded", if (ctl.b != null) a.fg else a.dim, 12)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TBtn(a, "Browse", Modifier.weight(1f)) { pick = "B" }
                TBtn(a, "Import", Modifier.weight(1f)) { impB.launch(arrayOf("*/*")) }
                TBtn(a, "Clear", Modifier.weight(1f)) { ctl.clearB() }
            }
        }
        val s = ctl.a
        if (s != null) {
            Card(a, "LAP SPLITS", accent = a.org) {
                val splits = remember(s) { RaceLoader.lapSplitStats(s.df) }
                val best = splits.filter { it.complete && it.timeS > 1 }.minByOrNull { it.timeS }
                Row(Modifier.fillMaxWidth()) { for (h in listOf("Lap", "Time", "Avg", "Top", "Brk%", "Thr%")) Lbl(a, h, a.dim, 10, modifier = Modifier.weight(1f)) }
                for (sp in splits) {
                    val c = if (sp === best) a.grn else if (!sp.complete) a.dim else a.fg
                    Row(Modifier.fillMaxWidth()) {
                        Lbl(a, "${sp.lap}", c, 11, modifier = Modifier.weight(1f)); Lbl(a, LapMath.fmtTime(sp.timeS), c, 11, modifier = Modifier.weight(1f))
                        Lbl(a, fixed(sp.avgSpeed, 0), c, 11, modifier = Modifier.weight(1f)); Lbl(a, fixed(sp.topSpeed, 0), c, 11, modifier = Modifier.weight(1f))
                        Lbl(a, fixed(sp.brakePct, 0), c, 11, modifier = Modifier.weight(1f)); Lbl(a, fixed(sp.throttlePct, 0), c, 11, modifier = Modifier.weight(1f))
                    }
                }
            }
            Card(a, "STATS", accent = a.grn) {
                val stats = remember(s) { RaceLoader.buildStats(s.meta, s.df) }
                for ((k, v) in stats) Row(Modifier.fillMaxWidth()) { Lbl(a, k, a.dim, 12, modifier = Modifier.weight(1f)); Lbl(a, v, a.fg, 12, true) }
            }
            Card(a, "CORNER BASELINE", accent = a.prp) {
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                    TBtn(a, "Best lap in race", Modifier.weight(1f), on = !ctl.pbBaseline) { ctl.pbBaseline = false; ctl.epoch++ }
                    TBtn(a, "Saved PB lap", Modifier.weight(1f), on = ctl.pbBaseline) { ctl.pbBaseline = true; ctl.epoch++ }
                }
            }
            Card(a, "RACE NOTES", accent = a.yel) {
                Field(a, ctl.notes, "notes (this session only)", Modifier.fillMaxWidth()) { ctl.notes = it }
            }
            Card(a, "EXPORT", accent = a.cyn) {
                TBtn(a, "Export CSV", Modifier.fillMaxWidth()) {
                    csv = RaceLoader.exportCsv(s.df)
                    saveCsv.launch("race_${LapMath.safeName(s.meta.text("car"))}_${LapMath.safeName(s.meta.text("track"))}.csv")
                }
                TBtn(a, "Race Card (PNG)", Modifier.fillMaxWidth()) {
                    scope.launch {
                        val fonts = CardFonts(
                            ResourcesCompat.getFont(ctx, R.font.inconsolata_regular) ?: Typeface.MONOSPACE,
                            ResourcesCompat.getFont(ctx, R.font.inconsolata_bold) ?: Typeface.MONOSPACE,
                        )
                        png = withContext(Dispatchers.Default) { ShareCard.png(ShareCard.raceSpec(s.meta, s.df), fonts) }
                        saveCard.launch("trace_race_card_${LapMath.safeName(s.meta.text("car"))}_${LapMath.safeName(s.meta.text("track"))}.png")
                    }
                }
            }
        }
        Spacer(Modifier.height(20.dp))
    }
    val mode = pick
    if (mode != null) {
        Dialog(onDismissRequest = { pick = null }) {
            Column(Modifier.fillMaxWidth().heightIn(max = 520.dp).background(a.bg).border(1.dp, a.dim2).padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
                Lbl(a, "Pick race $mode", a.cyn, 13, true)
                if (ctl.rows.isEmpty()) Lbl(a, "No saved races yet", a.dim, 12)
                LazyColumn(Modifier.weight(1f, fill = false)) {
                    items(ctl.rows) { r ->
                        Column(Modifier.fillMaxWidth().clickable {
                            pick = null
                            scope.launch { if (mode == "A") { ctl.setA(r.path); onLoaded() } else ctl.setB(r.path) }
                        }.padding(vertical = 7.dp)) {
                            Lbl(a, r.date, a.yel, 12, true); Lbl(a, r.label, a.fg, 11)
                        }
                        Box(Modifier.fillMaxWidth().height(1.dp).background(a.dim2))
                    }
                }
                TBtn(a, "Close", Modifier.fillMaxWidth()) { pick = null }
            }
        }
    }
}

@Composable
private fun RaceChartsTab(ctl: RaceController, a: AP) {
    val s = ctl.a
    if (s == null) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Lbl(a, "Load Race A first", a.dim, 13) }; return }
    var fig by remember { mutableStateOf<Fig?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var html by remember { mutableStateOf("") }
    val saveHtml = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/html")) { u: Uri? ->
        if (u != null) scope.launch(Dispatchers.IO) { ctx.contentResolver.openOutputStream(u)?.use { it.write(html.toByteArray()) } }
    }
    LaunchedEffect(ctl.epoch, ctl.group, ctl.compare, ctl.pbBaseline, a) {
        fig = null; err = null
        val dfb = if (ctl.compare) ctl.b?.df else null
        try {
            fig = withContext(Dispatchers.Default) {
                val base = if (ctl.group == "Corners") ctl.baseline(s) else null
                RaceCharts.build(ctl.group, RaceCharts.Input(a, s.df, dfb, base))
            }
        } catch (e: Exception) { err = "${e::class.simpleName}: ${e.message}" }
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            for (g in RaceCharts.GROUPS) TBtn(a, g, on = ctl.group == g) { ctl.group = g }
        }
        Row(Modifier.fillMaxWidth().padding(6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
            TBtn(a, if (ctl.compare) "Compare: ON" else "Compare: OFF", on = ctl.compare) { ctl.compare = !ctl.compare }
            Lbl(a, if (ctl.b == null) "(no Race B)" else ctl.b!!.label, a.dim, 11, modifier = Modifier.weight(1f))
            TBtn(a, "HTML") {
                val f = fig ?: return@TBtn
                scope.launch {
                    val ttl = "TRACE Race Analyst — ${s.label}"
                    html = withContext(Dispatchers.Default) { Exports.chartHtml(ctx, a, f, ttl) }
                    saveHtml.launch("trace_race_${LapMath.safeName(ctl.group)}_${LapMath.safeName(s.meta.text("track"))}.html")
                }
            }
        }
        Box(Modifier.weight(1f).fillMaxWidth()) {
            val f = fig
            when {
                err != null -> Lbl(a, err!!, a.org, 12, modifier = Modifier.padding(8.dp))
                f == null -> Lbl(a, "Building charts…", a.dim, 12, modifier = Modifier.padding(8.dp))
                else -> Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState())) { FigView(f, a); Spacer(Modifier.height(24.dp)) }
            }
        }
    }
}
