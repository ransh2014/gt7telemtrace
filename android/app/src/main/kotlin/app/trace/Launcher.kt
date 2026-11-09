package app.trace

import androidx.compose.foundation.BorderStroke
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.Text
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.style.TextDecoration
import app.trace.telemetry.Versions
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
fun Launcher(a: AP, light: Boolean, lapsDir: java.io.File, version: String, ignoreUpdates: Boolean, onIgnoreUpdates: (Boolean) -> Unit, onToggleTheme: () -> Unit, onRecords: () -> Unit, onOpen: (Tool) -> Unit) {
    val sub = Color(0xFF96949c)
    val ctx = androidx.compose.ui.platform.LocalContext.current
    val scope = androidx.compose.runtime.rememberCoroutineScope()
    var signed by androidx.compose.runtime.remember { androidx.compose.runtime.mutableStateOf(Account.load(ctx).signedIn) }
    val psn = Account.load(ctx).psn
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
        Text(stringResource(R.string.launcher_title), Modifier.fillMaxWidth().padding(top = 28.dp), color = a.cyn, fontFamily = Display,
            fontWeight = FontWeight.Bold, fontSize = 44.sp, textAlign = TextAlign.Center)
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
            Text(stringResource(R.string.version_fmt, version), color = sub, fontFamily = Mono, fontSize = 12.sp)
            if (!ignoreUpdates) when (val st = Updates.status) {
                is Versions.Status.Current -> Text(stringResource(R.string.update_current), Modifier.padding(start = 8.dp), color = a.grn, fontFamily = Mono, fontSize = 12.sp)
                is Versions.Status.Available -> Text(stringResource(R.string.update_available_fmt, st.version),
                    Modifier.padding(start = 8.dp).clickable { ctx.startActivity(android.content.Intent(android.content.Intent.ACTION_VIEW, android.net.Uri.parse(st.url))) },
                    color = a.blue, fontFamily = Mono, fontSize = 12.sp, textDecoration = TextDecoration.Underline)
                null -> {}
            }
        }
        Row(Modifier.fillMaxWidth().clickable { onIgnoreUpdates(!ignoreUpdates) }, horizontalArrangement = Arrangement.Center, verticalAlignment = Alignment.CenterVertically) {
            Checkbox(ignoreUpdates, { onIgnoreUpdates(it) }, colors = CheckboxDefaults.colors(checkedColor = a.acc, uncheckedColor = a.dim, checkmarkColor = Color.White))
            Text(stringResource(R.string.ignore_update_checks), color = sub, fontFamily = Mono, fontSize = 11.sp)
        }
        Text(stringResource(R.string.launcher_subtitle), Modifier.fillMaxWidth().padding(bottom = if (signed && psn.isNotEmpty()) 0.dp else 8.dp), color = sub,
            fontFamily = Mono, fontSize = 15.sp, textAlign = TextAlign.Center)
        if (signed && psn.isNotEmpty()) Text(stringResource(R.string.signed_in_as_fmt, psn), Modifier.fillMaxWidth().padding(bottom = 8.dp), color = sub, fontFamily = Mono, fontSize = 12.sp, textAlign = TextAlign.Center)
        ToolCard(a, stringResource(R.string.tool_live), stringResource(R.string.card_live_desc), a.cyn) { onOpen(Tool.LIVE) }
        ToolCard(a, stringResource(R.string.tool_lap), stringResource(R.string.card_lap_desc), a.prp) { onOpen(Tool.LAPS) }
        ToolCard(a, stringResource(R.string.tool_race), stringResource(R.string.card_race_desc), a.acc) { onOpen(Tool.RACE) }
        Spacer(Modifier.height(6.dp))
        // desktop footer, left to right: Log Out / Create Free Account, Backup Laps, Records, theme toggle
        Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.SpaceBetween) {
            FootLink(a, if (signed) stringResource(R.string.log_out) else stringResource(R.string.create_account), if (signed) sub else a.cyn) {
                if (signed) { Account.clear(ctx); signed = false; msg = "Logged out." } else dlg = true
            }
            FootLink(a, stringResource(R.string.backup_laps), sub) {
                if (!Exports.hasLaps(lapsDir)) msg = "No laps found to back up yet."
                else saveZip.launch("trace_laps_backup_${java.text.SimpleDateFormat("yyyyMMdd_HHmmss", java.util.Locale.ROOT).format(java.util.Date())}.zip")
            }
            FootLink(a, stringResource(R.string.records), sub, onRecords)
            FootLink(a, if (light) stringResource(R.string.theme_dark) else stringResource(R.string.theme_light), sub, onToggleTheme)
        }
        Text(stringResource(R.string.footer_note), Modifier.fillMaxWidth().padding(top = 6.dp), color = a.dim2, fontFamily = Body, fontSize = 10.sp)
        if (msg.isNotEmpty()) Text(msg, Modifier.fillMaxWidth(), color = sub, fontFamily = Mono, fontSize = 12.sp, textAlign = TextAlign.Center)
    }
}

@Composable
private fun FootLink(a: AP, text: String, color: Color, onClick: () -> Unit) =
    Text(text, Modifier.clickable(onClick = onClick).padding(vertical = 10.dp, horizontal = 2.dp), color = color, fontFamily = Body, fontSize = 12.sp, textDecoration = TextDecoration.Underline, maxLines = 1)

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
            Text(desc, color = Color(0xFF96949c), fontFamily = Mono, fontSize = 13.sp, lineHeight = 18.sp)
        }
        Text(stringResource(R.string.launch), Modifier.padding(top = 14.dp), color = accent, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 15.sp)
    }
}
