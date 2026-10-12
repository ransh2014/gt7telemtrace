package app.trace

import androidx.compose.foundation.background
import androidx.compose.foundation.border
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.*
import androidx.compose.foundation.lazy.LazyColumn
import androidx.compose.foundation.lazy.items
import androidx.compose.foundation.text.BasicTextField
import androidx.compose.material3.Text
import androidx.compose.runtime.*
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.graphics.SolidColor
import androidx.compose.ui.text.TextStyle
import androidx.compose.ui.text.font.FontWeight
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import app.trace.telemetry.LapIndex

@Composable
fun TBtn(a: AP, text: String, modifier: Modifier = Modifier, on: Boolean = false, color: Color = a.fg, onClick: () -> Unit) {
    Box(modifier.height(40.dp).background(if (on) a.acc else a.pnl2).clickable(onClick = onClick).padding(horizontal = 10.dp), contentAlignment = Alignment.Center) {
        Text(text, color = if (on) Color.White else color, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 12.sp, maxLines = 1)
    }
}

@Composable
fun Card(a: AP, title: String, modifier: Modifier = Modifier, accent: Color = a.cyn, content: @Composable ColumnScope.() -> Unit) {
    Column(modifier.fillMaxWidth().background(a.pnl).padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
        Text(title, color = accent, fontFamily = Mono, fontWeight = FontWeight.Bold, fontSize = 12.sp)
        content()
    }
}

@Composable
fun Lbl(a: AP, text: String, color: Color = a.fg, size: Int = 12, bold: Boolean = false, modifier: Modifier = Modifier) =
    Text(text, modifier, color = color, fontFamily = Mono, fontWeight = if (bold) FontWeight.Bold else FontWeight.Normal, fontSize = size.sp)

@Composable
fun Field(a: AP, value: String, hint: String, modifier: Modifier = Modifier, onChange: (String) -> Unit) {
    Box(modifier.height(40.dp).background(a.pnl2).border(1.dp, a.dim2).padding(horizontal = 8.dp), contentAlignment = Alignment.CenterStart) {
        if (value.isEmpty()) Text(hint, color = a.dim, fontFamily = Mono, fontSize = 12.sp)
        BasicTextField(value, onChange, singleLine = true, textStyle = TextStyle(color = a.fg, fontFamily = Mono, fontSize = 13.sp), cursorBrush = SolidColor(a.cyn), modifier = Modifier.fillMaxWidth())
    }
}

/** Saved-lap search dialog: car / track / date filters over the lap index (newest first). */
@Composable
fun LapPicker(a: AP, rows: List<LapIndex.Row>, title: String, onPick: (LapIndex.Row) -> Unit, onDismiss: () -> Unit) {
    var car by remember { mutableStateOf("") }
    var track by remember { mutableStateOf("") }
    var date by remember { mutableStateOf("") }
    val shown = remember(rows, car, track, date) { LapIndex.filter(rows, car, track, date) }
    Dialog(onDismissRequest = onDismiss) {
        Column(Modifier.fillMaxWidth().heightIn(max = 560.dp).background(a.bg).border(1.dp, a.dim2).padding(10.dp), verticalArrangement = Arrangement.spacedBy(6.dp)) {
            Lbl(a, title, a.cyn, 13, true)
            Row(horizontalArrangement = Arrangement.spacedBy(4.dp)) {
                Field(a, car, "car", Modifier.weight(1f)) { car = it }
                Field(a, track, "track", Modifier.weight(1f)) { track = it }
                Field(a, date, "date", Modifier.weight(0.7f)) { date = it }
            }
            Lbl(a, "${shown.size} laps", a.dim, 11)
            LazyColumn(Modifier.weight(1f, fill = false)) {
                items(shown) { r ->
                    Column(Modifier.fillMaxWidth().clickable { onPick(r) }.padding(vertical = 7.dp)) {
                        Lbl(a, "${r.date} | ${r.lapTime}", a.yel, 12, true)
                        Lbl(a, "${r.car} | ${r.track}", a.fg, 11)
                    }
                    Box(Modifier.fillMaxWidth().height(1.dp).background(a.dim2))
                }
            }
            TBtn(a, "Close", Modifier.fillMaxWidth(), onClick = onDismiss)
        }
    }
}
