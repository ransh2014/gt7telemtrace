package app.trace

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.unit.dp
import androidx.compose.ui.window.Dialog
import app.trace.telemetry.*
import kotlinx.coroutines.CoroutineScope
import kotlinx.coroutines.Dispatchers
import kotlinx.coroutines.delay
import kotlinx.coroutines.launch
import kotlinx.coroutines.withContext

/** "Create a Free Account": anonymous Supabase sign-up with just a display name (launcher onboarding / submit prompt). */
@Composable
fun CreateAccountDialog(a: AP, initial: String, onDone: (String) -> Unit, onDismiss: () -> Unit) {
    val ctx = LocalContext.current
    val scope = rememberCoroutineScope()
    var name by remember { mutableStateOf(initial) }
    var status by remember { mutableStateOf("") }
    var bad by remember { mutableStateOf(false) }
    var busy by remember { mutableStateOf(false) }
    Dialog(onDismissRequest = { if (!busy) onDismiss() }) {
        Column(Modifier.fillMaxWidth().background(a.bg).border(1.dp, a.dim2).padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Lbl(a, "Create a free account to submit laps", a.yel, 13, true)
            Lbl(a, "Just a display name -- no email or password needed.", a.dim, 11)
            Field(a, name, "display name", Modifier.fillMaxWidth()) { name = it }
            if (status.isNotEmpty()) Lbl(a, status, if (bad) a.acc else a.dim, 11)
            Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                TBtn(a, if (busy) "Creating…" else "Create Account", Modifier.weight(1f), on = !busy) {
                    if (busy) return@TBtn
                    val n = name.trim().split(Regex("\\s+")).filter { it.isNotEmpty() }.joinToString(" ").take(32)
                    if (n.isEmpty()) { status = "Enter a display name first."; bad = true; return@TBtn }
                    busy = true; bad = false; status = "Creating account…"
                    scope.launch {
                        val (session, saved) = withContext(Dispatchers.IO) {
                            val s = SupaAuth.signUpAnonymous()
                            s to (s?.let { SupaAuth.setDisplayName(it.access, it.userId, n) } ?: false)
                        }
                        busy = false
                        if (session == null) { status = "Couldn't reach the server -- try again."; bad = true; return@launch }
                        Account.saveSession(ctx, session); Account.setPsn(ctx, n)
                        if (!saved) { status = "Account created -- the display name didn't save. Submissions use the name you type when submitting, so this doesn't matter. Continuing…"; bad = true; delay(1800) }
                        onDone(n)
                    }
                }
                TBtn(a, "Cancel", Modifier.weight(1f)) { if (!busy) onDismiss() }
            }
        }
    }
}

/** Lap Analyst leaderboard panel: top 10 (+ ghost download), submit, community line. */
@Composable
fun LeaderboardCard(ctl: LapController, a: AP, scope: CoroutineScope) {
    val ctx = LocalContext.current
    val s = ctl.a
    var dlg by remember { mutableStateOf<String?>(null) }   // "account" | "psn"
    var psn by remember { mutableStateOf(Account.psn(ctx)) }
    LaunchedEffect(ctl.a) { if (ctl.a != null) ctl.refreshTop10() }
    Card(a, "LEADERBOARD", accent = a.yel) {
        if (s == null) { Lbl(a, "Load Lap A to see the top 10", a.dim, 11); return@Card }
        val (car, track) = ctl.carTrack(s)
        Lbl(a, "$car @ $track", a.cyn, 11)
        val rows = ctl.top10
        if (ctl.top10Busy || rows == null) Lbl(a, "loading top 10…", a.dim, 11)
        else if (rows.isEmpty()) Lbl(a, "no times yet — be the first!", a.dim, 11)
        else rows.forEachIndexed { k, r ->
            val i = k + 1
            val col = if (i == 1) a.yel else if (i <= 3) a.fg else a.dim
            Row(Modifier.fillMaxWidth(), verticalAlignment = Alignment.CenterVertically) {
                Lbl(a, "$i.", col, 12, modifier = Modifier.width(30.dp))
                Lbl(a, LapMath.fmtTime(r.ms / 1000.0), col, 12, modifier = Modifier.width(96.dp))
                Lbl(a, r.psn.ifEmpty { "?" }, col, 12, modifier = Modifier.weight(1f))
                Box(Modifier.size(36.dp).background(a.pnl2).clickable { scope.launch { ctl.downloadGhost(r) } }, contentAlignment = Alignment.Center) { Lbl(a, "⬇", a.cyn, 14, true) }
            }
        }
        Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
            TBtn(a, "Submit lap", Modifier.weight(1f), color = a.grn) {
                val err = Leaderboard.submissionError(s.meta, s.df)
                if (err != null) ctl.lbMsg = err
                else dlg = if (Account.load(ctx).signedIn) "psn" else "account"
            }
            TBtn(a, "Community line", Modifier.weight(1f), color = a.cyn) { scope.launch { ctl.loadConsensus() } }
        }
        TBtn(a, "Refresh top 10", Modifier.fillMaxWidth()) { scope.launch { ctl.refreshTop10() } }
        ctl.lbMsg?.let { m -> Lbl(a, m, a.org, 11, modifier = Modifier.clickable { ctl.lbMsg = null }) }
    }
    when (dlg) {
        "account" -> CreateAccountDialog(a, psn, onDone = { n -> psn = n; dlg = "psn" }, onDismiss = { dlg = null })
        "psn" -> {
            val slot = ctl.a
            if (slot != null) Dialog(onDismissRequest = { dlg = null }) {
                val (car, track) = ctl.carTrack(slot)
                Column(Modifier.fillMaxWidth().background(a.bg).border(1.dp, a.dim2).padding(14.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
                    Lbl(a, "Submit to Leaderboard", a.yel, 13, true)
                    Lbl(a, "$car @ $track  —  " + fixed(numOf(slot.meta["lap_time_s"]), 3) + "s", a.fg, 11)
                    Lbl(a, "PSN name:", a.dim, 11)
                    Field(a, psn, "PSN name", Modifier.fillMaxWidth()) { psn = it }
                    Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                        TBtn(a, "Submit", Modifier.weight(1f), on = true) {
                            val n = psn
                            dlg = null
                            scope.launch { ctl.lbMsg = ctl.submit(ctx, n) }
                        }
                        TBtn(a, "Cancel", Modifier.weight(1f)) { dlg = null }
                    }
                }
            }
        }
    }
}
