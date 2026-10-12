package app.trace

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.trace.telemetry.LapIndex
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.withContext
import java.io.File

/** records.py: best lap per (car, track) across everything saved. */
@Composable
fun RecordsScreen(a: AP, lapsDir: File, onBack: () -> Unit) {
    var rows by remember { mutableStateOf<List<LapIndex.Row>?>(null) }
    var filter by remember { mutableStateOf("") }
    var old by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) { rows = withContext(Dispatchers.IO) { LapIndex.scanAll(lapsDir) } }
    val all = remember(rows, old) { rows?.let { LapIndex.bestByCarTrack(it, old) } ?: emptyList() }
    val shown = remember(all, filter) {
        val q = filter.trim().lowercase()
        if (q.isEmpty()) all else all.filter { q in it.track.lowercase() || q in it.car.lowercase() }
    }
    Column(Modifier.fillMaxSize().background(a.bg).statusBarsPadding().navigationBarsPadding()) {
        Row(Modifier.fillMaxWidth().background(a.pnl2).padding(12.dp, 8.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
            Text("‹", Modifier.clickable(onClick = onBack).padding(end = 6.dp), color = a.cyn, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 24.sp)
            Lbl(a, "GT7", a.acc, 16, true); Lbl(a, "RECORDS", a.cyn, 16, true)
        }
        Column(Modifier.padding(6.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Field(a, filter, "filter by track or car", Modifier.fillMaxWidth()) { filter = it }
            TBtn(a, if (old) "Including pre-era laps" else "Include pre-era laps", Modifier.fillMaxWidth(), on = old) { old = !old }
            Lbl(a, when {
                rows == null -> "Scanning…"
                all.isEmpty() -> "No saved laps yet -- record some in the Live Dashboard"
                else -> "${shown.size} of ${all.size} records"
            }, a.dim, 11)
        }
        LazyColumn(Modifier.weight(1f).fillMaxWidth().padding(horizontal = 6.dp)) {
            items(shown) { r ->
                Column(Modifier.fillMaxWidth().padding(vertical = 6.dp)) {
                    Row(Modifier.fillMaxWidth()) {
                        Lbl(a, r.track, a.cyn, 12, true, modifier = Modifier.weight(1f))
                        Lbl(a, r.time, a.grn, 14, true)
                    }
                    Lbl(a, r.car, a.fg, 11)
                    Lbl(a, "${r.laps} lap${if (r.laps == 1) "" else "s"} · set ${r.date}", a.dim, 10)
                }
                Box(Modifier.fillMaxWidth().height(1.dp).background(a.dim2))
            }
        }
    }
}
