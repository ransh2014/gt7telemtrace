package app.trace

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.rememberScrollState
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.foundation.verticalScroll
import androidx.compose.material3.Checkbox
import androidx.compose.material3.CheckboxDefaults
import androidx.compose.material3.DropdownMenu
import androidx.compose.material3.DropdownMenuItem
import androidx.compose.material3.AlertDialog
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.alpha
import androidx.compose.ui.geometry.Offset
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.Path
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.graphics.drawscope.Stroke
import androidx.compose.ui.layout.Layout
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.text.style.TextAlign
import androidx.compose.ui.text.style.TextOverflow
import androidx.compose.ui.unit.Constraints
import androidx.compose.ui.unit.Dp
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import app.trace.telemetry.Packet
import app.trace.telemetry.TraceUtil
import app.trace.telemetry.TraceUtil.Band
import java.util.Locale

private fun Packet.d(k: String): Double = (this[k] as? Number)?.toDouble() ?: 0.0
private fun Packet.i(k: String): Int = (this[k] as? Number)?.toInt() ?: 0
private fun Packet.b(k: String): Boolean = this[k] as? Boolean ?: false
private fun f(v: Double, n: Int) = "%.${n}f".format(Locale.ROOT, v)
private fun sf(v: Double, n: Int) = "%+.${n}f".format(Locale.ROOT, v)
private fun bandColor(b: Band) = when (b) { Band.NONE -> D.dim; Band.COLD -> D.hi; Band.GOOD -> D.good; Band.WARM -> D.amb; Band.HOT, Band.BAD -> D.red }

