package app.trace

import android.graphics.Typeface
import android.net.Uri
import androidx.activity.compose.rememberLauncherForActivityResult
import androidx.activity.result.contract.ActivityResultContracts
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.horizontalScroll
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Slider
import androidx.compose.material3.SliderDefaults
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.StrokeCap
import androidx.compose.ui.graphics.drawscope.DrawScope
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.res.ResourcesCompat
import app.trace.charts.*
import app.trace.telemetry.*
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext
import kotlin.math.*

private enum class LTab(val title: String) { SETUP("Laps"), CHARTS("Charts"), REPLAY("Replay") }

@Composable
fun LapScreen(ctl: LapController, a: AP, onBack: () -> Unit) {
    var tab by remember { mutableStateOf(LTab.SETUP) }
    val scope = rememberCoroutineScope()
    LaunchedEffect(Unit) { ctl.refresh() }
    Column(Modifier.fillMaxSize().background(a.bg).statusBarsPadding()) {
        Row(Modifier.fillMaxWidth().background(a.pnl2).padding(12.dp, 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("‹", Modifier.clickable(onClick = onBack).padding(end = 6.dp), color = a.cyn, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 24.sp)
            Lbl(a, "GT7", a.acc, 16, true); Lbl(a, "LAP ANALYST", a.cyn, 16, true)
            Spacer(Modifier.weight(1f))
            if (ctl.busy) Lbl(a, "loading…", a.dim, 11)
        }
        Row(Modifier.fillMaxWidth().padding(6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            for (t in LTab.values()) TBtn(a, t.title, Modifier.weight(1f), on = tab == t) { tab = t }
        }
        ctl.error?.let { e ->
            Row(Modifier.fillMaxWidth().background(a.pnl).clickable { ctl.error = null }.padding(8.dp)) { Lbl(a, e, a.org, 11) }
        }
        Box(Modifier.weight(1f)) {
            when (tab) {
                LTab.SETUP -> SetupTab(ctl, a, scope) { if (ctl.a != null) tab = LTab.CHARTS }
                LTab.CHARTS -> ChartsTab(ctl, a)
                LTab.REPLAY -> ReplayTab(ctl, a)
            }
        }
    }
}

// ---------------------------------------------------------------- setup

@Composable
private fun SetupTab(ctl: LapController, a: AP, scope: kotlinx.coroutines.CoroutineScope, onLoaded: () -> Unit) {
    val ctx = LocalContext.current
    var pick by remember { mutableStateOf<String?>(null) }
    val importA = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { u: Uri? -> if (u != null) scope.launch { ctl.importA(ctx.contentResolver, u); onLoaded() } }
    val importB = rememberLauncherForActivityResult(ActivityResultContracts.OpenDocument()) { u: Uri? -> if (u != null) scope.launch { ctl.importB(ctx.contentResolver, u) } }
    var csv by remember { mutableStateOf("") }
    val saveCsv = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/csv")) { u: Uri? ->
        if (u != null) scope.launch(Dispatchers.IO) { ctx.contentResolver.openOutputStream(u)?.use { it.write(csv.toByteArray()) } }
    }
    var backupMsg by remember { mutableStateOf("") }
    val saveZip = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("application/zip")) { u: Uri? ->
        if (u != null) scope.launch(Dispatchers.IO) { val n = ctx.contentResolver.openOutputStream(u)?.use { Exports.backupLaps(ctl.lapsDir, it) } ?: 0; backupMsg = "Backed up $n files." }
    }
    var png by remember { mutableStateOf<ByteArray?>(null) }
    val saveCard = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("image/png")) { u: Uri? ->
        val bytes = png
        if (u != null && bytes != null) scope.launch(Dispatchers.IO) { ctx.contentResolver.openOutputStream(u)?.use { it.write(bytes) } }
    }
    var notes by remember(ctl.a) { mutableStateOf((ctl.a?.meta?.get("notes") as? String) ?: "") }
    var notesSaved by remember(ctl.a) { mutableStateOf(false) }
    val overlayColors = listOf(a.yel, a.grn, a.prp, a.org, Color(0xFF8899FF), Color(0xFFFF9ECB))

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(6.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
        Card(a, "LAP A") {
            Lbl(a, ctl.a?.full ?: "No lap loaded", if (ctl.a != null) a.fg else a.dim, 12)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TBtn(a, "Browse", Modifier.weight(1f)) { pick = "A" }
                TBtn(a, "Import", Modifier.weight(1f)) { importA.launch(arrayOf("*/*")) }
            }
        }
        Card(a, "LAP B", accent = a.yel) {
            Lbl(a, ctl.b?.full ?: "No lap loaded", if (ctl.b != null) a.fg else a.dim, 12)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TBtn(a, "Browse", Modifier.weight(1f)) { pick = "B" }
                TBtn(a, "Import", Modifier.weight(1f)) { importB.launch(arrayOf("*/*")) }
                TBtn(a, "Clear", Modifier.weight(1f)) { ctl.clearB() }
            }
        }
        Card(a, "OVERLAY", accent = a.prp) {
            if (ctl.extras.isEmpty()) Lbl(a, "Extra laps drawn on the Inputs charts", a.dim, 11)
            for ((s, c) in ctl.extras) Lbl(a, s.label, Color(c), 11)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                TBtn(a, "Add lap", Modifier.weight(1f)) { pick = "X" }
                TBtn(a, "Clear", Modifier.weight(1f)) { ctl.clearExtras() }
            }
        }
        LeaderboardCard(ctl, a, scope)
        val s = ctl.a
        if (s != null) {
            Card(a, "STATS", accent = a.grn) {
                val stats = remember(s) { LapMath.buildStats(s.meta, s.df) }
                for ((k, v) in stats) Row(Modifier.fillMaxWidth()) { Lbl(a, k, a.dim, 12, modifier = Modifier.weight(1f)); Lbl(a, v, a.fg, 12, true) }
                LapMath.lapFuelUsed(s.df)?.let { Row(Modifier.fillMaxWidth()) { Lbl(a, "Fuel / lap", a.dim, 12, modifier = Modifier.weight(1f)); Lbl(a, it, a.fg, 12, true) } }
            }
            Card(a, "SECTORS", accent = a.org) {
                val sec = remember(s) { LapMath.buildSectorStats(s.df) }
                Row(Modifier.fillMaxWidth()) { for (h in listOf("", "Time", "Avg", "Top")) Lbl(a, h, a.dim, 11, modifier = Modifier.weight(1f)) }
                for (r in sec) Row(Modifier.fillMaxWidth()) { for (c in r) Lbl(a, c, a.fg, 12, modifier = Modifier.weight(1f)) }
            }
            Card(a, "LAP NOTES", accent = a.yel) {
                Field(a, notes, "notes for this lap", Modifier.fillMaxWidth()) { notes = it; notesSaved = false }
                Row(horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
                    TBtn(a, "Save notes", Modifier.weight(1f)) { scope.launch { notesSaved = ctl.saveNotes(notes) } }
                    if (notesSaved) Lbl(a, "saved", a.grn, 11)
                    else if (s.file == null) Lbl(a, "imported lap: not saved", a.dim, 11)
                }
            }
            Card(a, "EXPORT", accent = a.cyn) {
                TBtn(a, "Export CSV", Modifier.fillMaxWidth()) {
                    csv = LapMath.exportCsv(s.df)
                    saveCsv.launch("lap_${LapMath.safeName(s.meta.text("car"))}_${LapMath.safeName(s.meta.text("track"))}.csv")
                }
                TBtn(a, "Backup All Laps", Modifier.fillMaxWidth()) {
                    if (!Exports.hasLaps(ctl.lapsDir)) backupMsg = "No laps found to back up yet."
                    else saveZip.launch("trace_laps_backup_${java.text.SimpleDateFormat("yyyyMMdd_HHmmss", java.util.Locale.ROOT).format(java.util.Date())}.zip")
                }
                if (backupMsg.isNotEmpty()) Lbl(a, backupMsg, a.dim, 11)
                TBtn(a, "Lap Card (PNG)", Modifier.fillMaxWidth()) {
                    scope.launch {
                        val fonts = CardFonts(
                            ResourcesCompat.getFont(ctx, R.font.inconsolata_regular) ?: Typeface.MONOSPACE,
                            ResourcesCompat.getFont(ctx, R.font.inconsolata_bold) ?: Typeface.MONOSPACE,
                        )
                        png = withContext(Dispatchers.Default) { ShareCard.png(ShareCard.lapSpec(s.meta, s.df), fonts) }
                        saveCard.launch("trace_card_${LapMath.safeName(s.meta.text("car"))}_${LapMath.safeName(s.meta.text("track"))}.png")
                    }
                }
            }
        }
        Spacer(Modifier.height(20.dp))
    }
    val mode = pick
    if (mode != null) LapPicker(a, ctl.rows, "Pick lap " + (if (mode == "X") "(overlay)" else mode), { r ->
        pick = null
        scope.launch {
            when (mode) { "A" -> { ctl.setA(r.path); onLoaded() }; "B" -> ctl.setB(r.path); else -> ctl.addExtra(r.path, overlayColors) }
        }
    }) { pick = null }
}

