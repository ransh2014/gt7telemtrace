package app.trace

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight

/** Inconsolata (SIL OFL) stands in for Consolas, used by the PC apps. */
val Mono = FontFamily(
    Font(R.font.inconsolata_regular, FontWeight.Normal),
    Font(R.font.inconsolata_bold, FontWeight.Bold),
)

private fun c(v: Long) = Color(0xFF000000 or v)

/** Live Dashboard palette (hardcoded dark in dashboard.py). */
object D {
    val bg = c(0x0a0a12); val pnl = c(0x0f0f1e); val tile = c(0x16213e); val hdr = c(0x0f3460)
    val acc = c(0xe94560); val hi = c(0x4fc3f7); val fg = c(0xc0c0e0); val dim = c(0x555566)
    val good = c(0x2ecc71); val amb = c(0xf39c12); val red = c(0xe74c3c); val cold = c(0x1a6a8a)
    val map = c(0x050510); val trough = c(0xe6e6e6); val log = c(0x00ff88); val frame = c(0x3a3a4a)
}

/** Lap / Race Analyst + launcher palette. */
data class AP(
    val bg: Color, val pnl: Color, val pnl2: Color, val dim2: Color, val acc: Color, val cyn: Color,
    val grn: Color, val yel: Color, val org: Color, val prp: Color, val fg: Color, val dim: Color,
) {
    companion object {
        val Dark = AP(c(0x07080f), c(0x0d0e1a), c(0x13141f), c(0x232438), c(0xff2255), c(0x00f0d4),
            c(0x39ff85), c(0xffd500), c(0xff8c00), c(0xb06aff), c(0xc8d3f5), c(0x343856))
        val Light = AP(c(0xf4f5fa), c(0xffffff), c(0xeceef5), c(0xd7dae6), c(0xd1004a), c(0x00897a),
            c(0x1c8a45), c(0xa67d00), c(0xc0621a), c(0x7a3fd6), c(0x14162a), c(0x7a7f9a))
    }
}