@Composable
fun LiveScreen(c: LiveController, onBack: () -> Unit) {
    val p = c.d; val online = c.online; val st = c.strat
    val has = p.isNotEmpty() && online
    val gearRatios = (p["gear_ratios"] as? List<*>)?.map { (it as? Number)?.toDouble() ?: 0.0 } ?: emptyList()
    val surf = (p["surface_type"] as? List<*>)?.map { it.toString() }
    val wsa = (p["wheel_steering_angle"] as? List<*>)?.map { (it as? Number)?.toDouble() ?: 0.0 }
    var detecting by remember { mutableStateOf(false) }
    var showSummary by remember { mutableStateOf(false) }

    val spd = p.d("speed_kmh"); val gear = p.i("gear"); val rawSug = p.i("suggested_gear")
    val sug = if (rawSug == 15) 0 else rawSug
    val rpm = p.d("rpm"); val mrpm = p.d("max_rpm").let { if (it == 0.0) 1.0 else it }
    val thr = p.d("throttle"); val brk = p.d("brake"); val clt = p.d("clutch")
    val curLap = p.i("lap_number"); val totLap = p.i("total_laps")
    val curPos = p.i("current_position"); val totPos = p.i("total_positions")
    val bestMs = if (p.containsKey("best_lap_ms")) p.i("best_lap_ms") else -1
    val lastMs = if (p.containsKey("last_lap_ms")) p.i("last_lap_ms") else -1
    val topSpd = p.i("est_top_speed")
    val fuelR = p.d("fuel_remaining"); val fuelC = p.d("fuel_capacity"); val isEv = p.b("is_ev")
    val fuelStr = if (fuelC == 100.0 || isEv) f(fuelR, 1) + "%" + (if (isEv) " batt" else "") else f(fuelR, 1) + "/" + f(fuelC, 0)
    val unit = if (fuelC == 100.0 || isEv) "%" else ""
    var fl = st.lapsOfFuel
    if (fl == null && fuelR > 0 && c.recorder.fuelPerLap > 0) fl = fuelR / c.recorder.fuelPerLap
    val fuelLapsNum = fl ?: 999.0
    val temps = listOf("tyre_temp_fl", "tyre_temp_fr", "tyre_temp_rl", "tyre_temp_rr").map { p.d(it) }.filter { it > 0 }
    val hasHot = temps.any { it > 100 }; val hasCold = temps.any { it < 60 }; val hasFuel = has && fuelLapsNum < 2

    Column(Modifier.fillMaxSize().background(D.bg).statusBarsPadding().navigationBarsPadding()
        .verticalScroll(rememberScrollState()).padding(8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {

        Row(Modifier.fillMaxWidth().background(D.hdr).padding(horizontal = 12.dp, vertical = 10.dp), verticalAlignment = Alignment.CenterVertically) {
            Text("‹", Modifier.clickable(onClick = onBack).padding(end = 10.dp), color = D.acc, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 24.sp)
            Text("TRACE  LIVE DASHBOARD", Modifier.weight(1f), color = D.acc, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 15.sp, maxLines = 1)
            Text(if (online) "● LIVE" else "● OFFLINE", color = if (online) D.good else D.dim, fontFamily = Mono, fontSize = 11.sp)
        }

        c.battWarn?.let { pct ->
            Row(Modifier.fillMaxWidth().background(Color(0xFFc0392b)).padding(10.dp, 6.dp), verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
                Text("LOW BATTERY ($pct%) - plug in your charger now, or this lap/race will be lost when the phone dies", Modifier.weight(1f), color = Color.White, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 11.sp)
                DBtn("OK, I'll charge", Modifier.width(120.dp)) { c.onBatteryAck() }
            }
        }

        Grid(2, 6.dp) {
            DBtn(if (c.raceRec) "Stop Race Recording" else "Record Race", bg = if (c.raceRec) D.acc else D.tile, fg = if (c.raceRec) Color.White else D.fg) { c.toggleRace() }
            DBtn(when (c.recLapState) { 1 -> "Waiting for start line..."; 2 -> "Stop Recording"; else -> "Record Lap" },
                bg = when (c.recLapState) { 1 -> D.amb; 2 -> D.acc; else -> D.tile }, fg = if (c.recLapState == 1) Color.Black else if (c.recLapState == 2) Color.White else D.fg) { c.toggleLap() }
            DBtn("Session Summary") { showSummary = true }
            DBtn("Export Session") { c.exportSession() }
        }

        Column(Modifier.fillMaxWidth().background(D.hdr).padding(10.dp, 8.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            Field("CONSOLE IP") {
                Row(horizontalArrangement = Arrangement.spacedBy(6.dp)) {
                    DInput(c.ip, { c.ip = it; c.onIpChange() }, Modifier.weight(1f))
                    DBtn(if (detecting) "..." else "Auto-Detect", Modifier.width(112.dp), fg = D.hi) {
                        if (!detecting) { detecting = true; c.discover { detecting = false } }
                    }
                }
            }
            Field("TRACK") { Picker(c.track, c.db.allTrackNames(), { c.onTrack(it) }, Modifier.fillMaxWidth(), D.fg) }
            Field("CAR") { DInput(c.car, { c.onCar(it) }, Modifier.fillMaxWidth(), color = D.fg) }
            Field("REC RATE") {
                var open by remember { mutableStateOf(false) }
                Box { DInput("${c.rate} Hz", {}, Modifier.fillMaxWidth().clickable { open = true }, readOnly = true)
                    DropdownMenu(open, { open = false }) { for (r in listOf(10, 20, 30, 60)) DropdownMenuItem({ Text("$r Hz", fontFamily = Mono) }, { c.onRate(r); open = false }) } }
            }
            Grid(2, 8.dp) { Check("ALLOW REMOTE", c.remoteOn) { c.onRemote(it) }; Check("METRICS :9109", c.metricsOn) { c.onMetrics(it) } }
            Text("PREFERENCES", color = D.dim, fontFamily = Mono, fontSize = 10.sp)
            Grid(2, 8.dp) {
                Check("VOICE ALERTS", c.voiceOn) { c.onVoice(it) }; Check("NOTIFICATIONS", c.notifyOn) { c.onNotify(it) }; Check("TOP-LAP GHOST", c.ghostEnabled) { c.onGhostToggle(it) }
                Check("CHECK FOR UPDATES", c.updateOn) { c.onUpdate(it) }; Check("SHARE USAGE DATA", c.analyticsOn) { c.onAnalytics(it) }; Check("DEBUG LOG", c.debugOn) { c.onDebug(it) }
                Check("KEEP AWAKE", c.keepAwake) { c.onKeepAwake(it) }
            }
        }

        Row(Modifier.fillMaxWidth().background(D.pnl).padding(12.dp, 8.dp), verticalAlignment = Alignment.CenterVertically) {
            Row(Modifier.weight(1f), verticalAlignment = Alignment.Bottom) {
                Text("%.0f".format(Locale.ROOT, spd), color = D.hi, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 60.sp, maxLines = 1)
                Text("km/h", Modifier.padding(start = 4.dp, bottom = 10.dp), color = D.dim, fontFamily = Mono, fontSize = 12.sp)
            }
            Column(horizontalAlignment = Alignment.CenterHorizontally) {
                Text("SUGGEST", color = D.dim, fontFamily = Mono, fontSize = 10.sp)
                Text(if (sug > 0) sug.toString() else "--", color = D.amb, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 20.sp)
            }
            Row(Modifier.weight(1f), horizontalArrangement = Arrangement.End, verticalAlignment = Alignment.Bottom) {
                Text("gear", Modifier.padding(end = 4.dp, bottom = 10.dp), color = D.dim, fontFamily = Mono, fontSize = 12.sp)
                Text(if (gear > 0) gear.toString() else "N", color = D.amb, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 60.sp)
            }
        }

        val deltaV = c.delta
        Grid(4, 8.dp, Modifier.fillMaxWidth().background(D.pnl).padding(horizontal = 4.dp, vertical = 10.dp)) {
            Stat("LAP", if (has) "$curLap/$totLap" else "--")
            Stat("CURRENT", if (c.curElapsed > 0) TraceUtil.msToLapTime((c.curElapsed * 1000).toInt()) else "--", D.hi)
            Stat("BEST", TraceUtil.msToLapTime(bestMs), D.good)
            Stat("LAST", TraceUtil.msToLapTime(lastMs))
            Stat("GRID", if (curPos > 0 && totPos > 0) "P$curPos/$totPos" else "--")
            Stat("TOP SPD", if (topSpd != 0) "$topSpd km/h" else "--", D.hi)
            Stat("DELTA", if (deltaV != null) sf(deltaV, 2) + "s" else "--", if (deltaV == null) D.fg else if (deltaV > 0) D.red else D.good)
            Stat("VS TOP", c.ghostGap?.let { sf(it, 2) + "s" } ?: "--", c.ghostGap?.let { if (it > 0) D.red else D.good } ?: D.fg)
        }

        Column(Modifier.fillMaxWidth().padding(horizontal = 4.dp), verticalArrangement = Arrangement.spacedBy(4.dp)) {
            Bar("RPM", (rpm / mrpm).coerceIn(0.0, 1.0), D.red, f(rpm, 0) + " / " + mrpm.toInt())
            Bar("THROTTLE", thr, D.good, f(thr * 100, 0) + "%")
            Bar("BRAKE", brk, D.red, f(brk * 100, 0) + "%")
            Bar("CLUTCH", clt.coerceIn(0.0, 1.0), D.amb, f(clt * 100, 0) + "%")
        }

        Frame("TYRES") { Grid(4, 8.dp) {
            for ((n, k, s) in listOf(Triple("FL", "tyre_temp_fl", "tyre_slip_fl"), Triple("FR", "tyre_temp_fr", "tyre_slip_fr"), Triple("RL", "tyre_temp_rl", "tyre_slip_rl"), Triple("RR", "tyre_temp_rr", "tyre_slip_rr"))) {
                val t = p.d(k); val sl = if (p.containsKey(s)) p.d(s) else 1.0
                Cell(n, if (t > 0) f(t, 0) + "C" else "--C", big = true, sub = "slip " + f(sl, 2), color = bandColor(TraceUtil.tempBand(t)), subColor = bandColor(TraceUtil.slipBand(sl)))
            }
        } }
        Frame("SUSPENSION (mm)") { Grid(4, 8.dp) {
            Cell("FL", f(p.d("susp_fl") * 1000, 0)); Cell("FR", f(p.d("susp_fr") * 1000, 0)); Cell("RL", f(p.d("susp_rl") * 1000, 0)); Cell("RR", f(p.d("susp_rr") * 1000, 0))
        } }
        Frame("DRIVER AIDS") { Grid(3, 8.dp) {
            for ((k, n) in listOf("handbrake_active" to "HANDBRAKE", "asm_active" to "ASM", "tcs_active" to "TCS", "rev_limit_alert" to "REV LIMIT", "lights_active" to "LIGHTS", "high_beams" to "HIGH BEAM"))
                Cell(n, if (p.b(k)) "YES" else "NO", color = if (p.b(k)) D.acc else D.dim)
        } }
        Frame("ENGINE AND FLUIDS") {
            Grid(4, 8.dp) {
                Cell("FUEL", fuelStr); Cell("FUEL LAPS", if (fuelLapsNum < 999) f(fuelLapsNum, 1) else "--")
                Cell("BOOST bar", if (p.d("boost") > 0) f(p.d("boost"), 2) else "--"); Cell("OIL C*", f(p.d("oil_temp"), 0))
                Cell("WATER C*", f(p.d("water_temp"), 0)); Cell("OIL bar", f(p.d("oil_pressure"), 1)); Cell("RIDE mm", f(p.d("ride_height_mm"), 0))
            }
            Text("* GT7 sends fixed values for these (85 / 110 C) -- not simulated", Modifier.padding(top = 4.dp), color = D.dim, fontFamily = Mono, fontSize = 8.sp)
        }
        Frame("STRATEGY (est.)") {
            val rough = if (st.fuelBasisLaps < 3) "~" else ""
            val fpl = st.fuelPerLap; val lof = st.lapsOfFuel; val spare = st.spareLaps; val pit = st.pitByLap
            Grid(4, 8.dp) {
                Cell("FUEL/LAP", if (fpl != null && fpl != 0.0) rough + f(fpl, 2) + unit else "--", color = if (fpl != null && fpl != 0.0) D.fg else D.dim)
                Cell("FUEL LAPS", if (lof != null) rough + f(lof, 1) else "--", color = if (lof == null) D.dim else if (lof < 2) D.red else if (lof < 4) D.amb else D.fg)
                when {
                    spare == null -> Cell("FINISH", "--", color = D.dim)
                    spare < 0 -> Cell("FINISH", "-" + f(Math.abs(spare), 1) + " laps", color = D.red)
                    st.stopNeeded == true -> Cell("FINISH", "TIGHT", color = D.amb)
                    else -> Cell("FINISH", "+" + f(spare, 1) + " laps", color = D.good)
                }
                when {
                    pit != null -> Cell("PIT BY", "LAP $pit", color = if (pit <= curLap) D.red else D.amb)
                    st.stopNeeded == false -> Cell("PIT BY", "NO STOP", color = D.good)
                    else -> Cell("PIT BY", "--", color = D.dim)
                }
                Cell("STINT", "${st.stintAge} laps")
                Cell("PACE LOSS", st.paceLossS?.let { "+" + f(it, 2) + "s" } ?: "--", color = if (st.paceLossS != null) D.fg else D.dim)
                Cell("TREND", st.trendSPerLap?.let { sf(it, 2) + "s/lap" } ?: "--", color = st.trendSPerLap?.let { if (it > 0.15) D.amb else D.fg } ?: D.dim)
                Cell("TYRES", st.tyreState ?: "--", color = when (st.tyreState) { "Fresh", "Good" -> D.good; "Fading" -> D.amb; "Worn" -> D.red; else -> D.dim })
            }
            val h = st.heat
            Text(if (h != null) "Tyre temps last lap: front ${f(h.frontAvg, 0)}C / rear ${f(h.rearAvg, 0)}C  | hot ${f(h.pctHot, 0)}%  cold ${f(h.pctCold, 0)}% of the lap" else "Tyre temps last lap: --",
                Modifier.padding(top = 6.dp), color = D.dim, fontFamily = Mono, fontSize = 8.sp)
            Row(Modifier.fillMaxWidth().padding(top = 4.dp), horizontalArrangement = Arrangement.SpaceBetween) {
                Text("Estimates from live data. GT7 sends no tyre wear; fuel burn-off hides some pace loss.", Modifier.weight(1f), color = D.dim, fontFamily = Mono, fontSize = 7.sp)
                Text("[ NEW TYRES ]", Modifier.clickable { c.newTyres() }.padding(start = 6.dp, top = 4.dp, bottom = 4.dp), color = D.acc, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 8.sp)
            }
        }
        Frame("DYNAMICS") { Grid(4, 8.dp) {
            Cell("VEL X", f(p.d("vel_x"), 2)); Cell("VEL Y", f(p.d("vel_y"), 2)); Cell("VEL Z", f(p.d("vel_z"), 2)); Cell("ANG X", f(p.d("ang_x"), 3))
            Cell("ANG Y", f(p.d("ang_y"), 3)); Cell("ANG Z", f(p.d("ang_z"), 3)); Cell("HEADING", f(Math.toDegrees(p.d("heading")), 1) + "deg")
        } }
        Frame("POSITION") { Grid(4, 8.dp) {
            Cell("WORLD X", f(p.d("world_x"), 1)); Cell("WORLD Y", f(p.d("world_y"), 1)); Cell("WORLD Z", f(p.d("world_z"), 1)); Cell("TRACK POS", f(p.d("track_position"), 1) + " m")
        } }
        Frame("DRIVETRAIN EXTRAS") { Grid(3, 8.dp) {
            Cell("STEERING", sf(p.d("steering"), 2)); Cell("CLUTCH ENG", f(p.d("clutch_engaged"), 2))
            Cell("RPM WARN", p.i("rpm_warning").toString()); Cell("RPM LIM", p.i("rpm_limiter").toString()); Cell("RPM@CLUTCH", f(p.d("rpm_after_clutch"), 0))
        } }
        Text("GEAR RATIOS: " + (gearRatios.withIndex().filter { it.value > 0 }.joinToString("  ") { "G${it.index + 1}:" + f(it.value, 3) }.ifEmpty { "--" }),
            Modifier.padding(horizontal = 4.dp), color = D.dim, fontFamily = Mono, fontSize = 8.sp)
        Frame("EXTENDED (B/~/C)") {
            fun opt(k: String, fmt: (Double) -> String) = (p[k] as? Number)?.let { fmt(it.toDouble()) } ?: "--"
            Grid(4, 8.dp) {
                Cell("CUR LAP", (p["current_lap_ms"] as? Number)?.let { TraceUtil.msToLapTime(it.toInt()) } ?: "--")
                Cell("CATEGORY", (p["car_category"] as? String)?.ifEmpty { null } ?: "--")
                Cell("WHEELBASE", (p["wheel_base"] as? Number)?.toDouble()?.takeIf { it != 0.0 }?.let { f(it, 3) + "m" } ?: "--")
                Cell("SWAY", opt("sway") { sf(it, 3) }); Cell("HEAVE", opt("heave") { sf(it, 3) }); Cell("SURGE", opt("surge") { sf(it, 3) })
                Cell("ENERGY RECOV", opt("energy_recovery") { sf(it, 3) })
            }
        }
        Frame("SURFACE (per tyre)") { Grid(4, 8.dp) {
            val names = mapOf("T" to "Tarmac", "C" to "Curb", "D" to "Dirt", "G" to "Grass", "S" to "Sand", "s" to "Snow")
            for ((i, n) in listOf("FL", "FR", "RL", "RR").withIndex()) {
                val code = surf?.getOrNull(i)?.ifEmpty { null }
                Cell(n, names[code] ?: code ?: "--", big = true, color = if (code == "T") D.good else if (code != null) D.amb else D.fg)
            }
        } }
        Frame("WHEEL STEERING ANGLE") { Grid(2, 8.dp) {
            Cell("FRONT L", wsa?.let { sf(Math.toDegrees(it[0]), 1) + "deg" } ?: "--"); Cell("FRONT R", wsa?.let { sf(Math.toDegrees(it[1]), 1) + "deg" } ?: "--")
        } }
        Frame("SESSION / MISC") { Grid(3, 8.dp) {
            val tod = p.i("time_of_day")
            Cell("TIME OF DAY", "%02d:%02d:%02d".format(Locale.ROOT, (tod / 3600000) % 24, (tod % 3600000) / 60000, (tod % 60000) / 1000))
            Cell("IS EV", if (isEv) "YES" else "no", color = if (isEv) D.good else D.fg); Cell("CAR ID", p.i("car_id").toString())
            Cell("FLAG 8E", "0x%02X".format(p.i("flags_8e"))); Cell("FLAG 8F", "0x%02X".format(p.i("flags_8f"))); Cell("FLAG 93", "0x%02X".format(p.i("flags_93")))
        } }

        Row(Modifier.padding(top = 6.dp), horizontalArrangement = Arrangement.spacedBy(6.dp)) {
            if (online && hasHot) Alert("TYRE HOT", if (c.flashOn) D.red else Color(0xFF6a1010), Color.White, true)
            if (online && hasCold) Alert("TYRE COLD", if (c.flashOn) D.cold else Color(0xFF0a3040), D.hi, true)
            if (online && hasFuel) Alert("FUEL LOW", if (c.flashOn) D.amb else Color(0xFF6a3a00), Color.Black, true)
        }

        Section("TRACK MAP")
        TrackMap(c.trackPts)
        Section("LAP HISTORY")
        LapHistory(c)
        Section("LOG")
        Column(Modifier.fillMaxWidth().heightIn(min = 96.dp).background(D.map).padding(8.dp)) {
            c.log.takeLast(12).forEach { Text(it, color = D.log, fontFamily = Mono, fontSize = 8.sp, lineHeight = 12.sp) }
        }
        Spacer(Modifier.height(16.dp))
    }

    if (showSummary) SummaryDialog(c) { showSummary = false }
    c.promptTick.let { }
    val pend = c.recorder.pendingIncomplete
    if (pend != null) AlertDialog(onDismissRequest = {}, containerColor = D.pnl,
        title = { Text("Incomplete lap", color = D.amb, fontFamily = Mono) },
        text = { Text("Incomplete lap (${pend.size} samples) -- save or discard?", color = D.fg, fontFamily = Mono, fontSize = 13.sp) },
        confirmButton = { Text("Save", Modifier.clickable { c.resolveIncomplete(true) }.padding(12.dp), color = D.amb, fontFamily = Mono, fontWeight = FontWeight.Bold) },
        dismissButton = { Text("Discard", Modifier.clickable { c.resolveIncomplete(false) }.padding(12.dp), color = D.dim, fontFamily = Mono) })
}

@Composable
private fun TrackMap(pts: List<Pair<Double, Double>>) {
    Box(Modifier.fillMaxWidth().height(180.dp).background(D.map).border(1.dp, D.dim)) {
        if (pts.size < 2) { Text("WAITING FOR DATA", Modifier.align(Alignment.Center), color = Color(0xFF333344), fontFamily = Mono, fontSize = 9.sp); return@Box }
        Canvas(Modifier.fillMaxSize()) {
            val pad = 14.dp.toPx(); val w = size.width; val h = size.height
            val minX = pts.minOf { it.first }; val maxX = pts.maxOf { it.first }; val minZ = pts.minOf { it.second }; val maxZ = pts.maxOf { it.second }
            val rx = (maxX - minX).takeIf { it != 0.0 } ?: 1.0; val rz = (maxZ - minZ).takeIf { it != 0.0 } ?: 1.0
            val scale = minOf((w - 2 * pad) / rx, (h - 2 * pad) / rz).toFloat()
            val ox = (w - rx.toFloat() * scale) / 2; val oy = (h - rz.toFloat() * scale) / 2
            fun cv(p: Pair<Double, Double>) = Offset(ox + (p.first - minX).toFloat() * scale, oy + (p.second - minZ).toFloat() * scale)
            val step = maxOf(1, pts.size / 400)
            val full = Path(); pts.filterIndexed { i, _ -> i % step == 0 }.forEachIndexed { i, p -> val o = cv(p); if (i == 0) full.moveTo(o.x, o.y) else full.lineTo(o.x, o.y) }
            drawPath(full, Color(0xFF1a3a5a), style = Stroke(2.dp.toPx()))
            val rec = Path(); pts.takeLast(80).forEachIndexed { i, p -> val o = cv(p); if (i == 0) rec.moveTo(o.x, o.y) else rec.lineTo(o.x, o.y) }
            drawPath(rec, D.hi, style = Stroke(2.dp.toPx()))
            val last = cv(pts.last())
            drawCircle(Color.White, 6.dp.toPx(), last); drawCircle(D.acc, 5.dp.toPx(), last)
        }
    }
}

@Composable
private fun LapHistory(c: LiveController) {
    Column(Modifier.fillMaxWidth().background(D.map)) {
        Row(Modifier.fillMaxWidth().background(D.pnl).padding(6.dp)) {
            for ((t, w) in listOf("#" to .1f, "Time" to .35f, "Track" to .28f, "Car" to .27f)) Text(t, Modifier.weight(w), color = D.acc, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 9.sp)
        }
        c.history.take(6).forEachIndexed { idx, m ->
            var ts = TraceUtil.msToLapTime((m.lapTimeS * 1000).toInt()); if (m.incomplete) ts = "($ts)"; if (m.isPb) ts = "🏆$ts"
            val col = if (m.isPb) Color(0xFFffd700) else if (idx == 0) D.hi else Color(0xFFc0c0e0)
            Row(Modifier.fillMaxWidth().padding(6.dp)) {
                Text((if (m.lapNum > 0) m.lapNum else c.history.size - idx).toString(), Modifier.weight(.1f), color = col, fontFamily = Mono, fontSize = 8.sp)
                Text(ts, Modifier.weight(.35f), color = col, fontFamily = Mono, fontSize = 8.sp, maxLines = 1)
                Text(m.track.take(9), Modifier.weight(.28f), color = col, fontFamily = Mono, fontSize = 8.sp, maxLines = 1)
                Text(m.car.take(9), Modifier.weight(.27f), color = col, fontFamily = Mono, fontSize = 8.sp, maxLines = 1)
            }
        }
    }
}

@Composable
private fun SummaryDialog(c: LiveController, onDismiss: () -> Unit) {
    AlertDialog(onDismissRequest = onDismiss, confirmButton = { Text("Close", Modifier.clickable(onClick = onDismiss).padding(12.dp), color = D.acc, fontFamily = Mono) },
        containerColor = D.bg, title = { Text("Session Summary", color = D.acc, fontFamily = Mono) },
        text = {
            Column {
                if (c.recorder.sessionSummary.isEmpty()) Text("No races recorded this session yet.", color = D.fg, fontFamily = Mono, fontSize = 12.sp)
                c.recorder.sessionSummary.forEachIndexed { i, s ->
                    val g = if (s.isNull("grid_position")) "--" else "P" + s.getInt("grid_position")
                    Text("${i + 1}  ${s.getString("track")}  ${s.getString("car")}  ${s.getInt("laps")} laps  ${"%.1f".format(Locale.ROOT, s.getDouble("duration_s"))}s  ${"%.1f".format(Locale.ROOT, s.getDouble("fuel_used"))} ${s.getString("fuel_unit")}  $g",
                        color = D.fg, fontFamily = Mono, fontSize = 11.sp, modifier = Modifier.padding(vertical = 3.dp))
                }
            }
        })
}


@Composable private fun Section(t: String) = Text(t, Modifier.padding(start = 2.dp, top = 6.dp), color = D.acc, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 11.sp)

@Composable
private fun Alert(t: String, bg: Color, fg: Color, active: Boolean) =
    Text(t, Modifier.alpha(if (active) 1f else .28f).background(bg).padding(8.dp, 6.dp), color = fg, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 11.sp)

@Composable
private fun DBtn(text: String, m: Modifier = Modifier, bg: Color = D.tile, fg: Color = D.fg, onClick: () -> Unit = {}) =
    Box(m.height(44.dp).background(bg).clickable(onClick = onClick).padding(horizontal = 8.dp), contentAlignment = Alignment.Center) {
        Text(text, color = fg, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 12.sp, maxLines = 1)
    }

@Composable
private fun Picker(v: String, options: List<String>, onChange: (String) -> Unit, m: Modifier, color: Color) {
    var open by remember { mutableStateOf(false) }
    Box(m) {
        DInput(v, { onChange(it); open = it.isNotEmpty() && options.any { o -> o.contains(it, true) && !o.equals(it, true) } }, Modifier.fillMaxWidth(), color = color)
        DropdownMenu(open, { open = false }, Modifier.heightIn(max = 280.dp)) {
            options.filter { v.isEmpty() || it.contains(v, true) }.take(40).forEach { o -> DropdownMenuItem({ Text(o, fontFamily = Mono, fontSize = 12.sp) }, { onChange(o); open = false }) }
        }
    }
}

@Composable
private fun Field(label: String, content: @Composable () -> Unit) = Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
    Text(label, Modifier.width(78.dp), color = D.dim, fontFamily = Mono, fontSize = 10.sp)
    Box(Modifier.weight(1f)) { content() }
}

@Composable
private fun DInput(v: String, onChange: (String) -> Unit, m: Modifier, color: Color = D.hi, readOnly: Boolean = false) =
    BasicTextField(v, onChange, m.height(40.dp).background(D.tile).padding(horizontal = 8.dp), singleLine = true, readOnly = readOnly,
        textStyle = TextStyle(color = color, fontFamily = Mono, fontSize = 13.sp), cursorBrush = SolidColor(D.hi),
        decorationBox = { inner -> Box(Modifier.fillMaxHeight(), contentAlignment = Alignment.CenterStart) { inner() } })

@Composable
private fun Check(label: String, initial: Boolean, onChange: (Boolean) -> Unit = {}) {
    var on by remember { mutableStateOf(initial) }
    Row(Modifier.heightIn(min = 40.dp).clickable { on = !on; onChange(on) }, verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(8.dp)) {
        Checkbox(on, { on = it; onChange(it) }, colors = CheckboxDefaults.colors(checkedColor = D.acc, uncheckedColor = D.dim, checkmarkColor = Color.White))
        Text(label, color = D.dim, fontFamily = Mono, fontSize = 11.sp)
    }
}

@Composable
private fun Stat(label: String, v: String, color: Color = D.fg) = Column(horizontalAlignment = Alignment.CenterHorizontally) {
    Text(label, color = D.dim, fontFamily = Mono, fontSize = 10.sp)
    Text(v, color = color, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 14.sp, maxLines = 1)
}

@Composable
private fun Bar(label: String, frac: Double, color: Color, value: String) = Row(verticalAlignment = Alignment.CenterVertically, horizontalArrangement = Arrangement.spacedBy(6.dp)) {
    Text(label, Modifier.width(72.dp), color = D.dim, fontFamily = Mono, fontSize = 10.sp)
    Box(Modifier.weight(1f).height(18.dp).background(D.trough)) { Box(Modifier.fillMaxHeight().fillMaxWidth(frac.toFloat().coerceIn(0f, 1f)).background(color)) }
    Text(value, Modifier.width(88.dp), color = color, fontFamily = Mono, fontSize = 11.sp, textAlign = TextAlign.End, maxLines = 1)
}

@Composable
private fun Frame(title: String, content: @Composable ColumnScope.() -> Unit) = Box(Modifier.fillMaxWidth().padding(top = 6.dp)) {
    Column(Modifier.fillMaxWidth().border(1.dp, D.frame).padding(6.dp, 14.dp, 6.dp, 8.dp), content = content)
    Text(title, Modifier.padding(start = 10.dp).offset(y = (-8).dp).background(D.bg).padding(horizontal = 6.dp), color = D.acc, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 10.sp)
}

/** Equal-width grid: rows of `cols` children, each child a single layout node. */
@Composable
private fun Grid(cols: Int, gap: Dp, m: Modifier = Modifier, content: @Composable () -> Unit) {
    Layout(content, m) { measurables, constraints ->
        val hgap = 4.dp.roundToPx(); val vgap = gap.roundToPx()
        val w = constraints.maxWidth
        val cellW = (w - hgap * (cols - 1)) / cols
        val cc = Constraints.fixedWidth(cellW).copy(minHeight = 0, maxHeight = Constraints.Infinity)
        val placeables = measurables.map { it.measure(cc) }
        val rows = placeables.chunked(cols)
        val rowH = rows.map { r -> r.maxOf { it.height } }
        val h = rowH.sum() + vgap * (rows.size - 1).coerceAtLeast(0)
        layout(w, h) {
            var y = 0
            rows.forEachIndexed { ri, r ->
                r.forEachIndexed { ci, pl -> pl.placeRelative(ci * (cellW + hgap), y) }
                y += rowH[ri] + vgap
            }
        }
    }
}

@Composable
private fun Cell(name: String, value: String, big: Boolean = false, sub: String? = null, color: Color? = null, subColor: Color = D.dim) =
    Column(Modifier.fillMaxWidth(), horizontalAlignment = Alignment.CenterHorizontally) {
        Text(name, color = D.dim, fontFamily = Mono, fontSize = 9.sp, maxLines = 1, overflow = TextOverflow.Ellipsis)
        Text(value, color = color ?: D.fg, fontFamily = Mono, fontWeight = FontWeight.Bold,
            fontSize = if (big) 17.sp else 13.sp, maxLines = 1)
        if (sub != null) Text(sub, color = subColor, fontFamily = Mono, fontSize = 9.sp)
    }
