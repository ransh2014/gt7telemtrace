package app.trace.charts

import android.graphics.Bitmap
import android.graphics.Canvas as AndroidCanvas
import android.graphics.Typeface
import androidx.compose.foundation.Canvas
import androidx.compose.foundation.background
import androidx.compose.foundation.layout.*
import androidx.compose.material3.Text
import androidx.compose.runtime.Composable
import androidx.compose.runtime.remember
import androidx.compose.ui.Alignment
import androidx.compose.ui.Modifier
import androidx.compose.ui.graphics.drawscope.drawIntoCanvas
import androidx.compose.ui.graphics.nativeCanvas
import androidx.compose.ui.platform.LocalContext
import androidx.compose.ui.platform.LocalDensity
import androidx.compose.ui.unit.dp
import androidx.compose.ui.unit.sp
import androidx.core.content.res.ResourcesCompat
import app.trace.AP
import app.trace.Mono
import app.trace.R

@Composable
fun rememberRTheme(a: AP): RTheme {
    val ctx = LocalContext.current
    val dens = LocalDensity.current.density
    return remember(a, dens) {
        val reg = ResourcesCompat.getFont(ctx, R.font.inconsolata_regular) ?: Typeface.MONOSPACE
        val bold = ResourcesCompat.getFont(ctx, R.font.inconsolata_bold) ?: Typeface.MONOSPACE
        RTheme(a, dens, reg, bold)
    }
}

@Composable
fun PanelView(p: Panel, t: RTheme, modifier: Modifier = Modifier) {
    BoxWithConstraints(modifier.fillMaxWidth()) {
        val dn = LocalDensity.current
        val wPx = with(dn) { maxWidth.toPx() }
        val hPx = PlotRender.heightFor(p, wPx, t)
        val hDp = with(dn) { hPx.toDp() }
        Canvas(Modifier.fillMaxWidth().height(hDp)) {
            drawIntoCanvas { c -> PlotRender.draw(c.nativeCanvas, p, size.width, size.height, t) }
        }
    }
}

/** One matplotlib-style figure: panels on a grid (1 column on phones, the PC grid on wide screens). */
@Composable
fun FigView(f: Fig, a: AP, modifier: Modifier = Modifier) {
    val t = rememberRTheme(a)
    BoxWithConstraints(modifier.fillMaxWidth()) {
        val cols = when {
            maxWidth < 560.dp -> 1
            maxWidth < 900.dp -> minOf(2, f.cols)
            else -> f.cols
        }
        Column(Modifier.fillMaxWidth().padding(horizontal = 6.dp), verticalArrangement = Arrangement.spacedBy(8.dp)) {
            if (f.suptitle != null) Text(f.suptitle, Modifier.fillMaxWidth().padding(top = 6.dp), color = a.fg, fontFamily = Mono, fontSize = 14.sp, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
            for (row in f.panels.chunked(cols)) {
                Row(Modifier.fillMaxWidth(), horizontalArrangement = Arrangement.spacedBy(8.dp), verticalAlignment = Alignment.Top) {
                    for (p in row) Box(Modifier.weight(1f)) { PanelView(p, t) }
                    repeat(cols - row.size) { Spacer(Modifier.weight(1f)) }
                }
            }
            if (f.footer != null) Text(f.footer, Modifier.fillMaxWidth().padding(bottom = 6.dp), color = a.dim, fontFamily = Mono, fontSize = 11.sp, textAlign = androidx.compose.ui.text.style.TextAlign.Center)
        }
    }
}

/** Renders a whole figure into one bitmap (for Chart HTML export). Grid is `cols` wide at [width] px. */
fun renderFigBitmap(f: Fig, t: RTheme, width: Int, cols: Int = f.cols): Bitmap {
    val gap = (8 * t.dens)
    val cw = (width - gap * (cols + 1)) / cols
    val rows = f.panels.chunked(cols)
    val title = if (f.suptitle != null) t.f(11f) * 2f else 0f
    val heights = rows.map { r -> r.maxOf { PlotRender.heightFor(it, cw, t) } }
    val total = (gap + title + heights.sum() + gap * rows.size).toInt() + (if (f.footer != null) (t.f(8f) * 2).toInt() else 0)
    val bmp = Bitmap.createBitmap(width, total.coerceAtLeast(1), Bitmap.Config.ARGB_8888)
    val cv = AndroidCanvas(bmp)
    cv.drawColor(t.bg)
    var y = gap + title
    val paint = android.graphics.Paint(android.graphics.Paint.ANTI_ALIAS_FLAG).apply { typeface = t.mono; color = t.fg; textAlign = android.graphics.Paint.Align.CENTER }
    if (f.suptitle != null) { paint.textSize = t.f(11f); cv.drawText(f.suptitle, width / 2f, gap + paint.textSize, paint) }
    for ((ri, r) in rows.withIndex()) {
        for ((ci, p) in r.withIndex()) {
            val x = gap + ci * (cw + gap)
            cv.save(); cv.translate(x, y); cv.clipRect(0f, 0f, cw, heights[ri])
            PlotRender.draw(cv, p, cw, PlotRender.heightFor(p, cw, t), t)
            cv.restore()
        }
        y += heights[ri] + gap
    }
    if (f.footer != null) { paint.textSize = t.f(7f); paint.color = t.dim; cv.drawText(f.footer, width / 2f, total - paint.textSize * 0.6f, paint) }
    return bmp
}
