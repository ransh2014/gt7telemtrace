package app.trace

import android.os.Bundle
import android.view.WindowManager
import androidx.activity.ComponentActivity
import androidx.activity.compose.BackHandler
import androidx.activity.compose.setContent
import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.res.stringResource
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.trace.telemetry.Packet
import app.trace.telemetry.TelemetryClient
import kotlinx.coroutines.delay

class MainActivity : ComponentActivity() {
    private lateinit var live: LiveController
    override fun onCreate(savedInstanceState: Bundle?) {
        super.onCreate(savedInstanceState)
        if (android.os.Build.VERSION.SDK_INT >= 33 && checkSelfPermission(android.Manifest.permission.POST_NOTIFICATIONS) != android.content.pm.PackageManager.PERMISSION_GRANTED)
            requestPermissions(arrayOf(android.Manifest.permission.POST_NOTIFICATIONS), 7)
        live = LiveController(applicationContext)
        live.start()
        val lapCtl = LapController(live.lapsDir)
        val raceCtl = RaceController(live.lapsDir)
        setContent {
            var tool by remember { mutableStateOf<Tool?>(null) }
            var light by remember { mutableStateOf(false) }
            val a = if (light) AP.Light else AP.Dark
            LaunchedEffect(live.online, live.keepAwake) {
                if (live.online || live.keepAwake) window.addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON) else window.clearFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON)
            }
            var records by remember { mutableStateOf(false) }
            // desktop window-title pattern "TRACE <version> - <Tool name>", shown in the recent-apps switcher
            val titleFmt = getString(R.string.window_title_fmt)
            val names = mapOf(null to getString(R.string.launcher_window_tool), Tool.LIVE to getString(R.string.tool_live), Tool.LAPS to getString(R.string.tool_lap), Tool.RACE to getString(R.string.tool_race))
            LaunchedEffect(tool) { setTaskDescription(android.app.ActivityManager.TaskDescription(titleFmt.format(live.versionName(), names[tool]))) }
            LaunchedEffect(tool) {
                val t = tool
                if (t != null && live.analyticsOn) Analytics.track(when (t) { Tool.LIVE -> "dashboard"; Tool.LAPS -> "lap_analyst"; Tool.RACE -> "race_analyst" }, live.versionName())
            }
            BackHandler(enabled = tool != null || records) { if (records) records = false else tool = null }
            if (records) { RecordsScreen(a, live.lapsDir) { records = false }; return@setContent }
            Column(Modifier.fillMaxSize().background(if (tool == Tool.LIVE) D.bg else a.bg)) {
                Box(Modifier.weight(1f)) {
                    when (tool) {
                        null -> Launcher(a, light, live.lapsDir, live.versionName(), !live.updateOn, { live.onUpdate(!it) }, { light = !light }, { records = true }) { tool = it }
                        Tool.LIVE -> LiveScreen(live) { tool = null }
                        Tool.LAPS -> LapScreen(lapCtl, a) { tool = null }
                        Tool.RACE -> RaceScreen(raceCtl, a) { tool = null }
                    }
                }
                if (tool != null) NavBar(tool!!) { tool = it }
            }
        }
    }
    override fun onDestroy() { live.stop(); super.onDestroy() }
}

@Composable
private fun NavBar(cur: Tool, onPick: (Tool) -> Unit) {
    Row(Modifier.fillMaxWidth().background(D.hdr).navigationBarsPadding().padding(8.dp, 6.dp), horizontalArrangement = Arrangement.spacedBy(4.dp)) {
        for ((t, n) in listOf(Tool.LIVE to stringResource(R.string.tool_live), Tool.LAPS to stringResource(R.string.tool_lap), Tool.RACE to stringResource(R.string.tool_race))) {
            Box(Modifier.weight(1f).height(48.dp).background(if (t == cur) D.acc else D.hdr).clickable { onPick(t) }, contentAlignment = Alignment.Center) {
                Text(n, color = if (t == cur) androidx.compose.ui.graphics.Color.White else D.fg, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 12.sp, maxLines = 1)
            }
        }
    }
}
