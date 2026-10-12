package app.trace

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.getValue
import androidx.compose.runtime.setValue
import kotlinx.coroutines.launch
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.drawBehind
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp

enum class Tool { LIVE, LAPS, RACE }

@Composable
fun Launcher(a: AP, light: Boolean, lapsDir: java.io.File, onToggleTheme: () -> Unit, onRecords: () -> Unit, onOpen: (Tool) -> Unit) {
    val sub = Color(0xFF6b7090)
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var signed by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(Account.load(ctx).signedIn) }
    var dlg by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(false) }
    var msg by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf("") }
    val saveZip = androidx.activity.compose.rememberLauncherForActivityResult(androidx.activity.result.contract.ActivityResultContracts.CreateDocument("application/zip")) { u: android.net.Uri? ->
        if (u != null) scope.launch(kotlinx.coroutines.Dispatchers.IO) {
            val n = ctx.contentResolver.openOutputStream(u)?.use { Exports.backupLaps(lapsDir, it) } ?: 0
            msg = "Backed up $n files."
        }
    }
    if (dlg) CreateAccountDialog(a, "", { dlg = false; signed = true; msg = "Account created." }) { dlg = false }
    Column(
        Modifier.fillMaxSize().background(a.bg).statusBarsPadding().navigationBarsPadding()
            .verticalScroll(rememberScrollState()).padding(8.dp),
        verticalArrangement = Arrangement.spacedBy(8.dp),
    ) {
        Text("TRACE", Modifier.fillMaxWidth().padding(top = 28.dp), color = a.cyn, fontFamily = Mono,
            fontWeight = FontWeight.Bold, fontSize = 44.sp, textAlign = TextAlign.Center)
        Text("GT7 Telemetry Suite", Modifier.fillMaxWidth().padding(bottom = 8.dp), color = sub,
            fontFamily = Mono, fontSize = 15.sp, textAlign = TextAlign.Center)
        ToolCard(a, "Live Dashboard", "Real-time telemetry overlay while you drive.", a.cyn) { onOpen(Tool.LIVE) }
        ToolCard(a, "Lap Analyst", "Compare laps, chart every input, submit to the leaderboard.", a.prp) { onOpen(Tool.LAPS) }
        ToolCard(a, "Race Analyst", "Full-race lap splits, stats and notes.", a.acc) { onOpen(Tool.RACE) }
        Spacer(Modifier.height(6.dp))
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            FootBtn(a, if (signed) "Log Out" else "Create Free Account", Modifier.weight(1f)) {
                if (signed) { Account.clear(ctx); signed = false; msg = "Logged out." } else dlg = true
            }
            FootBtn(a, "Backup Laps", Modifier.weight(1f)) {
                if (!Exports.hasLaps(lapsDir)) msg = "No laps found to back up yet."
                else saveZip.launch("trace_laps_backup_${java.text.SimpleDateFormat("yyyyMMdd_HHmmss", java.util.Locale.ROOT).format(java.util.Date())}.zip")
            }
        }
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            FootBtn(a, "Records", Modifier.weight(1f), onRecords)
            FootBtn(a, if (light) "Dark" else "Light", Modifier.weight(1f), onToggleTheme)
        }
        Updates.latest?.let { v ->
            Text("Update available: v$v -- tap to open", Modifier.fillMaxWidth().clickable { ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(Updates.url))) }.padding(6.dp),
                color = a.cyn, fontFamily = Mono, fontSize = 12.sp, textAlign = TextAlign.Center)
        }
        if (msg.isNotEmpty()) Text(msg, Modifier.fillMaxWidth(), color = sub, fontFamily = Mono, fontSize = 12.sp, textAlign = TextAlign.Center)
    }
}

@Composable
private fun FootBtn(a: AP, text: String, m: Modifier, onClick: () -> Unit) =
    Box(m.height(46.dp).background(a.pnl2).clickable(onClick = onClick), contentAlignment = Alignment.Center) {
        Text(text, color = a.fg, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 13.sp)
    }

@Composable
private fun ToolCard(a: AP, title: String, desc: String, accent: Color, onClick: () -> Unit) {
    Column(
        Modifier.fillMaxWidth().heightIn(min = 150.dp).background(a.pnl)
            .drawBehind { drawLine(accent, Offset(0f, 1.5.dp.toPx()), Offset(size.width, 1.5.dp.toPx()), 3.dp.toPx()) }
            .clickable(onClick = onClick).padding(16.dp, 16.dp, 16.dp, 14.dp),
        verticalArrangement = Arrangement.SpaceBetween,
    ) {
        Column {
            Text(title, color = accent, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 22.sp)
            Spacer(Modifier.height(6.dp))
            Text(desc, color = Color(0xFF6b7090), fontFamily = Mono, fontSize = 13.sp, lineHeight = 18.sp)
        }
        Text("Launch →", Modifier.padding(top = 14.dp), color = accent, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 15.sp)
    }
}