// ---------------------------------------------------------------- charts

@Composable
private fun ChartsTab(ctl: LapController, a: AP) {
    val s = ctl.a
    if (s == null) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Lbl(a, "Load Lap A on the Laps tab", a.dim, 13) }; return }
    var fig by remember { mutableStateOf<Fig?>(null) }
    var err by remember { mutableStateOf<String?>(null) }
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var html by remember { mutableStateOf("") }
    val saveHtml = rememberLauncherForActivityResult(ActivityResultContracts.CreateDocument("text/html")) { u: Uri? ->
        if (u != null) scope.launch(Dispatchers.IO) { ctx.contentResolver.openOutputStream(u)?.use { it.write(html.toByteArray()) } }
    }
    LaunchedEffect(ctl.epoch, ctl.group, ctl.compare, a) {
        fig = null; err = null
        val pool = if (ctl.group == "Theoretical") ctl.theoryPool() else null
        val extras = ctl.extrasFor()
        val dfb = if (ctl.compare) ctl.b?.df else null
        try {
            fig = withContext(Dispatchers.Default) { LapCharts.build(ctl.group, LapCharts.Input(a, s.df, dfb, extras, ctl.consensus, pool)) }
        } catch (e: Exception) { err = "${e::class.simpleName}: ${e.message}" }
    }
    Column(Modifier.fillMaxSize()) {
        Row(Modifier.fillMaxWidth().horizontalScroll(rememberScrollState()).padding(horizontal = 6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            for (g in LapCharts.GROUPS) TBtn(a, g, on = ctl.group == g) { ctl.group = g }
        }
        Row(Modifier.fillMaxWidth().padding(6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp), verticalAlignment = Alignment.CenterVertically) {
            TBtn(a, if (ctl.compare) "Compare: ON" else "Compare: OFF", on = ctl.compare) { ctl.compare = !ctl.compare }
            Lbl(a, if (ctl.b == null) "(no Lap B)" else ctl.b!!.label, a.dim, 11, modifier = Modifier.weight(1f))
            TBtn(a, "HTML") {
                val f = fig ?: return@TBtn
                scope.launch {
                    val ttl = "TRACE Lap Analyst — ${s.label}"
                    html = withContext(Dispatchers.Default) { Exports.chartHtml(ctx, a, f, ttl) }
                    saveHtml.launch("trace_${LapMath.safeName(ctl.group)}_${LapMath.safeName(s.meta.text("track"))}.html")
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

// ---------------------------------------------------------------- replay

private val SPEEDS = listOf(0.25, 0.5, 1.0, 2.0, 4.0, 8.0)

private class MapXf(val minX: Double, val minZ: Double, val sc: Double, val ox: Double, val oy: Double, val h: Double) {
    fun x(wx: Double) = (ox + (wx - minX) * sc).toFloat()
    fun y(wz: Double) = (h - (oy + (wz - minZ) * sc)).toFloat()
}

private fun mapXf(frames: List<Frame>, w: Double, h: Double): MapXf {
    var mnx = Double.MAX_VALUE; var mxx = -Double.MAX_VALUE; var mnz = Double.MAX_VALUE; var mxz = -Double.MAX_VALUE
    for (f in frames) { val x = f["world_x"]; val z = f["world_z"]; for (i in 0 until f.n) { mnx = min(mnx, x[i]); mxx = max(mxx, x[i]); mnz = min(mnz, z[i]); mxz = max(mxz, z[i]) } }
    val pad = 14.0
    val xr = max(1e-6, mxx - mnx); val zr = max(1e-6, mxz - mnz)
    val sc = min((w - 2 * pad) / xr, (h - 2 * pad) / zr)
    return MapXf(mnx, mnz, sc, (w - xr * sc) / 2, (h - zr * sc) / 2, h)
}

@Composable
private fun ReplayTab(ctl: LapController, a: AP) {
    val s = ctl.a
    if (s == null) { Box(Modifier.fillMaxSize(), contentAlignment = Alignment.Center) { Lbl(a, "Load Lap A on the Laps tab", a.dim, 13) }; return }
    val track = s.meta.text("track") ?: ""
    var bins by remember(s) { mutableStateOf(if (track.isNotBlank()) Boundary.load(ctl.lapsDir, track) else null) }
    ReplayView(a, s.df, ctl.b?.df, SPEEDS, boundary = bins, onSaveBoundary = {
        when {
            !s.df.has("world_x") -> "This lap has no GPS data to extract a boundary from."
            track.isBlank() -> "This lap has no track name recorded."
            else -> {
                val b = Boundary.extract(s.df)
                if (b.isEmpty()) "Not enough GPS samples in this lap to extract a boundary."
                else try { Boundary.save(ctl.lapsDir, track, b); bins = b; "Saved track boundary for '$track' (${b.size} sectors)." } catch (e: Exception) { "Couldn't save the track boundary: ${e.message}" }
            }
        }
    }) { LapMath.lapXY(it) }
}

@Composable
fun ReplayView(a: AP, dfa: Frame, dfb: Frame?, speeds: List<Double>, boundary: List<Boundary.Bin>? = null, onSaveBoundary: (() -> String)? = null, xyOf: (Frame) -> Pair<DoubleArray, DoubleArray>) {
    var bMsg by remember { mutableStateOf("") }
    val SPEEDS = speeds
    val s = dfa
    val n = dfa.n
    var pos by remember(s) { mutableStateOf(0.0) }
    var playing by remember(s) { mutableStateOf(false) }
    var spdIdx by remember { mutableStateOf(2) }
    var synced by remember { mutableStateOf(true) }
    var heat by remember { mutableStateOf(false) }
    val rate = remember(s) { 1.0 / max(1e-3, Np.median(LapLoader.stepDt(dfa["t"]))) }
    val hasMap = dfa.has("world_x") && dfa.has("world_z")
    val lxy = remember(s) { xyOf(dfa) }
    val lxyB = remember(dfb) { dfb?.let { xyOf(it) } }
    val deltas = remember(s, dfb, heat) { if (heat && dfb != null) LapMath.computeSectorDeltas(dfa, dfb, 25.0) else null }

    LaunchedEffect(playing, spdIdx, s) {
        var last = 0L
        while (playing) {
            androidx.compose.runtime.withFrameNanos { now ->
                if (last != 0L) { val dt = min(0.25, (now - last) / 1e9); pos += SPEEDS[spdIdx] * rate * dt }
                last = now
                if (pos >= n - 1) { pos = (n - 1).toDouble(); playing = false }
            }
        }
    }
    val i = pos.toInt().coerceIn(0, n - 1)
    val frac = pos - i
    val tA = dfa["t"]
    val ib: Int = if (dfb == null) 0 else if (synced) ((i.toDouble() / max(1, n - 1)) * (dfb.n - 1)).roundToInt().coerceIn(0, dfb.n - 1)
        else Np.searchsorted(dfb["t"], tA[i], false).coerceIn(0, dfb.n - 1)

    Column(Modifier.fillMaxSize().verticalScroll(rememberScrollState()).padding(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        if (dfb != null) {
            val d = Np.interp(lxy.first[i], lxyB!!.first, lxyB.second) - lxy.second[i]
            val txt = when {
                abs(d) < 0.0005 -> "══ DEAD HEAT ══"
                d > 0 -> "▷▷ △ A +${fixed(abs(d), 3)}s ahead of B ◁◁"
                else -> "▷▷ △ B +${fixed(abs(d), 3)}s ahead of A ◁◁"
            }
            Box(Modifier.fillMaxWidth().background(a.pnl2).padding(8.dp), contentAlignment = Alignment.Center) {
                Lbl(a, txt, if (abs(d) < 0.0005) a.dim else if (d > 0) a.cyn else a.yel, 14, true)
            }
        }
        InfoRow(a, "A", a.cyn, dfa, i)
        if (dfb != null) InfoRow(a, "B", a.yel, dfb, ib)
        if (hasMap) {
            BoxWithConstraints(Modifier.fillMaxWidth().height(330.dp).background(a.pnl)) {
                val dn = androidx.compose.ui.platform.LocalDensity.current
                val wPx = with(dn) { maxWidth.toPx() }.toDouble(); val hPx = with(dn) { maxHeight.toPx() }.toDouble()
                val xf = remember(s, dfb, wPx, hPx) { mapXf(listOfNotNull(dfa, dfb), wPx, hPx) }
                val paths = remember(s, xf, heat, deltas) { buildPaths(dfa, xf, deltas) }
                val bnd = remember(s) { Pair(Np.min(dfa["world_x"]), Np.max(dfa["world_x"])) }
                Canvas(Modifier.fillMaxSize()) {
                    if (boundary != null && boundary.isNotEmpty()) {
                        val dash = androidx.compose.ui.graphics.PathEffect.dashPathEffect(floatArrayOf(6f * density, 5f * density))
                        for (side in 0..1) {
                            val p = Path()
                            val all = boundary + boundary[0]
                            for ((k, b) in all.withIndex()) {
                                val ox = xf.x(if (side == 0) b.lx else b.rx); val oy = xf.y(if (side == 0) b.lz else b.rz)
                                if (k == 0) p.moveTo(ox, oy) else p.lineTo(ox, oy)
                            }
                            drawPath(p, a.dim.copy(alpha = 0.4f), style = Stroke(width = 1f * density, pathEffect = dash))
                        }
                    }
                    for ((c, p) in paths) drawPath(p, c, style = Stroke(width = 3.5f * density, cap = StrokeCap.Round))
                    if (dfb != null) drawLine(a.dim, Offset(xf.x(dfa["world_x"][i]), xf.y(dfa["world_z"][i])), Offset(xf.x(dfb["world_x"][ib]), xf.y(dfb["world_z"][ib])), 1.5f * density)
                    // start dot and end square
                    drawCircle(a.grn, 5f * density, Offset(xf.x(dfa["world_x"][0]), xf.y(dfa["world_z"][0])))
                    drawRect(a.acc, Offset(xf.x(dfa["world_x"][n - 1]) - 4f * density, xf.y(dfa["world_z"][n - 1]) - 4f * density), androidx.compose.ui.geometry.Size(8f * density, 8f * density))
                    // trail
                    val from = max(0, i - 50)
                    for (k in from until i) {
                        val al = (k - from).toFloat() / 50f
                        drawLine(a.cyn.copy(alpha = al), Offset(xf.x(dfa["world_x"][k]), xf.y(dfa["world_z"][k])), Offset(xf.x(dfa["world_x"][k + 1]), xf.y(dfa["world_z"][k + 1])), 2.5f * density)
                    }
                    val size = 0.022 * max(bnd.second - bnd.first, 1.0) * 1.0
                    carTri(dfa, i, frac, xf, size, a.cyn, bnd)
                    if (dfb != null) carTri(dfb, ib, 0.0, xf, size, a.yel, bnd)
                }
            }
        } else Box(Modifier.fillMaxWidth().height(80.dp).background(a.pnl), contentAlignment = Alignment.Center) { Lbl(a, "This lap has no GPS track", a.dim, 12) }

        Slider(
            value = (pos / max(1, n - 1)).toFloat(), onValueChange = { pos = it.toDouble() * (n - 1) }, valueRange = 0f..1f,
            colors = SliderDefaults.colors(thumbColor = a.cyn, activeTrackColor = a.cyn, inactiveTrackColor = a.dim2),
        )
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.fillMaxWidth()) {
            TBtn(a, "⏮", Modifier.weight(1f)) { pos = 0.0; playing = false }
            TBtn(a, if (playing) "⏸" else "▶", Modifier.weight(1f), on = playing) { if (pos >= n - 1) pos = 0.0; playing = !playing }
            TBtn(a, "${SPEEDS[spdIdx].toString().removeSuffix(".0")}×", Modifier.weight(1f)) { spdIdx = (spdIdx + 1) % SPEEDS.size }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp), modifier = Modifier.fillMaxWidth()) {
            TBtn(a, if (synced) "Synced" else "Real-Time", Modifier.weight(1f)) { synced = !synced }
            TBtn(a, "Heatmap", Modifier.weight(1f), on = heat) { heat = !heat }
            if (onSaveBoundary != null) TBtn(a, "Save Boundary", Modifier.weight(1f), on = boundary != null) { bMsg = onSaveBoundary() }
        }
        if (bMsg.isNotEmpty()) Lbl(a, bMsg, a.dim, 11)
        Spacer(Modifier.height(20.dp))
    }
}

private fun buildPaths(df: Frame, xf: MapXf, deltas: DoubleArray?): List<Pair<Color, Path>> {
    val n = df.n; val x = df["world_x"]; val z = df["world_z"]; val sp = df["speed_kmh"]
    val bins = 32
    val ps = Array(bins) { Path() }
    val used = BooleanArray(bins)
    var lo = Double.MAX_VALUE; var hi = -Double.MAX_VALUE
    val v = deltas ?: sp
    for (k in 0 until n) { lo = min(lo, v[k]); hi = max(hi, v[k]) }
    val span = if (deltas != null) max(1e-9, max(abs(lo), abs(hi))) else max(1e-9, hi - lo)
    for (k in 0 until n - 1) {
        val t = if (deltas != null) 0.5 + v[k] / (2 * span) else (v[k] - lo) / span
        val b = (t.coerceIn(0.0, 1.0) * (bins - 1)).roundToInt()
        ps[b].moveTo(xf.x(x[k]), xf.y(z[k])); ps[b].lineTo(xf.x(x[k + 1]), xf.y(z[k + 1])); used[b] = true
    }
    val cm = if (deltas != null) "RdYlGn_r" else "turbo"
    return (0 until bins).filter { used[it] }.map { Color(Cmaps.at(cm, it.toDouble() / (bins - 1))) to ps[it] }
}

private fun DrawScope.carTri(df: Frame, i: Int, frac: Double, xf: MapXf, size: Double, color: Color, bnd: Pair<Double, Double>) {
    val hdg: Double? = if (df.has("heading")) { val h = df["heading"]; if (Np.max(h) - Np.min(h) > 0.05) h[i] else null } else null
    val n = df.n; val x = df["world_x"]; val z = df["world_z"]
    val i2 = min(n - 1, i + 1)
    val px = x[i] + (x[i2] - x[i]) * frac; val pz = z[i] + (z[i2] - z[i]) * frac
    val a0 = max(0, i - 2); val a1 = min(n - 1, i + 2)
    var dx = x[a1] - x[a0]; var dz = z[a1] - z[a0]
    if (abs(dx) + abs(dz) < 1e-9) { dx = 0.0; dz = 1.0 }
    val th = if (hdg != null) hdg else atan2(dx, dz)
    val fx = sin(th); val fz = cos(th); val rx = cos(th); val rz = -sin(th)
    fun pt(lx: Double, lz: Double) = Offset(xf.x(px + (lz * fx + lx * rx) * size), xf.y(pz + (lz * fz + lx * rz) * size))
    val p = Path()
    val v0 = pt(0.0, 1.0); val v1 = pt(-0.55, -0.65); val v2 = pt(0.55, -0.65)
    p.moveTo(v0.x, v0.y); p.lineTo(v1.x, v1.y); p.lineTo(v2.x, v2.y); p.close()
    drawPath(p, color)
}

@Composable
private fun InfoRow(a: AP, name: String, c: Color, df: Frame, i: Int) {
    val ii = i.coerceIn(0, df.n - 1)
    val thr = if (df.has("throttle")) df["throttle"][ii] else 0.0
    val brk = if (df.has("brake")) df["brake"][ii] else 0.0
    Column(Modifier.fillMaxWidth().background(a.pnl).padding(8.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Lbl(a, name, c, 13, true)
            Lbl(a, LapMath.fmtTime(df["t"][ii], 2), a.fg, 12)
            Lbl(a, "${fixed(df["speed_kmh"][ii], 0)} km/h", a.fg, 12)
            Lbl(a, "G${df["gear"][ii].toInt()}", a.fg, 12)
            if (df.has("rpm")) Lbl(a, "${fixed(df["rpm"][ii], 0)} rpm", a.fg, 12)
            Lbl(a, "${fixed(ii.toDouble() / max(1, df.n - 1) * 100, 0)}%", a.dim, 12)
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp), verticalAlignment = Alignment.CenterVertically) {
            Lbl(a, "THR", a.grn, 10)
            Box(Modifier.weight(1f).height(8.dp).background(a.dim2)) { Box(Modifier.fillMaxHeight().fillMaxWidth(thr.toFloat().coerceIn(0f, 1f)).background(a.grn)) }
            Lbl(a, "BRK", a.acc, 10)
            Box(Modifier.weight(1f).height(8.dp).background(a.dim2)) { Box(Modifier.fillMaxHeight().fillMaxWidth(brk.toFloat().coerceIn(0f, 1f)).background(a.acc)) }
        }
    }
}
