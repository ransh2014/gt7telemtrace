package app.trace

import androidx.compose.foundation.background
import androidx.compose.foundation.clickable
import androidx.compose.foundation.layout.Arrangement
import androidx.compose.foundation.layout.Box
import androidx.compose.foundation.layout.Column
import androidx.compose.foundation.layout.Row
import androidx.compose.foundation.layout.Spacer
import androidx.compose.foundation.layout.height
import androidx.compose.foundation.layout.padding
import androidx.compose.foundation.layout.size
import androidx.compose.foundation.shape.CircleShape
import androidx.compose.foundation.shape.RoundedCornerShape
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.LaunchedEffect
import androidx.compose.runtime.getValue
import androidx.compose.runtime.mutableStateOf
import androidx.compose.runtime.remember
import androidx.compose.runtime.setValue
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.draw.clip
import androidx.compose.ui.graphics.Color
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.compose.ui.window.Dialog
import kotlinx.coroutines.delay

/**
 * Easter egg: long-press the TRACE title on the launcher. Five red lights come on one by one, then go out:
 * LIGHTS OUT. Tap to skip. Purely cosmetic; nothing is read, stored or sent.
 */
@Composable
fun LightsOut(a: AP, onDone: () -> Unit) {
    var lit by remember { mutableStateOf(0) }
    var go by remember { mutableStateOf(false) }
    LaunchedEffect(Unit) {
        delay(400)
        for (i in 1..5) {
            lit = i
            delay(420)
        }
        delay(900)
        lit = 0
        go = true
        delay(1000)
        onDone()
    }
    Dialog(onDismissRequest = onDone) {
        Column(
            Modifier.clip(RoundedCornerShape(12.dp)).background(a.pnl).clickable(onClick = onDone).padding(24.dp),
            horizontalAlignment = Alignment.CenterHorizontally,
        ) {
            Row(horizontalArrangement = Arrangement.spacedBy(10.dp)) {
                for (i in 1..5) {
                    Box(
                        Modifier.size(40.dp).clip(CircleShape)
                            .background(if (i <= lit) Color(0xFFc8102e) else Color(0xFF1a1a1d)),
                    )
                }
            }
            Spacer(Modifier.height(18.dp))
            Text(if (go) "LIGHTS OUT" else " ", color = Color(0xFF3c9a5f), fontFamily = Impact, fontSize = 28.sp)
            Text("tap to skip", color = a.dim, fontFamily = Mono, fontSize = 11.sp)
        }
    }
}
