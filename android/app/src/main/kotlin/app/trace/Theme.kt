package app.trace

import androidx.compose.ui.graphics.Color
import androidx.compose.ui.text.font.Font
import androidx.compose.ui.text.font.FontFamily
import androidx.compose.ui.text.font.FontWeight

/** The TRACE website's fonts (all SIL OFL), bundled in res/font -- same families as the PC apps. */
val Mono = FontFamily(                                   // JetBrains Mono: data, labels
    Font(R.font.jetbrainsmono_regular, FontWeight.Normal),
    Font(R.font.jetbrainsmono_bold, FontWeight.Bold),
)
val Display = FontFamily(                                // Sora: titles
    Font(R.font.sora_regular, FontWeight.Normal),
    Font(R.font.sora_bold, FontWeight.Bold),
)
val Body = FontFamily(                                   // Manrope: prose
    Font(R.font.manrope_regular, FontWeight.Normal),
    Font(R.font.manrope_bold, FontWeight.Bold),
)
val Impact = FontFamily(Font(R.font.anton_regular, FontWeight.Normal))   // Anton: big numerals

private fun c(v: Long) = Color(0xFF000000 or v)

/** Live Dashboard palette -- the website's tokens (see theme.py on the PC side). */
object D {
    val bg = c(0x08080a); val pnl = c(0x0e0e11); val tile = c(0x202024); val hdr = c(0x17171b)
    val acc = c(0xe2455a); val hi = c(0x5fb3a8); val fg = c(0xf2f0ec); val dim = c(0x605e68)
    val good = c(0x3c9a5f); val amb = c(0xc9a24b); val red = c(0xe2455a); val cold = c(0x1c5c58)
    val map = c(0x050506); val trough = c(0x202024); val log = c(0x5fb3a8); val frame = c(0x2f2f31)
}

/** Lap / Race Analyst + launcher palette. */
data class AP(
    val bg: Color, val pnl: Color, val pnl2: Color, val dim2: Color, val acc: Color, val cyn: Color,
    val grn: Color, val yel: Color, val org: Color, val prp: Color, val fg: Color, val dim: Color,
) {
    companion object {
        val Dark = AP(c(0x08080a), c(0x0e0e11), c(0x17171b), c(0x202024), c(0xe2455a), c(0x5fb3a8),
            c(0x3c9a5f), c(0xc9a24b), c(0xc8102e), c(0x96949c), c(0xf2f0ec), c(0x605e68))
        val Light = AP(c(0xf4f5fa), c(0xffffff), c(0xeceef5), c(0xd7dae6), c(0xd1004a), c(0x00897a),
            c(0x1c8a45), c(0xa67d00), c(0xc0621a), c(0x7a3fd6), c(0x14162a), c(0x7a7f9a))
    }
}
