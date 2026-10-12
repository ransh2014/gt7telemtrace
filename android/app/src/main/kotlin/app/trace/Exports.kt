package app.trace

import android.content.Context
import android.graphics.Bitmap
import android.graphics.Typeface
import android.util.Base64
import androidx.core.content.res.ResourcesCompat
import app.trace.charts.Fig
import app.trace.charts.RTheme
import app.trace.charts.renderFigBitmap
import java.io.ByteArrayOutputStream
import java.io.File
import java.io.OutputStream
import java.util.zip.ZipEntry
import java.util.zip.ZipOutputStream

object Exports {
    /** Chart HTML export: the figure rendered to PNG, embedded base64 in a minimal dark page (as the PC apps do). Must run off the main thread. */
    fun chartHtml(ctx: Context, a: AP, f: Fig, title: String): String {
        val reg = ResourcesCompat.getFont(ctx, R.font.inconsolata_regular) ?: Typeface.MONOSPACE
        val bold = ResourcesCompat.getFont(ctx, R.font.inconsolata_bold) ?: Typeface.MONOSPACE
        val t = RTheme(a, 1.53f, reg, bold)
        val bmp = renderFigBitmap(f, t, 1320, f.cols)
        val bos = ByteArrayOutputStream()
        bmp.compress(Bitmap.CompressFormat.PNG, 100, bos)
        bmp.recycle()
        val b64 = Base64.encodeToString(bos.toByteArray(), Base64.NO_WRAP)
        val esc = title.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
        return "<!doctype html><html><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\">" +
            "<title>$esc</title><style>body{margin:0;background:#0b0b10;color:#c8d3f5;font-family:Consolas,monospace}" +
            "h1{font-size:14px;padding:10px 16px;margin:0}img{display:block;width:100%;max-width:1320px;margin:0 auto}</style></head>" +
            "<body><h1>$esc</h1><img alt=\"$esc\" src=\"data:image/png;base64,$b64\"></body></html>"
    }

    /** config.backup_laps: zip the laps folder (relative paths, skipping the backups folder). Returns the file count; 0 = nothing to back up. */
    fun backupLaps(folder: File, out: OutputStream): Int {
        val files = if (folder.isDirectory) folder.walkTopDown().filter { it.isFile && !it.relativeTo(folder).path.startsWith("backups") }.toList() else emptyList()
        if (files.isEmpty()) return 0
        ZipOutputStream(out).use { z ->
            for (f in files) {
                z.putNextEntry(ZipEntry(f.relativeTo(folder).path.replace(File.separatorChar, '/')))
                f.inputStream().use { it.copyTo(z) }
                z.closeEntry()
            }
        }
        return files.size
    }

    fun hasLaps(folder: File): Boolean = folder.isDirectory && folder.walkTopDown().any { it.isFile && !it.relativeTo(folder).path.startsWith("backups") }
}
